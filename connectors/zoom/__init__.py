"""Zoom connector: recording lists, generated summaries, transcript search.

Exposes the tools in tools.py both in-process (for the harness registry)
and as an MCP server (server.py). See connectors/base.py for the pattern.
"""

from __future__ import annotations
