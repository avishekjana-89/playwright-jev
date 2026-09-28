"""
engine/core/state.py

Pydantic models that flow through the agent loop:
  PageState → StepRecord → TaskState → AgentResult
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Page state ────────────────────────────────────────────────────────────────

class PageState(BaseModel):
    """Snapshot of the browser page at a given moment."""
    url: str = ""
    title: str = ""
    accessibility_tree: str = ""
    screenshot_b64: str | None = None  # only when needs_visual=True
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_context_string(self, max_chars: int = 6000) -> str:
        """Serialise to a compact string for Jev's `state` input."""
        return "\n".join([
            f"URL: {self.url}",
            f"Title: {self.title}",
            "--- Accessibility Tree ---",
            self.accessibility_tree[:max_chars],
        ])


# ── Tool actions ──────────────────────────────────────────────────────────────

class ToolAction(str, Enum):
    NAVIGATE         = "navigate"
    NAVIGATE_BACK    = "navigate_back"
    SNAPSHOT         = "snapshot"
    CLICK            = "click"
    FILL             = "fill"
    SELECT           = "select"
    HOVER            = "hover"
    PRESS            = "press"
    DONE             = "done"
    ABORTED          = "aborted"


# ── Jev decision ──────────────────────────────────────────────────────────────

class JevDecision(BaseModel):
    """Structured output from a single Jev evaluation."""
    tool_action: ToolAction
    needs_visual: bool
    complexity: str          # "simple" | "moderate" | "complex"
    raw_probabilities: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0


# ── Step record ───────────────────────────────────────────────────────────────

class StepRecord(BaseModel):
    """Immutable record of one sense→decide→act cycle."""
    step_index: int
    context_snapshot: str
    decision: JevDecision
    tool_result: dict[str, Any] = Field(default_factory=dict)
    page_state_after: PageState | None = None
    error: str | None = None
    duration_ms: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── Task state ────────────────────────────────────────────────────────────────

class TaskState(BaseModel):
    """Mutable state carried through the full agent loop for one task."""
    task_id: str
    goal: str
    variables: dict[str, str] = Field(default_factory=dict)
    current_page: PageState = Field(default_factory=PageState)
    history: list[StepRecord] = Field(default_factory=list)
    pending_params: dict[str, Any] = Field(default_factory=dict)
    scratchpad: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def record_step(self, record: StepRecord) -> None:
        self.history.append(record)
        if record.page_state_after:
            self.current_page = record.page_state_after

    @property
    def step_count(self) -> int:
        return len(self.history)

    @property
    def last_action(self) -> ToolAction | None:
        return self.history[-1].decision.tool_action if self.history else None


# ── Agent result ──────────────────────────────────────────────────────────────

class ResultStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    ABORTED = "aborted"


class AgentResult(BaseModel):
    """Final result returned by AgentLoop.run()."""
    status: ResultStatus
    task_id: str
    goal: str
    total_steps: int
    total_duration_ms: float
    output: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str | None = None
    history: list[StepRecord] = Field(default_factory=list)
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def success(cls, state: TaskState, output: dict[str, Any] | None = None) -> "AgentResult":
        return cls(
            status=ResultStatus.SUCCESS,
            task_id=state.task_id,
            goal=state.goal,
            total_steps=state.step_count,
            total_duration_ms=_elapsed_ms(state.started_at),
            output=output or {},
            history=state.history,
        )

    @classmethod
    def failure(cls, reason: str, state: TaskState) -> "AgentResult":
        return cls(
            status=ResultStatus.FAILURE,
            task_id=state.task_id,
            goal=state.goal,
            total_steps=state.step_count,
            total_duration_ms=_elapsed_ms(state.started_at),
            failure_reason=reason,
            history=state.history,
        )

    @classmethod
    def aborted(cls, reason: str, state: TaskState) -> "AgentResult":
        return cls(
            status=ResultStatus.ABORTED,
            task_id=state.task_id,
            goal=state.goal,
            total_steps=state.step_count,
            total_duration_ms=_elapsed_ms(state.started_at),
            failure_reason=reason,
            history=state.history,
        )


def _elapsed_ms(started_at: datetime) -> float:
    return (datetime.now(UTC) - started_at).total_seconds() * 1000
