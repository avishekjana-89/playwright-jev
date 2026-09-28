"""
engine/core/config.py

Framework-wide settings loaded from environment variables.
All modules import `settings` from here — single source of truth.
"""
from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPMode(str, Enum):
    STDIO = "stdio"
    SSE = "sse"


class Browser(str, Enum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Typesafe AI (Jev) — use either TYPESAFE_API_KEY or OPENROUTER_API_KEY
    typesafe_api_key: str = Field(default="", description="Typesafe AI API key")
    openrouter_api_key: str = Field(default="", description="OpenRouter API key (alternative to TYPESAFE_API_KEY)")
    typesafe_base_url: str = Field(
        default="",
        description="Override Jev base URL (e.g. https://openrouter.ai/api for OpenRouter)",
    )

    # Playwright MCP
    playwright_mcp_mode: MCPMode = Field(default=MCPMode.STDIO)
    playwright_mcp_sse_url: str = Field(default="http://localhost:8931")
    playwright_headless: bool = Field(default=True)
    playwright_browser: Browser = Field(default=Browser.CHROMIUM)

    # Jev decision thresholds
    jev_visual_threshold: float = Field(default=0.60, ge=0.0, le=1.0)

    # Agent loop
    max_steps_per_task: int = Field(default=50, ge=1)
    step_timeout_seconds: int = Field(default=30, ge=1)
    max_retries: int = Field(default=3, ge=0)
    agent_loop_detection_window: int = Field(default=3, ge=1, description="Number of identical snapshots before forcing a state change")
    max_context_tokens: int = Field(default=25_000, ge=1_000, description="Max tokens for context layout/tree")

    # Logging
    log_level: str = Field(default="INFO")

    @model_validator(mode="after")
    def warn_missing_key(self) -> "Settings":
        # Only warn if neither key is set
        if not self.typesafe_api_key and not self.openrouter_api_key:
            import warnings
            warnings.warn(
                "Neither TYPESAFE_API_KEY nor OPENROUTER_API_KEY is set. "
                "Jev decisions will fall back to mock.",
                stacklevel=2,
            )
        return self

    @property
    def jev_api_key(self) -> str:
        """Resolved API key — prefers OPENROUTER_API_KEY, then TYPESAFE_API_KEY."""
        return self.openrouter_api_key or self.typesafe_api_key

    @property
    def jev_base_url(self) -> str | None:
        """Resolved base URL — explicit override, or OpenRouter default if using OR key."""
        if self.typesafe_base_url:
            return self.typesafe_base_url
        if self.openrouter_api_key:
            return "https://openrouter.ai/api"
        return None


# Singleton — import this everywhere
settings = Settings()
