"""
engine/mcp/playwright_tools.py

Typed async wrappers around every Playwright MCP server tool.
Handles session lifecycle, retries, and structured response parsing.

MCP server launched via:
  npx @playwright/mcp@latest [--headless] [--browser chromium|firefox|webkit]
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from engine.core.config import MCPMode, settings

log = structlog.get_logger(__name__)

# ─── MCP imports (graceful degradation if mcp not installed) ─────────────────
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.sse import sse_client
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False
    log.warning("mcp SDK not installed — using MockPlaywrightClient")


class PlaywrightMCPClient:
    """
    Manages a Playwright MCP server session and exposes typed tool methods.

    Lifecycle:
        client = PlaywrightMCPClient()
        async with client.session():
            result = await client.navigate("https://example.com")
    """

    def __init__(self) -> None:
        self._session: Any | None = None

    # ─── Session management ───────────────────────────────────────────────────

    @asynccontextmanager
    async def session(self) -> AsyncGenerator["PlaywrightMCPClient", None]:
        """Context manager that starts/stops the MCP server process."""
        if not _MCP_AVAILABLE:
            log.warning("mcp.mock.active", hint="Install 'mcp' package for real browser")
            self._session = _MockSession()
            yield self
            return

        if settings.playwright_mcp_mode == MCPMode.STDIO:
            async with self._stdio_session() as sess:
                self._session = sess
                yield self
        else:
            async with self._sse_session() as sess:
                self._session = sess
                yield self

    @asynccontextmanager
    async def _stdio_session(self) -> AsyncGenerator[Any, None]:
        """Launch Playwright MCP as a local stdio subprocess."""
        server_params = StdioServerParameters(
            command="npx",
            args=[
                "@playwright/mcp@latest",
                f"--browser={settings.playwright_browser.value}",
                "--isolated",
                *(["--headless"] if settings.playwright_headless else []),
            ],
        )
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                log.info("mcp.session.started", mode="stdio")
                yield session
        log.info("mcp.session.closed", mode="stdio")

    @asynccontextmanager
    async def _sse_session(self) -> AsyncGenerator[Any, None]:
        """Connect to a remote Playwright MCP server via SSE."""
        async with sse_client(settings.playwright_mcp_sse_url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                log.info("mcp.session.started", mode="sse", url=settings.playwright_mcp_sse_url)
                yield session
        log.info("mcp.session.closed", mode="sse")

    # ─── Internal dispatcher ──────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def _call(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Call a Playwright MCP tool by name with retry logic."""
        if self._session is None:
            raise RuntimeError("No active MCP session. Use `async with client.session()`.")

        log.debug("mcp.tool.call", tool=tool_name, args=args)
        result = await self._session.call_tool(tool_name, args)

        # MCP result has a `content` list; we normalise to a plain dict
        if hasattr(result, "content") and result.content:
            first = result.content[0]
            if hasattr(first, "text"):
                return {"type": "text", "content": first.text}
            if hasattr(first, "data"):
                return {"type": "image", "content": first.data}
        return {"type": "raw", "content": str(result)}

    # ─── DOM-stability helper ─────────────────────────────────────────────────

    async def _wait_for_stable_snapshot(
        self,
        *,
        poll_interval: float = 0.4,
        max_wait: float = 5.0,
        stable_rounds: int = 2,
    ) -> dict[str, Any]:
        """
        Poll browser_snapshot until the accessibility tree stops changing.

        Exits as soon as `stable_rounds` consecutive polls return the same
        content hash, or when `max_wait` seconds elapse — whichever comes first.
        This replaces hardcoded asyncio.sleep() calls after navigation / clicks.

        Args:
            poll_interval:  Seconds between polls (default 0.4 s).
            max_wait:       Hard upper bound in seconds (default 5 s).
            stable_rounds:  How many identical hashes in a row = "stable".

        Returns:
            The last snapshot result dict (reusable as page state).
        """
        deadline = time.monotonic() + max_wait
        prev_hash: str | None = None
        stable = 0
        result: dict[str, Any] = {}

        while time.monotonic() < deadline:
            result = await self._call("browser_snapshot", {})
            content = result.get("content", "")
            current_hash = hashlib.md5(content.encode()).hexdigest()

            if current_hash == prev_hash:
                stable += 1
                if stable >= stable_rounds:
                    log.debug(
                        "mcp.stable_snapshot.done",
                        stable_rounds=stable,
                        elapsed_s=round(max_wait - (deadline - time.monotonic()), 2),
                    )
                    result["_is_prefetched_snapshot"] = True
                    return result
            else:
                stable = 0

            prev_hash = current_hash
            await asyncio.sleep(poll_interval)

        log.warning("mcp.stable_snapshot.timeout", max_wait=max_wait)
        result["_is_prefetched_snapshot"] = True
        return result

    # ─── Typed Playwright MCP tool wrappers ───────────────────────────────────

    async def navigate(self, url: str, **_: Any) -> dict[str, Any]:
        """Navigate the browser to a URL, then wait for the page to stabilise."""
        await self._call("browser_navigate", {"url": url})
        # Wait for DOM to settle — avoids a follow-up SNAPSHOT step
        return await self._wait_for_stable_snapshot()

    async def navigate_back(self, **_: Any) -> dict[str, Any]:
        """Navigate back to the previous page, then wait for stability."""
        await self._call("browser_navigate_back", {})
        return await self._wait_for_stable_snapshot()

    async def snapshot(self, **_: Any) -> dict[str, Any]:
        """
        Capture the current page's accessibility tree.
        Primary source of page truth — preferred over screenshots for interaction.
        """
        return await self._call("browser_snapshot", {})

    async def click(
        self,
        ref: str | None = None,
        selector: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Click an element, then wait for the page to stabilise."""
        if ref:
            await self._call("browser_click", {"target": ref})
        elif selector:
            await self._call("browser_click", {"selector": selector})
        else:
            raise ValueError("click() requires either `ref` or `selector`")
        # Page may navigate or re-render after a click — wait for stability
        return await self._wait_for_stable_snapshot()

    async def type_text(
        self,
        ref: str,
        text: str,
        submit: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        """Type text into an input field, then wait for page to stabilise."""
        args: dict[str, Any] = {"target": ref, "text": text}
        if submit:
            args["submit"] = True
        await self._call("browser_type", args)
        # Typing can trigger live validation / dynamic content — wait for stability
        return await self._wait_for_stable_snapshot(poll_interval=0.3, max_wait=3.0)

    async def screenshot(
        self,
        full_page: bool = False,
        element_ref: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Capture a screenshot. Returns base64 image data."""
        args: dict[str, Any] = {"fullPage": full_page}
        if element_ref:
            args["target"] = element_ref
        return await self._call("browser_take_screenshot", args)

    async def select_option(
        self,
        ref: str,
        value: str,
        **_: Any,
    ) -> dict[str, Any]:
        """Choose an option in a <select> or combobox, then wait for stability."""
        await self._call("browser_select_option", {"target": ref, "value": value})
        return await self._wait_for_stable_snapshot(poll_interval=0.3, max_wait=3.0)


    async def hover(self, ref: str, **_: Any) -> dict[str, Any]:
        """Hover over an element (triggers hover menus, tooltips)."""
        return await self._call("browser_hover", {"target": ref})

    async def press_key(self, key: str, **_: Any) -> dict[str, Any]:
        """Press a keyboard key (e.g. 'Enter', 'Tab', 'Escape')."""
        return await self._call("browser_press_key", {"key": key})

    async def mark_done(self, **_: Any) -> dict[str, Any]:
        """Sentinel — signals AgentLoop to terminate successfully."""
        return {"status": "done"}


# ─── Mock session for development without MCP installed ──────────────────────

class _MockSession:
    """Offline stub that returns canned responses."""

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:  # noqa: ARG002
        from types import SimpleNamespace
        log.warning("mcp.mock.call", tool=tool_name)
        mock_content = SimpleNamespace(
            text=f"[MOCK] Tool '{tool_name}' called with args: {args}"
        )
        return SimpleNamespace(content=[mock_content])
