"""
examples/test_autonomous_jev.py

Demonstrates the 100% autonomous, zero-LLM agent loop using Playwright MCP.
It uses the DynamicJevRouter to dynamically extract click/type targets from
the accessibility tree using multiple-choice Jev classifications.
"""
import asyncio
import sys

from engine.logging_setup import setup_logging
from engine.core.agent_loop import AgentLoop
from engine.core.state import TaskState
from engine.jev.decision_engine import JevDecisionEngine
from engine.mcp.playwright_tools import PlaywrightMCPClient
from engine.router.dynamic_router import DynamicJevRouter

async def main() -> None:
    print("=" * 60)
    print("  Autonomous System 1 (Dynamic Jev) — Wikipedia Search")
    print("=" * 60)

    setup_logging(task_id="auto-001")
    state = TaskState(
        task_id="auto-001",
        goal="You are on the Wikipedia Main Page. Find the search input field, type the query, and submit it. Once the results page loads, take a snapshot to confirm, then signal DONE.",
        variables={
            "search_query": "Antigravity (physics)"
        }
    )

    mcp = PlaywrightMCPClient()
    jev = JevDecisionEngine()
    
    # 2. Inject our new DynamicJevRouter
    router = DynamicJevRouter(mcp, jev)
    
    async with mcp.session():
        # Pass the custom router to AgentLoop
        loop = AgentLoop(mcp=mcp, router=router, jev=jev, max_steps=10)
        
        # Run the autonomous loop
        start_url = "https://en.wikipedia.org/wiki/Main_Page"
        result = await loop.run(state, start_url=start_url)
        
        print("\n" + "=" * 60)
        print(f"Task finished with status: {result.status.value}")
        print(f"Total steps: {result.total_steps}")
        print("=" * 60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(1)
