"""
engine/logging_setup.py

Centralised logging configuration for the playwright-jev framework.

Creates a per-run log file under `logs/` so every Jev request/response
(logged at DEBUG level) is captured without cluttering the console.

Usage (at the top of any example / entrypoint):
    from engine.logging_setup import setup_logging
    setup_logging()                # INFO on console, DEBUG in file
    setup_logging(console_level="DEBUG")  # DEBUG on both
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, MutableMapping

import structlog
from rich.logging import RichHandler


# ─── JSON file renderer ───────────────────────────────────────────────────────

class _JSONFileRenderer:
    """
    structlog processor that serialises each event as a JSON line to a file.
    Raises DropEvent so the record is never forwarded further down the chain.
    """

    def __init__(self, file_handle: Any) -> None:
        self._fh = file_handle

    def __call__(self, logger: Any, method: str, event_dict: MutableMapping[str, Any]) -> str:
        line = json.dumps(event_dict, default=str, ensure_ascii=False)
        self._fh.write(line + "\n")
        self._fh.flush()
        raise structlog.DropEvent()


# ─── Public API ───────────────────────────────────────────────────────────────

def setup_logging(
    *,
    console_level: str = "INFO",
    file_level: str = "DEBUG",
    log_dir: str | Path = "logs",
    task_id: str | None = None,
) -> Path:
    """
    Configure structlog + stdlib logging with:
      - Console  : Rich pretty output at `console_level` (default INFO)
      - Log file : JSON-lines at `file_level` (default DEBUG) — one file per run

    The file captures EVERYTHING including full Jev request state strings and
    raw API responses that are too large / verbose for the terminal.

    Args:
        console_level: Minimum level printed to the terminal  (e.g. "INFO").
        file_level:    Minimum level written to the log file  (e.g. "DEBUG").
        log_dir:       Directory for log files (created automatically).
        task_id:       Optional label embedded in the filename for easy grep.

    Returns:
        Path to the log file created for this run.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Unique filename per run:  logs/2026-09-28_11-45-36_auto-001.jsonl
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    suffix = f"_{task_id}" if task_id else ""
    log_path = log_dir / f"{ts}{suffix}.jsonl"
    log_file = log_path.open("w", encoding="utf-8")

    numeric_console = getattr(logging, console_level.upper(), logging.INFO)
    numeric_file    = getattr(logging, file_level.upper(),    logging.DEBUG)

    # ── stdlib root logger ────────────────────────────────────────────────────
    console_handler = RichHandler(
        level=numeric_console,
        show_path=False,
        markup=True,
    )
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    logging.basicConfig(
        level=min(numeric_console, numeric_file),  # root must pass lowest level
        format="%(message)s",
        handlers=[console_handler],
        force=True,
    )

    # ── structlog processors ──────────────────────────────────────────────────
    shared_pre: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
    ]

    file_renderer   = _JSONFileRenderer(log_file)
    console_renderer = structlog.dev.ConsoleRenderer()

    def _fanout(logger: Any, method: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        """Write to file first; then decide whether to keep for console."""
        level_no = getattr(logging, event_dict.get("level", "info").upper(), logging.INFO)

        # Always write DEBUG+ to the file, but ONLY for jev request/response events
        if level_no >= numeric_file:
            event_name = event_dict.get("event", "")
            if event_name in ("jev.request", "jev.response"):
                try:
                    file_renderer(logger, method, dict(event_dict))
                except structlog.DropEvent:
                    pass  # expected — renderer raises DropEvent after writing

        # Drop from the console pipeline if below the console threshold
        if level_no < numeric_console:
            raise structlog.DropEvent()

        return event_dict

    structlog.configure(
        processors=[
            *shared_pre,
            _fanout,
            console_renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            min(numeric_console, numeric_file)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,   # False so reconfigure works in tests
    )

    print(f"📄 Run log → {log_path.resolve()}", flush=True)
    return log_path
