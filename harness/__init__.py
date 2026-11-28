"""CS agent harness: the chassis around the model for post-sales teams.

The harness is everything around the model (routing, tools, memory,
approvals). The model is the engine, the harness is the chassis, and the
dashboard (v0.8.0) is what the CSM operates from.

Public surface:
- Config: environment-based configuration (config.py).
- get_llm: model-agnostic LLM client (llm.py).
- Router: cheap routing to the right specialist agent (router.py).
- ToolRegistry: the tools agents may call, summary-first (registry.py).
- ApprovalQueue: file-backed human review queue (approvals.py).
- AccountMemory: per-account memory across runs (memory.py).
- EscalationStore: escalation state per signal per account
  (escalations.py).
- ScheduleStore: scheduled jobs, file-backed (scheduler.py).
- MCPServerStore: external MCP servers, file-backed (mcp_client.py).
- MCPClient: speaks MCP to an external server, stdio or SSE.
- load_external_tools: register an external server's tools in the
  registry, namespaced by server (mcp_client.py).
- AGENTS: the eighteen specialist agents, all implemented (agents.py).
- TokenBudget: per-job token budget enforcement (summarizer.py).
"""

from __future__ import annotations

from .agents import AGENTS, Agent, Draft
from .approvals import ApprovalItem, ApprovalQueue
from .config import Config
from .escalations import EscalationRecord, EscalationStore
from .llm import LLMClient, OpenAICompatibleLLM, StubLLM, get_llm
from .mcp_client import (
    MCPClient,
    MCPServerConfig,
    MCPServerStore,
    build_external_calls,
    is_external_tool,
    load_external_tools,
    register_adapter,
    select_external_tools,
)
from .memory import AccountMemory, MemoryRecord
from .registry import Tool, ToolRegistry
from .router import RouteResult, Router
from .scheduler import (
    RunResult,
    Schedule,
    ScheduleStore,
    SchedulerAlreadyRunning,
    is_due,
    next_run_after,
    run_due,
)
from .summarizer import TokenBudget, estimate_tokens

__all__ = [
    "AGENTS",
    "AccountMemory",
    "Agent",
    "ApprovalItem",
    "ApprovalQueue",
    "Config",
    "Draft",
    "EscalationRecord",
    "EscalationStore",
    "LLMClient",
    "MCPClient",
    "MCPServerConfig",
    "MCPServerStore",
    "MemoryRecord",
    "OpenAICompatibleLLM",
    "RouteResult",
    "Router",
    "RunResult",
    "Schedule",
    "ScheduleStore",
    "SchedulerAlreadyRunning",
    "StubLLM",
    "TokenBudget",
    "Tool",
    "ToolRegistry",
    "build_external_calls",
    "estimate_tokens",
    "get_llm",
    "is_due",
    "is_external_tool",
    "load_external_tools",
    "next_run_after",
    "register_adapter",
    "run_due",
    "select_external_tools",
]

__version__ = "0.17.0"
