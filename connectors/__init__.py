"""Connectors: the sources of truth agents read.

Each connector is a vendor integration (Salesforce, Slack, Gong) that
exposes tools both in-process (for the harness registry) and as an MCP
server (for any MCP-capable LLM). See connectors/base.py for the pattern.
"""

from __future__ import annotations
