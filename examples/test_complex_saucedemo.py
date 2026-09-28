import asyncio
from typing import Any

from engine.core.agent_loop import AgentLoop
from engine.core.state import TaskState
from engine.jev.decision_engine import JevDecisionEngine
from engine.logging_setup import setup_logging
from engine.mcp.playwright_tools import PlaywrightMCPClient
from engine.router.dynamic_router import DynamicJevRouter

async def main() -> None:
    print("=" * 80)
    print("  Complex Autonomous Task — SauceDemo (E-commerce Checkout Flow)")
    print("=" * 80)

    setup_logging(task_id="complex-saucedemo-001")

    state = TaskState(
        task_id="complex-saucedemo-001",
        goal=(
            "You are on the Swag Labs login page. "
            "Fill the username field with the value of variable 'username' "
            "and the password field with the value of variable 'password'. "
            "Click the Login button. "
            "Once the products page loads, first sort the products by selecting the value of variable 'sort_option' from the sort dropdown. "
            "Then, find the 'Sauce Labs Backpack' and click its 'Add to cart' button. "
            "Next, click the shopping cart icon at the top right of the page to view your cart. "
            "On the cart page, click the 'Checkout' button. "
            "On the checkout information page, fill your First Name with variable 'first_name', "
            "Last Name with variable 'last_name', and Zip/Postal Code with variable 'zip_code'. "
            "Click the Continue button. "
            "On the overview page, click the Finish button. "
            "Finally, take a snapshot of the order completion screen and signal DONE."
        ),
        variables={
            "username": "standard_user",
            "password": "secret_sauce",
            "sort_option": "za",  # Saucedemo value for 'Name (Z to A)'
            "first_name": "Jev",
            "last_name": "Agent",
            "zip_code": "90210"
        }
    )

    mcp = PlaywrightMCPClient()
    jev = JevDecisionEngine()
    
    # Use the upgraded DynamicJevRouter
    router = DynamicJevRouter(mcp, jev)
    
    start_url = "https://www.saucedemo.com/"

    async with mcp.session():
        # Pass the custom router to AgentLoop. 
        # Increase max_steps to 20 to give it plenty of room to execute the complex flow.
        loop = AgentLoop(mcp=mcp, router=router, jev=jev, max_steps=20)
        result = await loop.run(state, start_url=start_url)
        
        print("\n" + "=" * 60)
        print(f"Task finished with status: {result.status.value}")
        print(f"Total steps: {result.total_steps}")
        print("=" * 60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown requested.")
