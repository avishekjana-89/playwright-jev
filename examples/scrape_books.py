"""
examples/scrape_books.py

Runnable example: scrape book titles and prices from books.toscrape.com.

Usage:
    PYTHONPATH=. .venv/bin/python examples/scrape_books.py
"""
import asyncio
import logging
import structlog
from rich.logging import RichHandler

from engine.core.agent_loop import AgentLoop
from engine.core.state import TaskState
from engine.jev.decision_engine import JevDecisionEngine
from engine.mcp.playwright_tools import PlaywrightMCPClient
from engine.router.dynamic_router import DynamicJevRouter

# Setup beautiful logging for the rest of the app
logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[RichHandler(show_time=False, show_path=False)])
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
        structlog.dev.ConsoleRenderer(colors=True),
    ]
)

async def main() -> None:
    print("=" * 60)
    print("  Autonomous System 1 (Dynamic Jev) — Scrape Books")
    print("=" * 60)
    
    state = TaskState(
        task_id="scrape-001",
        goal="You are on books.toscrape.com. Click on the first book and signal DONE.",
        variables={}
    )

    mcp = PlaywrightMCPClient()
    jev = JevDecisionEngine()
    router = DynamicJevRouter(mcp, jev)
    
    async with mcp.session():
        loop = AgentLoop(mcp=mcp, router=router, jev=jev, max_steps=10)
        start_url = "https://books.toscrape.com"
        result = await loop.run(state, start_url=start_url)
        
        print("\n" + "=" * 60)
        print(f"Task finished with status: {result.status.value}")
        print(f"Total steps: {result.total_steps}")
        print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
