"""
engine/reporting/event_log.py

Structured event log capturing every agent decision and action.
Each step is recorded with full Jev decision probabilities for audit.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from engine.core.state import StepRecord

log = structlog.get_logger(__name__)


class EventLog:
    """
    Append-only structured log of agent steps.

    - In-memory buffer for the current run
    - Optional JSONL file sink for persistence
    """

    def __init__(self, log_dir: str | Path | None = None) -> None:
        self._events: list[dict[str, Any]] = []
        self._log_file: Path | None = None

        if log_dir:
            log_path = Path(log_dir)
            log_path.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            self._log_file = log_path / f"agent_run_{ts}.jsonl"
            log.info("event_log.file", path=str(self._log_file))

    def record(self, step: StepRecord) -> None:
        """Append a step record to the in-memory buffer (and file if configured)."""
        event: dict[str, Any] = {
            "ts": step.timestamp.isoformat(),
            "step": step.step_index,
            "action": step.decision.tool_action.value,
            "complexity": step.decision.complexity,
            "needs_visual": step.decision.needs_visual,
            "jev_latency_ms": step.decision.latency_ms,
            "raw_jev_probs": step.decision.raw_probabilities,
            "tool_result_status": step.tool_result.get("status", "ok"),
            "duration_ms": step.duration_ms,
            "error": step.error,
        }
        self._events.append(event)

        if self._log_file:
            with self._log_file.open("a") as f:
                f.write(json.dumps(event) + "\n")

        log.debug("event_log.recorded", step=step.step_index)

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def summary(self) -> dict[str, Any]:
        """Aggregate summary of the run for reporting."""
        if not self._events:
            return {}

        total = len(self._events)
        errors = sum(1 for e in self._events if e.get("error"))
        actions = {}
        for e in self._events:
            a = e["action"]
            actions[a] = actions.get(a, 0) + 1

        avg_jev_ms = (
            sum(e["jev_latency_ms"] for e in self._events) / total
            if total else 0
        )
        avg_step_ms = (
            sum(e["duration_ms"] for e in self._events) / total
            if total else 0
        )

        return {
            "total_steps": total,
            "errors": errors,
            "action_counts": actions,
            "avg_jev_latency_ms": round(avg_jev_ms, 1),
            "avg_step_duration_ms": round(avg_step_ms, 1),
        }
