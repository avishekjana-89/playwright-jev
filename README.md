# Playwright-Jev Engine

> **Autonomous, scalable browser automation — Playwright MCP + Typesafe AI Jev**

---

## What is this?

A Python engine for building intelligent browser-automation agents that make every decision through Typesafe AI's **Jev** (System 1) classification engine — no generative LLM required.

| Component | Role |
|---|---|
| **Typesafe AI Jev** | System 1 — sub-15 ms structured decisions (tool selection, safety, complexity, urgency) |
| **Playwright MCP** | Browser execution via accessibility-tree-based interaction |
| **Router** | Core router: Jev → Guardrail → Playwright MCP dispatch |
| **DynamicJevRouter** | Fully autonomous mode — Jev dynamically picks target elements and variables |

---

## Quick Start

### 1. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
cp .env.example .env        # then fill in your API keys
```

### 3. Set API keys in `.env`

```ini
# Required for Jev decisions
TYPESAFE_API_KEY=sk-...
```

### 4. Run an example

```bash
# Autonomous Wikipedia search (DynamicJevRouter)
PYTHONPATH=. .venv/bin/python examples/test_autonomous_jev.py

# Autonomous book scraping
PYTHONPATH=. .venv/bin/python examples/scrape_books.py

# Complex multi-step task: OrangeHRM login + form edit
PYTHONPATH=. .venv/bin/python examples/test_complex_task.py
```

---

## Project Structure

```
playwright-jev/
├── engine/
│   ├── core/
│   │   ├── agent_loop.py        ← Orchestration loop: SENSE → DECIDE → ACT → OBSERVE
│   │   ├── state.py             ← Pydantic models (PageState, TaskState, AgentResult, …)
│   │   └── config.py            ← Typed settings loaded from .env
│   ├── jev/
│   │   ├── decision_engine.py   ← Jev API wrapper — batches ALL questions in one call
│   │   ├── question_sets.py     ← Domain-specific question bundles (core/scraping/form/regression)
│   │   └── guardrails.py        ← Multi-layer safety gate (Noul prob + URL deny-list)
│   ├── mcp/
│   │   └── playwright_tools.py  ← Typed async wrappers for all Playwright MCP tools
│   ├── router/
│   │   ├── router.py            ← Core router: Jev → Guardrail → MCP dispatch
│   │   └── dynamic_router.py    ← DynamicJevRouter: fully autonomous, zero-LLM routing
│   ├── memory/
│   │   └── context_builder.py   ← Serialises TaskState → Jev context string
│   └── reporting/
│       └── event_log.py         ← Structured JSONL audit log per step
├── examples/
│   ├── scrape_books.py          ← Scrape books.toscrape.com (DynamicJevRouter)
│   ├── test_autonomous_jev.py   ← Wikipedia search (DynamicJevRouter)
│   └── test_complex_task.py     ← OrangeHRM login + edit (DynamicJevRouter)
├── .env.example                 ← All supported environment variables
├── requirements.txt             ← Runtime + dev dependencies
└── pyproject.toml               ← Build config, pytest, ruff, mypy settings
```

---

## Routing Modes

### Mode 1: AgentLoop with Router (default)

The default `AgentLoop` uses the core `Router` internally — Jev picks the action, the guardrail checks safety, and the result is dispatched directly to Playwright MCP.

```python
import asyncio
from engine.core.agent_loop import AgentLoop
from engine.mcp.playwright_tools import PlaywrightMCPClient

async def main():
    async with PlaywrightMCPClient().session() as mcp:
        result = await AgentLoop(mcp=mcp).run(
            goal="Extract all book titles and prices",
            start_url="https://books.toscrape.com",
        )
        print(result.status, result.total_steps)

asyncio.run(main())
```

### Mode 2: DynamicJevRouter (fully autonomous)

Uses multiple chained Jev classifications to pick both the **action** and the **target element / variable** — no hand-written selectors, no LLM.

```python
import asyncio
from engine.core.agent_loop import AgentLoop
from engine.core.state import TaskState
from engine.jev.decision_engine import JevDecisionEngine
from engine.mcp.playwright_tools import PlaywrightMCPClient
from engine.router.dynamic_router import DynamicJevRouter

async def main():
    state = TaskState(
        task_id="wiki-001",
        goal="You are on the Wikipedia Main Page. Submit the query 'Antigravity (physics)', then signal DONE.",
        variables={
            "search_query": "Antigravity (physics)",
        },
    )
    mcp = PlaywrightMCPClient()
    jev = JevDecisionEngine()
    router = DynamicJevRouter(mcp, jev)

    async with mcp.session():
        start_url = "https://en.wikipedia.org/wiki/Main_Page"
        result = await AgentLoop(mcp=mcp, router=router, jev=jev, max_steps=10).run(state, start_url=start_url)
        print(result.status, result.total_steps)

asyncio.run(main())
```

**How it works:**

1. Jev picks the next **action** (navigate / click / fill / …)
2. For element-targeting actions (click, fill, hover, select), Jev picks the exact **accessibility ref** from all interactive elements
3. For text-input actions, Jev picks the correct **task variable** to type

---

## Decision Flow

```
Task Goal + Page State
        │
        ▼
  ┌─────────────────────────────────────────┐
  │  Jev (System 1) — ONE parallel call     │  ← ~15ms
  │  • Which MCP tool?       (Choice)       │
  │  • Visual check needed?  (Noul)         │
  │  • How complex?          (Choice)       │
  └────────────────┬────────────────────────┘
                   │
        ┌──────────▼──────────┐
        │   Guardrail Gate    │ → ABORT if denied URL
        └──────────┬──────────┘
                   │
     ┌─────────────┴──────────────┐
     │                            │
  Router path             DynamicJevRouter path
  (direct dispatch)       ┌─────────────────────┐
                          │ Jev picks element   │
                          │ Jev picks variable  │
                          └──────────┬──────────┘
                                     │
                            Playwright MCP tool
                    browser_navigate / browser_click /
                    browser_type / browser_snapshot / …
```

---

## Domain Question Sets

| Set | Import | Extra Jev Questions |
|---|---|---|
| `CORE_TOOL_SELECTION` | default | Base 3 questions (action, visual, complexity) |
| `SCRAPING_QUESTIONS` | `from engine.jev.question_sets import SCRAPING_QUESTIONS` | + data_present, needs_pagination, element_type, requires_auth |
| `FORM_AUTOMATION_QUESTIONS` | `from engine.jev.question_sets import FORM_AUTOMATION_QUESTIONS` | + form_valid, has_errors, submit_ready, field_type |
| `REGRESSION_TEST_QUESTIONS` | `from engine.jev.question_sets import REGRESSION_TEST_QUESTIONS` | + test_passing, regression_detected, severity |

Use a named question set via the registry:

```python
from engine.jev.question_sets import get_question_set
qs = get_question_set("scraping")  # "core" | "scraping" | "form" | "regression"
```

Pass it into `AgentLoop`:

```python
AgentLoop(mcp=mcp, question_set=qs).run(goal="...")
```

---

## Configuration

All settings read from `.env` (see [`.env.example`](.env.example)):

| Variable | Default | Description |
|---|---|---|
| `TYPESAFE_API_KEY` | — | Typesafe AI key (**required** for real Jev; mock used if unset) |
| `PLAYWRIGHT_MCP_MODE` | `stdio` | `stdio` (spawn local npx) or `sse` (remote server) |
| `PLAYWRIGHT_MCP_SSE_URL` | `http://localhost:8931` | SSE server URL (only when `mode=sse`) |
| `PLAYWRIGHT_HEADLESS` | `true` | Run browser headlessly |
| `PLAYWRIGHT_BROWSER` | `chromium` | `chromium` \| `firefox` \| `webkit` |
| `JEV_VISUAL_THRESHOLD` | `0.60` | Min `needs_visual` Noul probability to request screenshot |
| `MAX_STEPS_PER_TASK` | `50` | Hard step limit per agent run |
| `STEP_TIMEOUT_SECONDS` | `30` | Per-step timeout |
| `MAX_RETRIES` | `3` | MCP tool-call retry attempts |
| `LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |

---

## Guardrails

The `GuardrailGate` runs a zero-latency static safety check before every action:

1. **URL deny-list** — always blocks URLs containing sensitive substrings (e.g., `bank`, `payment`, `checkout`, `admin`, `delete`, `destroy`, `reset-password`).

Extend the deny-list:

```python
from engine.jev.guardrails import GuardrailGate
gate = GuardrailGate(extra_deny_list=["internal", "staging"])
```
