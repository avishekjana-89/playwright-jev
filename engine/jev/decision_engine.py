"""
engine/jev/decision_engine.py

Wraps Typesafe AI's system_one() API for all agent decisions.

KEY DESIGN: Always batch ALL questions into ONE API call per step.
Never call Jev once per question — that defeats its performance advantage.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from engine.core.config import settings
from engine.core.state import JevDecision, ToolAction

log = structlog.get_logger(__name__)


class JevDecisionEngine:
    """
    Evaluates all decision questions for one step in a single Jev API call.

    Usage:
        engine = JevDecisionEngine()
        decision = await engine.decide(context_string, question_set)
    """

    def __init__(self) -> None:
        self._client = self._build_client()

    def _build_client(self) -> Any:
        try:
            from typesafe_sdk import TypeSafeClient
            kwargs: dict[str, Any] = {"api_key": settings.jev_api_key or None}
            if settings.jev_base_url:
                kwargs["base_url"] = settings.jev_base_url
                log.info("jev.client.openrouter", base_url=settings.jev_base_url)
            return TypeSafeClient(**kwargs)
        except ImportError:
            log.warning("typesafe_sdk not installed — using MockJevClient")
            return _MockJevClient()

    async def decide(self, context: str, questions: dict[str, Any], *, timeout_ms: int = 30000) -> JevDecision:
        """
        Single Jev call that evaluates ALL questions in parallel.

        Args:
            context:    Serialised page + task state string.
            questions:  A question set dict from engine.jev.question_sets.
            timeout_ms: Hard timeout before raising TimeoutError.
        """
        log.debug("jev.decide.start", context_length=len(context), num_questions=len(questions))
        t0 = time.perf_counter()

        # ── REQUEST payload ──────────────────────────────────────────────────
        log.debug(
            "jev.request",
            state=context,
            questions={k: repr(v) for k, v in questions.items()},
        )

        try:
            result = await asyncio.to_thread(self._client.system_one, state=context, questions=questions)
        except Exception as exc:
            log.error("jev.decide.error", error=str(exc))
            return self.safe_fallback()

        latency_ms = (time.perf_counter() - t0) * 1000

        # ── RESPONSE payload ─────────────────────────────────────────────────
        log.debug(
            "jev.response",
            latency_ms=round(latency_ms, 1),
            raw=_serialize_jev_result(result),
        )
        log.info("jev.decide.done", latency_ms=round(latency_ms, 1))
        return self._parse_result(result, latency_ms)

    async def decide_dynamic(self, context: str, questions: dict[str, Any], caller: str = "dynamic") -> Any:
        """
        Handles on-the-fly custom Jev questions (like picking an element or variable).
        Returns the raw result object from the Typesafe SDK.
        """
        log.debug(
            "jev.request",
            caller=caller,
            state=context,
            questions={k: repr(v) for k, v in questions.items()},
        )

        try:
            result = await asyncio.to_thread(
                self._client.system_one,
                state=context,
                questions=questions
            )
        except Exception as exc:
            log.error("jev.decide_dynamic.error", caller=caller, error=str(exc))
            raise

        log.debug(
            "jev.response",
            caller=caller,
            raw=_serialize_jev_result(result),
        )
        return result

    def _parse_result(self, result: Any, latency_ms: float) -> JevDecision:
        """Map raw Jev API result to a typed JevDecision."""
        raw: dict[str, Any] = {}

        # tool_action (Choice)
        tool_action_str = "snapshot"
        if hasattr(result, "choices") and "tool_action" in result.choices:
            c = result.choices["tool_action"]
            tool_action_str = c.choice
            raw["tool_action_probs"] = getattr(c, "probabilities", {})
        try:
            tool_action = ToolAction(tool_action_str)
        except ValueError:
            tool_action = ToolAction.SNAPSHOT

        # needs_visual (Noul)
        needs_visual = False
        if hasattr(result, "nouls") and "needs_visual" in result.nouls:
            prob = result.nouls["needs_visual"].noul
            raw["needs_visual_prob"] = prob
            needs_visual = prob > settings.jev_visual_threshold

        # complexity (Choice)
        complexity = "simple"
        if hasattr(result, "choices") and "complexity" in result.choices:
            complexity = result.choices["complexity"].choice
            raw["complexity_probs"] = getattr(result.choices["complexity"], "probabilities", {})

        return JevDecision(
            tool_action=tool_action,
            needs_visual=needs_visual,
            complexity=complexity,
            raw_probabilities=raw,
            latency_ms=latency_ms,
        )

    @staticmethod
    def safe_fallback() -> JevDecision:
        """
        Conservative fallback when Jev is unreachable.
        Takes a snapshot (read-only) so the loop can continue safely.
        """
        return JevDecision(
            tool_action=ToolAction.SNAPSHOT,
            needs_visual=False,
            complexity="simple",
            raw_probabilities={"fallback": True},
            latency_ms=0.0,
        )


# ── Jev result serialiser (shared by decision_engine + dynamic_router) ────────

def _serialize_jev_result(result: Any) -> dict[str, Any]:
    """
    Convert an opaque Jev SDK result object into a plain dict for debug logging.
    Handles the choices / nouls / scores structure without crashing on missing keys.
    """
    out: dict[str, Any] = {}

    if hasattr(result, "choices"):
        out["choices"] = {
            k: {
                "choice": getattr(v, "choice", None),
                "probabilities": getattr(v, "probabilities", {}),
            }
            for k, v in result.choices.items()
        }

    if hasattr(result, "nouls"):
        out["nouls"] = {
            k: {"noul": getattr(v, "noul", None)}
            for k, v in result.nouls.items()
        }

    if hasattr(result, "scores"):
        out["scores"] = {
            k: {"score": getattr(v, "score", None)}
            for k, v in result.scores.items()
        }

    return out


# ── Offline mock (used when typesafe_sdk is not installed) ────────────────────

class _MockJevClient:
    """Returns a deterministic mock result for local development."""

    def system_one(self, state: str, questions: dict[str, Any]) -> Any:  # noqa: ARG002
        log.warning("jev.mock.active", hint="Set TYPESAFE_API_KEY to use real Jev")
        return _MockJevResult()


class _MockJevResult:
    """Named mock result object returned by _MockJevClient — importable by tests."""

    def __init__(self) -> None:
        from types import SimpleNamespace
        self.choices = {
            "tool_action": SimpleNamespace(choice="snapshot", probabilities={}),
            "complexity":  SimpleNamespace(choice="simple",   probabilities={}),
        }
        self.nouls = {
            "needs_visual": SimpleNamespace(noul=0.2),
        }
        self.scores = {}
