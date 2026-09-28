"""
engine/jev/guardrails.py

Safety gate that runs before every tool dispatch.
Blocks actions that hit a denied URL pattern.
"""
from __future__ import annotations

import structlog

from engine.core.config import settings
from engine.core.state import JevDecision

log = structlog.get_logger(__name__)


class GuardrailGate:
    """
    Safety check on every JevDecision:
      - Static URL deny-list (never visit sensitive pages)
    """

    # URLs containing these substrings will always be blocked
    URL_DENY_LIST = [
        "bank", "payment", "checkout", "admin",
        "delete", "destroy", "reset-password",
    ]

    def __init__(
        self,
        extra_deny_list: list[str] | None = None,
    ) -> None:
        self.deny_list = self.URL_DENY_LIST + (extra_deny_list or [])

    def check(
        self,
        decision: JevDecision,
        *,
        current_url: str = "",
        step_index: int = 0,
    ) -> tuple[bool, str]:
        """Return (is_allowed, reason). Caller aborts the step if is_allowed=False."""

        # Static URL deny-list
        if current_url:
            url_lower = current_url.lower()
            for pattern in self.deny_list:
                if pattern in url_lower:
                    reason = f"Guardrail: URL contains denied pattern '{pattern}'"
                    log.warning("guardrail.blocked.url", url=current_url, pattern=pattern)
                    return False, reason

        log.debug("guardrail.passed", step=step_index, action=decision.tool_action)
        return True, "ok"
