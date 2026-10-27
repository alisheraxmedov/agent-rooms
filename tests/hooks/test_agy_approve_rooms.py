import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "agy_approve_rooms.py"
APPROVE = '{"decision": "auto_approve"}'
ALLOW = '{"decision": "allow"}'


def payload(name="call_mcp_tool", args=None):
    tool_call = {"name": name}
    if args is not None:
        tool_call["args"] = args
    return json.dumps(
        {"toolCall": tool_call, "stepIdx": 4, "conversationId": "c", "workspacePaths": ["/repo"]}
    )


def run_hook(stdin):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_rooms_server_is_auto_approved():
    result = run_hook(payload(args={"ServerName": "rooms", "ToolName": "ping", "Arguments": {}}))
    assert result.returncode == 0
    assert result.stdout.strip() == APPROVE


def test_other_server_is_allowed():
    result = run_hook(payload(args={"ServerName": "github", "ToolName": "ping"}))
    assert result.returncode == 0
    assert result.stdout.strip() == ALLOW


def test_other_tool_name_is_allowed_even_with_rooms_server():
    result = run_hook(payload(name="run_command", args={"ServerName": "rooms"}))
    assert result.returncode == 0
    assert result.stdout.strip() == ALLOW


@pytest.mark.parametrize(
    "stdin",
    [
        payload(),
        payload(args={}),
        payload(args="rooms"),
        json.dumps({"toolCall": "call_mcp_tool"}),
        json.dumps({"stepIdx": 1}),
    ],
)
def test_missing_or_malformed_fields_are_allowed(stdin):
    result = run_hook(stdin)
    assert result.returncode == 0
    assert result.stdout.strip() == ALLOW


@pytest.mark.parametrize("stdin", ["", "not json {{{", "\x00\xff garbage", "[1, 2]", "null", '"rooms"'])
def test_garbage_stdin_is_allowed(stdin):
    result = run_hook(stdin)
    assert result.returncode == 0
    assert result.stdout.strip() == ALLOW
