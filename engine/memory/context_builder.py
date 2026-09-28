"""
engine/memory/context_builder.py

Assembles the `state` string fed into Jev's system_one() call.
Jev's intelligence is only as good as the context it receives — this is critical.

Context includes:
  - Current task goal
  - Current page URL, title, and accessibility tree (truncated)
  - Recent step history (last N actions + outcomes)
  - Task-specific scratchpad data
"""
from __future__ import annotations

from engine.core.state import TaskState, ToolAction



from engine.core.tokenizer import truncate_to_tokens
from engine.core.config import settings

# Cap on inline result snippets in the history section.
_MAX_RESULT_CHARS = 300


class ContextBuilder:
    """
    Serialises TaskState into a concise, token-efficient string for Jev.

    Keep context focused — Jev is optimised for structured signals,
    not long narratives. The accessibility tree is the most important signal.
    """

    def __init__(
        self,
        history_window: int = 5,
        max_tree_tokens: int | None = None,
        max_result_chars: int = _MAX_RESULT_CHARS,
    ) -> None:
        self.history_window = history_window
        self.max_tree_tokens = max_tree_tokens or settings.max_context_tokens
        self.max_result_chars = max_result_chars

    def build(self, state: TaskState) -> str:
        sections: list[str] = []

        # ── 1. Task goal ──────────────────────────────────────────────────────
        sections.append(f"TASK GOAL: {state.goal}")
        sections.append(f"Steps completed: {state.step_count}")

        # ── 2. Current page ───────────────────────────────────────────────────
        page = state.current_page
        sections.append("\nCURRENT PAGE:")
        sections.append(f"  URL:   {page.url or '(unknown)'}")
        sections.append(f"  Title: {page.title or '(unknown)'}")
        if page.accessibility_tree:
            tree = page.accessibility_tree
            truncated_tree, orig_len, trunc_len = truncate_to_tokens(tree, self.max_tree_tokens)
            if orig_len > trunc_len:
                truncated_tree += f"\n... [accessibility tree truncated — {orig_len - trunc_len:,} tokens omitted to stay within token limit]"
            sections.append(f"\nACCESSIBILITY TREE:\n{truncated_tree}")

        # ── 3. Recent history ─────────────────────────────────────────────────
        recent = state.history[-self.history_window :]
        if recent:
            total_steps = state.step_count
            sections.append(f"\nRECENT ACTIONS (last {len(recent)} of {total_steps} steps):")
            for i, record in enumerate(recent):
                step_num = record.step_index + 1          # 1-based for readability
                outcome = "✓" if not record.error else f"✗ {record.error}"
                result_msg = ""
                if record.tool_result and "content" in record.tool_result:
                    if record.decision.tool_action.value == "snapshot":
                        # Explicit SNAPSHOT action — already shown in ACCESSIBILITY TREE above
                        result_msg = " → [Snapshot taken]"
                    elif record.tool_result.get("_is_prefetched_snapshot"):
                        # navigate / click / fill / select — they call _wait_for_stable_snapshot()
                        # internally. content has been overwritten with a human-readable summary;
                        # the raw tree is already in CURRENT PAGE above so we don't repeat it.
                        summary = str(record.tool_result.get("content", ""))[: self.max_result_chars]
                        result_msg = f" → {summary}" if summary else " → [Page stable]"
                    elif record.tool_result.get("type") == "image":
                        result_msg = " → [Image captured]"
                    else:
                        # Plain text result (e.g. fill confirmation) — safe to inline, but cap it
                        res_content = str(record.tool_result["content"])[: self.max_result_chars]
                        result_msg = f" → {res_content}"
                sections.append(
                    f"  Step {step_num}/{total_steps}: "
                    f"{record.decision.tool_action.value}{result_msg}  {outcome}"
                )

        # ── 4. Scratchpad (task-specific signals) ─────────────────────────────
        if state.scratchpad:
            sections.append("\nSCRATCHPAD:")
            for k, v in list(state.scratchpad.items())[:10]:
                sections.append(f"  {k}: {str(v)[:200]}")

        # ── 5. Loop-detection guard ────────────────────────────────────────────
        # If the last N actions were all snapshots the agent is stuck in a loop.
        # Inject a strong hint so Jev deprioritises choosing SNAPSHOT again.
        loop_window = settings.agent_loop_detection_window
        recent_actions = [
            r.decision.tool_action for r in state.history[-loop_window:]
        ]
        if (
            len(recent_actions) == loop_window
            and all(a == ToolAction.SNAPSHOT for a in recent_actions)
        ):
            sections.append(
                f"\n⚠️  LOOP WARNING: The last {loop_window} consecutive actions were all "
                "'snapshot'. The page content is stable — you MUST choose a "
                "different, productive action now (click, fill, navigate, done, …). "
                "Do NOT pick 'snapshot' again."
            )

        return "\n".join(sections)
