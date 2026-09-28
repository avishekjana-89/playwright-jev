"""
engine/router/router.py

Routes each agent step:
  1. Jev evaluates ALL questions in one parallel call
  2. GuardrailGate checks safety — aborts if blocked
  3. Dispatch to the correct Playwright MCP tool
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from engine.core.state import JevDecision, TaskState, ToolAction
from engine.jev.decision_engine import JevDecisionEngine
from engine.jev.guardrails import GuardrailGate
from engine.jev.question_sets import CORE_TOOL_SELECTION
from engine.mcp.playwright_tools import PlaywrightMCPClient

log = structlog.get_logger(__name__)


class Router:
    """
    Jev (fast, parallel) → Guardrail → Playwright MCP tool dispatch.
    """

    def __init__(
        self,
        mcp: PlaywrightMCPClient,
        jev: JevDecisionEngine | None = None,
        guardrail: GuardrailGate | None = None,
        question_set: dict[str, Any] | None = None,
    ) -> None:
        self.mcp = mcp
        self.jev = jev or JevDecisionEngine()
        self.guardrail = guardrail or GuardrailGate()
        self.question_set = question_set or CORE_TOOL_SELECTION

    async def route(self, state: TaskState, context: str) -> tuple[JevDecision, dict[str, Any]]:
        """
        Execute one route step.

        Returns:
            (JevDecision, tool_result)
            tool_result["status"] == "aborted" means the guardrail blocked it.
            tool_result["status"] == "done"    means the task is complete.
        """
        # Step 1: Jev decides the next action (all questions in one call)
        decision = await self.jev.decide(context, self.question_set)
        log.info(
            "router.decision",
            step=state.step_count,
            action=decision.tool_action.value,
            complexity=decision.complexity,
            latency_ms=round(decision.latency_ms, 1),
        )

        # Step 2: Guardrail safety check
        allowed, reason = self.guardrail.check(
            decision, current_url=state.current_page.url, step_index=state.step_count
        )
        if not allowed:
            log.warning("router.blocked", reason=reason)
            return decision, {"status": "aborted", "reason": reason}

        # Step 3: Done signal
        if decision.tool_action == ToolAction.DONE:
            return decision, {"status": "done"}

        # Step 4: Dispatch to Playwright MCP
        result = await self._dispatch(decision, state)
        return decision, result

    async def _dispatch(self, decision: JevDecision, state: TaskState) -> dict[str, Any]:
        """Map ToolAction to the appropriate Playwright MCP call."""
        p = state.pending_params

        dispatch: dict[ToolAction, Callable[[], Awaitable[dict[str, Any]]]] = {
            ToolAction.NAVIGATE:      lambda: self.mcp.navigate(**p),
            ToolAction.NAVIGATE_BACK: lambda: self.mcp.navigate_back(),
            ToolAction.SNAPSHOT:      lambda: self.mcp.snapshot(),
            ToolAction.CLICK:         lambda: self.mcp.click(**p),
            ToolAction.FILL:          lambda: self.mcp.type_text(**p),
            ToolAction.SELECT:        lambda: self.mcp.select_option(**p),
            ToolAction.HOVER:         lambda: self.mcp.hover(**p),
            ToolAction.PRESS:         lambda: self.mcp.press_key(**p),
        }

        handler = dispatch.get(decision.tool_action)
        if handler is None:
            log.warning("router.unknown_action", action=decision.tool_action)
            return await self.mcp.snapshot()

        try:
            result: dict[str, Any] = await handler()
            return result
        except Exception as exc:
            log.error("router.dispatch.error", action=decision.tool_action.value, error=str(exc))
            return {"status": "error", "error": str(exc)}
