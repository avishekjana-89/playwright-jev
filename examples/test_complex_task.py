import asyncio
import sys

from engine.logging_setup import setup_logging
from engine.core.agent_loop import AgentLoop
from engine.core.state import TaskState
from engine.jev.decision_engine import JevDecisionEngine
from engine.mcp.playwright_tools import PlaywrightMCPClient
from engine.router.dynamic_router import DynamicJevRouter

async def main() -> None:
    print("=" * 80)
    print("  Complex Autonomous Task — OrangeHRM Login and Navigation")
    print("=" * 80)

    setup_logging(task_id="complex-orangehrm-001")

    state = TaskState(
        task_id="complex-orangehrm-001",
        goal=(
            "You are on the OrangeHRM demo login page. "
            "Wait for the page to load, then fill the username field with the value of variable 'username' "
            "and the password field with the value of variable 'password'. "
            "Click the Login button. "
            "After successful login and the dashboard loads, find and click on the 'My Info' link in the left navigation menu. "
            "Once the personal details page loads, clear the 'First Name' field, then fill the 'First Name' field with the value of variable 'new_first_name'. "
            "Click the Save button at the bottom of the Personal Details section. "
            "Finally, take a snapshot and signal DONE."
        ),
        variables={
            "username": "Admin",
            "password": "admin123",
            "new_first_name": "JevAgent"
        }
    )

    mcp = PlaywrightMCPClient()
    jev = JevDecisionEngine()
    
    # 2. Inject our upgraded DynamicJevRouter
    router = DynamicJevRouter(mcp, jev)
    
    async with mcp.session():
        # Pass the custom router to AgentLoop. 
        # We increase max_steps to 20 to give it plenty of room to execute the complex flow.
        loop = AgentLoop(mcp=mcp, router=router, jev=jev, max_steps=20)
        
        # Run the autonomous loop
        start_url = "https://opensource-demo.orangehrmlive.com/web/index.php/auth/login"
        result = await loop.run(state, start_url=start_url)
        
        print("\n" + "=" * 80)
        print(f"Task finished with status: {result.status.value}")
        print(f"Total steps: {result.total_steps}")
        print("=" * 80)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(1)
