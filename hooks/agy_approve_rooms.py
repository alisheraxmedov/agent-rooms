#!/usr/bin/env python3
"""Antigravity PreToolUse hook: auto-approve MCP calls to the "rooms" server."""

from __future__ import annotations

import json
import sys

SERVER = "rooms"


def decide(raw: str) -> str:
    try:
        tool_call = json.loads(raw)["toolCall"]
        approved = tool_call["name"] == "call_mcp_tool" and tool_call["args"]["ServerName"] == SERVER
    except Exception:
        approved = False
    return "auto_approve" if approved else "allow"


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except Exception:
        raw = ""
    print(json.dumps({"decision": decide(raw)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
