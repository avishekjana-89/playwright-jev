"""
engine/jev/question_sets.py

Reusable, domain-specific Jev question bundles.
Import these as the `questions` arg to JevDecisionEngine.decide().
"""
from __future__ import annotations

from typing import Any

try:
    from typesafe_sdk import Choice, Noul, Score
except ImportError:
    # Stub types for development without API key
    class Choice:  # type: ignore[no-redef]
        def __init__(self, instructions: str = "", **kwargs: object) -> None:
            pass
    class Noul:  # type: ignore[no-redef]
        def __init__(self, instructions: str = "", **kwargs: object) -> None:
            pass
    class Score:  # type: ignore[no-redef]
        def __init__(self, instructions: str = "", **kwargs: object) -> None:
            pass


# ─── Core navigation/action question set (used by default AgentLoop) ──────────

CORE_TOOL_SELECTION: dict[str, Any] = {
    "tool_action": Choice(
        instructions=(
            "Based on the current page state and the overall task goal, "
            "which single browser action should the agent take next?\n"
            "CRITICAL RULE: BEFORE selecting 'done', you must review the provided Page Layout context to visually verify that your previous actions were successful and the goal is complete."
        ),
        criteria={
            "navigate":         "Go to URL",
            "navigate_back":    "Navigate to the previous page",
            "snapshot":         "Refresh/read the current page's accessibility tree to verify changes or understand its structure",
            "click":            "Click element",
            "fill":             "Enter text (or clear field by passing empty string)",
            "select":           "Select dropdown option",
            "hover":            "Mouse hover",
            "press":            "Keyboard key press",
            "done":             "The task goal has been fully achieved and verified based on the current context — stop the loop",
        },
    ),
    "needs_visual": Noul(
        instructions=(
            "Does completing this step require a visual screenshot to verify "
            "layout, images, or non-text content? Answer YES only if "
            "the accessibility tree alone is insufficient."
        )
    ),
    "complexity": Choice(
        instructions=(
            "How much multi-step reasoning is required to determine "
            "the correct next action from the current page state?"
        ),
        criteria={
            "simple":   "Clear, deterministic next step — no ambiguity",
            "moderate": "Requires interpreting some page content or conditional logic",
            "complex":  "Highly ambiguous UI, novel situation, or requires planning ahead",
        },
    ),
}


# ─── Web Scraping question set ────────────────────────────────────────────────

SCRAPING_QUESTIONS: dict[str, Any] = {
    **CORE_TOOL_SELECTION,
    "data_present": Noul(
        instructions="Is the target data the agent is trying to extract visible on the current page?"
    ),
    "needs_pagination": Noul(
        instructions=(
            "Is there a 'Next page', 'Load more', or pagination control that should be followed "
            "to retrieve additional data?"
        )
    ),
    "element_type": Choice(
        instructions="What type of HTML structure contains the primary data to extract?",
        criteria={
            "table":   "HTML <table> with rows and columns",
            "list":    "Ordered or unordered list items",
            "cards":   "Repeated card or tile components in a grid",
            "text":    "Free-form paragraph, article, or prose text",
            "dynamic": "Lazy-loaded, AJAX, or JavaScript-rendered content",
        },
    ),
    "requires_auth": Noul(
        instructions="Does accessing the target data require the user to be logged in or authenticated?"
    ),
}


# ─── Form Automation question set ────────────────────────────────────────────

FORM_AUTOMATION_QUESTIONS: dict[str, Any] = {
    **CORE_TOOL_SELECTION,
    "form_valid": Noul(
        instructions="Is the current form in a valid, fillable state with all required fields accessible?"
    ),
    "has_errors": Noul(
        instructions="Are there any visible validation error messages currently displayed on the form?"
    ),
    "submit_ready": Noul(
        instructions="Have all required fields been filled? Is the form ready to be submitted?"
    ),
    "field_type": Choice(
        instructions="What type of form field should be interacted with next?",
        criteria={
            "text":       "Text input, email, password, or textarea",
            "select":     "Dropdown select or combobox",
            "checkbox":   "Checkbox or toggle",
            "radio":      "Radio button group",
            "file":       "File upload input",
            "date":       "Date picker or date-time input",
            "submit":     "Submit or CTA button",
        },
    ),
}


# ─── Regression Testing question set ─────────────────────────────────────────

REGRESSION_TEST_QUESTIONS: dict[str, Any] = {
    **CORE_TOOL_SELECTION,
    "test_passing": Noul(
        instructions="Is the element or behaviour currently matching the expected baseline state?"
    ),
    "regression_detected": Noul(
        instructions="Is there a visible difference, error, or unexpected change compared to the baseline?"
    ),
    "severity": Choice(
        instructions="If a regression is detected, how severe is it?",
        criteria={
            "critical": "Core functionality is broken or unavailable",
            "major":    "Significant UX degradation or feature broken",
            "minor":    "Cosmetic issue, edge case, or non-blocking defect",
            "none":     "No regression detected — everything looks correct",
        },
    ),
}


# ─── Registry ────────────────────────────────────────────────────────────────

QUESTION_SET_REGISTRY: dict[str, dict[str, Any]] = {
    "core":        CORE_TOOL_SELECTION,
    "scraping":    SCRAPING_QUESTIONS,
    "form":        FORM_AUTOMATION_QUESTIONS,
    "regression":  REGRESSION_TEST_QUESTIONS,
}


def get_question_set(name: str) -> dict[str, Any]:
    """Look up a named question set. Raises KeyError if not found."""
    if name not in QUESTION_SET_REGISTRY:
        available = ", ".join(QUESTION_SET_REGISTRY.keys())
        raise KeyError(f"Unknown question set '{name}'. Available: {available}")
    return QUESTION_SET_REGISTRY[name]
