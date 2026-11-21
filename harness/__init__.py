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
- AGENTS: the nine specialist agents, all implemented (agents.py).
- TokenBudget: per-job token budget enforcement (summarizer.py).
"""

from __future__ import annotations

from .agents import AGENTS, Agent, Draft
from .approvals import ApprovalItem, ApprovalQueue
from .config import Config
from .llm import LLMClient, OpenAICompatibleLLM, StubLLM, get_llm
from .registry import Tool, ToolRegistry
from .router import RouteResult, Router
from .summarizer import TokenBudget, estimate_tokens

__all__ = [
    "AGENTS",
    "Agent",
    "ApprovalItem",
    "ApprovalQueue",
    "Config",
    "Draft",
    "LLMClient",
    "OpenAICompatibleLLM",
    "RouteResult",
    "Router",
    "StubLLM",
    "TokenBudget",
    "Tool",
    "ToolRegistry",
    "estimate_tokens",
    "get_llm",
]

__version__ = "0.9.0"
