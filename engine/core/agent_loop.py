"""
engine/core/agent_loop.py

Main orchestration loop: SENSE → DECIDE → ACT → OBSERVE → repeat.

Usage:
    async with PlaywrightMCPClient().session() as mcp:
        result = await AgentLoop(mcp).run("Go to example.com and read the title")
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import structlog

from engine.core.config import settings
from engine.core.state import (
    AgentResult,
    JevDecision,
    PageState,
    ResultStatus,
    StepRecord,
    TaskState,
    ToolAction,
)
from engine.jev.decision_engine import JevDecisionEngine
from engine.jev.question_sets import CORE_TOOL_SELECTION
from engine.memory.context_builder import ContextBuilder
from engine.mcp.playwright_tools import PlaywrightMCPClient
from engine.reporting.event_log import EventLog
from engine.router.router import Router

log = structlog.get_logger(__name__)


class AgentLoop:
    """
    Drives the sense→decide→act cycle until the task is done,
    max steps are reached, or an unrecoverable error occurs.
    """

    def __init__(
        self,
        mcp: PlaywrightMCPClient,
        *,
        question_set: dict[str, Any] | None = None,
        jev: JevDecisionEngine | None = None,
        max_steps: int | None = None,
        step_timeout: int | None = None,
        router: Any = None,
    ) -> None:
        self.mcp = mcp
        self.jev = jev or JevDecisionEngine()
        self.router = router or Router(
            mcp=mcp,
            jev=self.jev,
            question_set=question_set or CORE_TOOL_SELECTION,
        )
        self.context_builder = ContextBuilder()
        self.event_log = EventLog()
        self.max_steps = max_steps or settings.max_steps_per_task
        self.step_timeout = step_timeout or settings.step_timeout_seconds

    async def run(
        self,
        goal: str | TaskState,
        start_url: str = "",
        initial_params: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> AgentResult:
        """
        Execute the full agent loop for a given goal.

        Args:
            goal:           Natural-language task description OR an existing TaskState.
            start_url:      Optional URL to navigate to before the loop starts.
            initial_params: Optional seed params for the first tool call.
            task_id:        Optional run ID for tracing; auto-generated if absent.
        """
        if isinstance(goal, TaskState):
            state = goal
            task_id = state.task_id
            goal_str = state.goal
            if initial_params:
                state.pending_params.update(initial_params)
        else:
            task_id = task_id or str(uuid.uuid4())[:8]
            goal_str = goal
            state = TaskState(task_id=task_id, goal=goal_str, pending_params=initial_params or {})

        log.info("agent_loop.start", task_id=task_id, goal=goal_str, max_steps=self.max_steps)

        if start_url:
            state.pending_params["url"] = start_url
            await self.mcp.navigate(url=start_url)
            await self._refresh_page_state(state)

        for step_index in range(self.max_steps):
            decision: JevDecision | None = None
            tool_result: dict[str, Any] = {}
            error: str | None = None
            t0 = time.perf_counter()

            try:
                context = self.context_builder.build(state)
                decision, tool_result = await asyncio.wait_for(
                    self.router.route(state, context),
                    timeout=self.step_timeout,
                )
            except asyncio.TimeoutError:
                error = f"Step {step_index} timed out after {self.step_timeout}s"
                log.error("agent_loop.step.timeout", step=step_index)
                decision = decision or JevDecisionEngine.safe_fallback()
                tool_result = {"status": "timeout"}
            except Exception as exc:
                error = str(exc)
                log.error("agent_loop.step.error", step=step_index, error=error, exc_info=True)
                decision = decision or JevDecisionEngine.safe_fallback()
                tool_result = {"status": "error", "error": error}

            duration_ms = (time.perf_counter() - t0) * 1000

            # OBSERVE: refresh page state after a successful action.
            # Fix 1: If the action WAS a snapshot (or returned a pre-fetched
            # stable snapshot), reuse its result directly — no second MCP call.
            new_page_state: PageState | None = None
            if tool_result.get("status") not in ("aborted", "done", "timeout"):
                if (
                    decision
                    and decision.tool_action == ToolAction.SNAPSHOT
                    and tool_result.get("type") in ("text", "raw")
                ):
                    # Result is already the accessibility tree — parse it directly
                    new_page_state = _page_state_from_snapshot(tool_result, state)
                elif tool_result.get("_is_snapshot"):
                    # Router marked this as a pre-fetched stable snapshot
                    new_page_state = _page_state_from_snapshot(tool_result, state)
                else:
                    new_page_state = await self._refresh_page_state(state)
                if decision and decision.needs_visual:
                    shot = await self.mcp.screenshot()
                    if new_page_state and shot.get("type") == "image":
                        new_page_state.screenshot_b64 = shot["content"]

            record = StepRecord(
                step_index=step_index,
                context_snapshot=self.context_builder.build(state)[:500],
                decision=decision,
                tool_result=tool_result,
                page_state_after=new_page_state,
                error=error,
                duration_ms=duration_ms,
            )
            state.record_step(record)
            self.event_log.record(record)

            log.info(
                "agent_loop.step.done",
                step=step_index,
                action=decision.tool_action.value,
                status=tool_result.get("status", "ok"),
                duration_ms=round(duration_ms, 1),
            )

            # Terminal conditions
            if tool_result.get("status") == "done" or decision.tool_action == ToolAction.DONE:
                log.info("agent_loop.complete", task_id=task_id, steps=step_index + 1)
                return AgentResult.success(state, output=state.scratchpad)

            if tool_result.get("status") == "aborted":
                reason = tool_result.get("reason", "unknown")
                log.warning("agent_loop.aborted", task_id=task_id, reason=reason)
                return AgentResult.aborted(reason, state)

            # Reset per-step params so the router/task fills them fresh next step
            state.pending_params = {}

        log.warning("agent_loop.max_steps", task_id=task_id, steps=self.max_steps)
        return AgentResult.failure(f"Task did not complete within {self.max_steps} steps", state)

    async def _refresh_page_state(self, state: TaskState) -> PageState:
        """Capture a fresh accessibility snapshot and update state."""
        try:
            snapshot = await self.mcp.snapshot()
            page = _page_state_from_snapshot(snapshot, state)
            state.current_page = page
            return page
        except Exception as exc:
            log.warning("agent_loop.snapshot.failed", error=str(exc))
            return state.current_page


def _page_state_from_snapshot(result: dict[str, Any], state: "TaskState") -> PageState:
    """
    Parse a raw MCP snapshot result into a PageState without an extra MCP call.

    Used by agent_loop to reuse the result of a SNAPSHOT action or a
    _wait_for_stable_snapshot() call that mutation tools (navigate, click, …)
    already return.
    """
    content: str = result.get("content", "")
    url = state.current_page.url
    title = state.current_page.title
    for line in content.splitlines()[:10]:
        if "page url:" in line.lower():
            url = line.split(":", 1)[-1].strip()
        elif "page title:" in line.lower():
            title = line.split(":", 1)[-1].strip()
    return PageState(url=url, title=title, accessibility_tree=content)
