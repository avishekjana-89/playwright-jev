"""
engine/router/dynamic_router.py

A pure System 1 autonomous router.
It uses Jev dynamically to extract parameters by turning the page's
interactive elements into multiple-choice questions.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import structlog
from rich.console import Console
from rich.panel import Panel
from typesafe_sdk import Choice

from engine.core.config import settings
from engine.core.state import JevDecision, TaskState, ToolAction
from engine.core.tokenizer import truncate_to_tokens
from engine.jev.decision_engine import JevDecisionEngine, _serialize_jev_result
from engine.mcp.playwright_tools import PlaywrightMCPClient
from engine.router.router import Router

log = structlog.get_logger(__name__)
console = Console()

# Matches lines ending in `[number]` (e.g., `link "Log in" [32]`)
# Group 1: The element description (e.g. `link "Log in"`)
# Group 2: The ref ID (e.g. `e32`)
ELEMENT_REGEX = re.compile(r'^\s*-\s*(.*?)\s+\[ref=(.*?)\]')

class DynamicJevRouter(Router):
    """
    Achieves full autonomy without generative LLMs by using a 
    multi-step Jev classification pipeline.
    """
    def __init__(self, mcp: PlaywrightMCPClient, jev: JevDecisionEngine) -> None:
        super().__init__(mcp=mcp, jev=jev)

    async def route(self, state: TaskState, context: str) -> tuple[JevDecision, dict[str, Any]]:
        # STRIP NOISE FOR MAIN DECISION PROMPT
        # We don't need element IDs or properties (like [ref=e1], [cursor=pointer])
        # for deciding *what* to do, only for picking *which* element to interact with.
        lines = []
        for line in context.splitlines():
            if line.lstrip().startswith("- "):
                line = re.sub(r' \[.*?\]', '', line)
            lines.append(line)
        stripped_context = "\n".join(lines)

        decision = await self.jev.decide(stripped_context, self.question_set)
        action = decision.tool_action
        console.print(Panel(
            f"[bold cyan]Action Picked:[/bold cyan] {action.value}\n[dim]Context Size: {len(stripped_context)} chars (stripped)[/dim]",
            title="🧠 [bold magenta]JEV Main Decision:[/bold magenta] What to do next?",
            border_style="magenta",
            expand=False
        ))
        
        # 1. Action: DONE
        if action == ToolAction.DONE:
            return decision, await self.mcp.mark_done()
            
        # 2. Action: SNAPSHOT (wait/refresh)
        if action == ToolAction.SNAPSHOT:
            result = await self.mcp.snapshot()
            # Fix 3: Signal to agent_loop that this result IS the fresh snapshot
            # so it can skip the redundant _refresh_page_state() call.
            result["_is_snapshot"] = True
            return decision, result
            
        # 3. Action: NAVIGATE
        if action == ToolAction.NAVIGATE:
            if not state.variables:
                return decision, {"status": "error", "error": "No task variables provided to navigate."}
                
            variable_key = await self._ask_jev_to_pick_variable(state, purpose="navigate to")
            url = state.variables[variable_key]
            res = await self.mcp.navigate(url=url)
            res["content"] = f"Navigated to {url}"
            return decision, res
            
        if action == ToolAction.NAVIGATE_BACK:
            res = await self.mcp.navigate_back()
            res["content"] = "Navigated back to the previous page."
            return decision, res
            
        # 4. Other Non-Target Actions
        if action == ToolAction.PRESS:
            key = await self._ask_jev_to_pick_variable(state, purpose="press key")
            return decision, await self.mcp.press_key(key=state.variables.get(key, "Enter"))
            
        # 5. Actions requiring an element target
        target_actions = (
            ToolAction.CLICK, ToolAction.FILL, ToolAction.SELECT, 
            ToolAction.HOVER
        )
        if action in target_actions:
            elements = self._extract_interactive_elements(context)
            if not elements:
                console.print("[bold red]ERROR: No interactive elements found![/bold red]")
                console.print(f"Context snippet: {context[:500]}...")
                return decision, {"status": "error", "error": "No interactive elements found on the page."}
            
            target_ref = await self._ask_jev_to_pick_element(elements, action, state, context)
            
            if action == ToolAction.CLICK:
                res = await self.mcp.click(ref=target_ref)
                res["content"] = f"Clicked element {target_ref} ({elements.get(target_ref, '')})"
                return decision, res
            if action == ToolAction.FILL:
                if not state.variables:
                    return decision, {"status": "error", "error": "No task variables provided for typing."}
                variable_key = await self._ask_jev_to_pick_variable(state, purpose="type into the field", target_desc=elements.get(target_ref, ""))
                res = await self.mcp.type_text(ref=target_ref, text=state.variables[variable_key], submit=False)
                res["content"] = f"Filled element {target_ref} with variable '{variable_key}'"
                return decision, res
            if action == ToolAction.HOVER:
                res = await self.mcp.hover(ref=target_ref)
                res["content"] = f"Hovered over element {target_ref}"
                return decision, res
            if action == ToolAction.SELECT:
                val = await self._ask_jev_to_pick_variable(state, purpose="select value", target_desc=elements.get(target_ref, ""))
                res = await self.mcp.select_option(ref=target_ref, value=state.variables.get(val, ""))
                res["content"] = f"Selected option from {target_ref} using variable '{val}'"
                return decision, res
                
        return decision, {"status": "error", "error": f"Action '{action}' not fully supported by dynamic router yet."}
        
    def _extract_interactive_elements(self, context: str) -> dict[str, str]:
        """Parses the accessibility tree and returns a dict of {ref_id: description}."""
        elements = {}
        interactive_roles = {
            "link", "button", "textbox", "searchbox", "combobox", "checkbox", 
            "radio", "menuitem", "switch", "slider", "spinbutton", "tab", "option"
        }
        
        for line in context.splitlines():
            if not line.strip().startswith("-"):
                continue
                
            match = ELEMENT_REGEX.search(line)
            if match:
                raw_desc, ref_id = match.groups()
                parts = raw_desc.split(' ', 1)
                role = parts[0].lower()
                name = parts[1].strip(' "') if len(parts) > 1 else "Unlabeled"
                if role in interactive_roles:
                    if name == "Unlabeled":
                        continue
                    elements[ref_id] = f"Element {ref_id} ('{name}')"
                    
        return elements

    async def _ask_jev_to_pick_element(self, elements: dict[str, str], action: ToolAction, state: TaskState, context: str) -> str:
        """Dynamically creates a Jev question to pick an element."""
        # ── Cap to Jev API limit of 255 choices ───────────────────────────────
        # Rank elements by keyword overlap with the goal so the most relevant
        # candidates are kept when the page has more than 255 interactive elements.
        _MAX_CHOICES = 255
        if len(elements) > _MAX_CHOICES:
            goal_words = set(state.goal.lower().split())
            
            # Pre-compute token sets to avoid expensive string splitting during the sort loop
            element_tokens = {
                ref_id: set(desc.lower().split())
                for ref_id, desc in elements.items()
            }
            
            def _relevance(item: tuple[str, str]) -> int:
                return len(goal_words & element_tokens[item[0]])
                
            ranked = sorted(elements.items(), key=_relevance, reverse=True)
            elements = dict(ranked[:_MAX_CHOICES])
            log.debug(
                "dynamic_router.elements.capped",
                original=len(elements) + (len(ranked) - _MAX_CHOICES),
                kept=_MAX_CHOICES,
            )

        criteria = {ref_id: desc for ref_id, desc in elements.items()}

        instructions = (
            f"Goal: '{state.goal}'.\n"
            f"Carefully analyze the Page Layout context provided below to find the exact target. "
            f"WARNING: DO NOT just match keywords blindly! Cross-reference the elements with their structural position in the Page Layout to ensure you are selecting the actual target (e.g., a specific content item) rather than a generic navigational link or header that happens to share similar text.\n"
            f"CRITICAL RULE: Avoid selecting 'Unlabeled' elements if there is a named element (like a text link) for the exact same target right next to it. Unlabeled elements are often image wrappers that can be unclickable in automation.\n"
            f"Which element should we {action.value}?"
        )

        questions = {
            "target_element": Choice(
                instructions=instructions,
                criteria=criteria
            )
        }
        
        preview = "\n".join(f"• {c}" for c in list(criteria.values())[:3])
        if len(criteria) > 3:
            preview += f"\n• ... (and {len(criteria) - 3} more)"
            
        console.print(Panel(
            f"[bold blue]Question:[/bold blue] {questions['target_element'].instructions}\n\n[bold]Choices:[/bold]\n{preview}",
            title="🤖 [bold yellow]JEV Question:[/bold yellow] Pick an Element",
            border_style="yellow",
            expand=False
        ))
        
        numbered_context = "\n".join(f"{i+1:3d} | {line}" for i, line in enumerate(context.splitlines()))
        
        truncated_context, orig_len, trunc_len = truncate_to_tokens(numbered_context, settings.max_context_tokens)
        if orig_len > trunc_len:
            truncated_context += f"\n... [{orig_len - trunc_len:,} tokens omitted — focus on the interactive elements listed above]"
            
        state_str = f"Goal: {state.goal}\nAction: {action.value}\n\n--- Page Layout ---\n{truncated_context}"

        # ── REQUEST (element picker) ─────────────────────────────────────────
        # Pass the (truncated) context so Jev can see the structural layout
        decision = await self.jev.decide_dynamic(
            context=state_str,
            questions=questions,
            caller="pick_element"
        )

        # ── RESPONSE (element picker) ──────────────────────────────────────
        
        chosen_ref = str(decision.choices["target_element"].choice)
        console.print(Panel(
            f"[bold green]Answer:[/bold green] {chosen_ref}\n[dim]Description: {elements.get(chosen_ref)}[/dim]",
            title="🎯 [bold green]JEV Decision:[/bold green] Element Picked",
            border_style="green",
            expand=False
        ))
        return chosen_ref

    async def _ask_jev_to_pick_variable(self, state: TaskState, purpose: str = "type into the field", target_desc: str = "") -> str:
        """Dynamically creates a Jev question to pick a task variable."""
        criteria = {
            key: f"Variable '{key}' with value: '{value}'" 
            for key, value in state.variables.items()
        }
        
        if "navigate" in purpose.lower():
            instructions = (
                f"Goal: '{state.goal}'.\n"
                f"Carefully select the variable that contains the correct URL we should navigate to in order to achieve the goal."
            )
        else:
            target_info = f" into the element '{target_desc}'" if target_desc else f" for the purpose: {purpose}"
            instructions = (
                f"Goal: '{state.goal}'.\n"
                f"We are about to input text{target_info}. "
                f"Carefully evaluate the available variables and select the one that contains the correct data for this specific field."
            )
            
        questions = {
            "text_variable": Choice(
                instructions=instructions,
                criteria=criteria
            )
        }
        
        preview = "\n".join(f"• {k}: {v}" for k, v in state.variables.items())
        console.print(Panel(
            f"[bold blue]Question:[/bold blue] {instructions}\n\n[bold]Choices:[/bold]\n{preview}",
            title=f"🤖 [bold yellow]JEV Question:[/bold yellow] Pick a Variable for '{purpose}'",
            border_style="yellow",
            expand=False
        ))
        
        state_str = f"Goal: {state.goal}"

        # ── REQUEST (variable picker) ────────────────────────────────────────
        decision = await self.jev.decide_dynamic(
            context=state_str,
            questions=questions,
            caller="pick_variable"
        )

        # ── RESPONSE (variable picker) ──────────────────────────────────────
        
        chosen_key = str(decision.choices["text_variable"].choice)
        console.print(Panel(
            f"[bold green]Answer:[/bold green] {chosen_key}\n[dim]Value: {state.variables.get(chosen_key)}[/dim]",
            title="🎯 [bold green]JEV Decision:[/bold green] Variable Picked",
            border_style="green",
            expand=False
        ))
        return chosen_key
