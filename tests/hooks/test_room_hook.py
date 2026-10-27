import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "room_hook.py"
TOKEN = "t" * 32
PROMPT = 'Room "dev": hello\nline two'
SAMPLE_STDIN = json.dumps({"session_id": "s1", "stop_hook_active": False})
IDLE = (204, {"X-Listen-State": "idle"}, b"")
TIMEOUT = (204, {"X-Listen-State": "timeout"}, b"")


def ok_response(prompt: str = PROMPT) -> tuple[int, dict[str, str], bytes]:
    body = {"room": "dev", "alias": "", "messages": [], "prompt": prompt}
    return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()


@dataclass
class Stub:
    port: int
    script: list = field(default_factory=list)
    default: tuple = IDLE
    requests: list = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def stub():
    state = Stub(port=0)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parts = urlsplit(self.path)
            state.requests.append(
                {"path": parts.path, "query": parse_qs(parts.query), "headers": dict(self.headers)}
            )
            status, headers, body = state.script.pop(0) if state.script else state.default
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "token"
    path.write_text(TOKEN + "\n")
    return path


def run_hook(*args, stdin=SAMPLE_STDIN, env=None):
    full_env = {k: v for k, v in os.environ.items() if k not in ("ROOMS_URL", "ROOMS_ALIAS")}
    full_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(HOOK), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=20,
        env=full_env,
        check=False,
    )


def run_against(stub, token_file, fmt, *extra, **kwargs):
    return run_hook("--format", fmt, "--token-file", str(token_file), "--url", stub.url, *extra, **kwargs)


@pytest.mark.parametrize(("fmt", "decision"), [("claude", "block"), ("codex", "block"), ("agy", "continue")])
def test_message_produces_continue_decision(stub, token_file, fmt, decision):
    stub.script = [ok_response()]
    result = run_against(stub, token_file, fmt)
    assert result.returncode == 0
    assert result.stdout.strip() == json.dumps({"decision": decision, "reason": PROMPT})
    assert json.loads(result.stdout)["reason"] == PROMPT


@pytest.mark.parametrize(
    ("fmt", "expected"), [("claude", ""), ("codex", ""), ("agy", '{"decision": "stop"}')]
)
def test_idle_produces_stop(stub, token_file, fmt, expected):
    stub.script = [IDLE]
    result = run_against(stub, token_file, fmt)
    assert result.returncode == 0
    assert result.stdout.strip() == expected


def test_timeout_then_message_loops(stub, token_file):
    stub.script = [TIMEOUT, ok_response()]
    result = run_against(stub, token_file, "claude")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"decision": "block", "reason": PROMPT}
    assert len(stub.requests) == 2


def test_max_wait_bounds_endless_timeouts(stub, token_file):
    stub.default = TIMEOUT
    started = time.monotonic()
    result = run_against(stub, token_file, "agy", "--max-wait", "6")
    assert time.monotonic() - started < 10
    assert result.returncode == 0
    assert result.stdout.strip() == '{"decision": "stop"}'
    assert stub.requests
    assert all(int(r["query"]["timeout"][0]) <= 1 for r in stub.requests)


@pytest.mark.parametrize(
    "response",
    [
        (500, {}, b"boom"),
        (401, {"Content-Type": "application/json"}, b'{"error":"unauthorized"}'),
        (200, {"Content-Type": "application/json"}, b"not json"),
        (200, {"Content-Type": "application/json"}, b'{"no_prompt": 1}'),
    ],
)
@pytest.mark.parametrize(("fmt", "expected"), [("claude", ""), ("agy", '{"decision": "stop"}')])
def test_bad_responses_stop(stub, token_file, response, fmt, expected):
    stub.script = [response]
    result = run_against(stub, token_file, fmt)
    assert result.returncode == 0
    assert result.stdout.strip() == expected


def test_connection_refused_stops(token_file):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    result = run_hook("--format", "agy", "--token-file", str(token_file), "--url", f"http://127.0.0.1:{port}")
    assert result.returncode == 0
    assert result.stdout.strip() == '{"decision": "stop"}'


def test_missing_token_file_stops_without_request(stub, tmp_path):
    result = run_against(stub, tmp_path / "absent", "agy")
    assert result.returncode == 0
    assert result.stdout.strip() == '{"decision": "stop"}'
    assert stub.requests == []


def test_empty_token_stops_without_request(stub, tmp_path):
    path = tmp_path / "token"
    path.write_text("  \n")
    result = run_against(stub, path, "claude")
    assert result.returncode == 0
    assert result.stdout == ""
    assert stub.requests == []


def test_request_carries_bearer_token_and_bounded_timeout(stub, token_file):
    result = run_against(stub, token_file, "claude")
    assert result.returncode == 0
    request = stub.requests[0]
    assert request["path"] == "/api/listen/next"
    assert request["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert 1 <= int(request["query"]["timeout"][0]) <= 540
    assert TOKEN not in result.stdout + result.stderr


def test_alias_query_only_when_set(stub, token_file):
    run_against(stub, token_file, "claude")
    run_against(stub, token_file, "claude", "--alias", "reviewer-1")
    run_against(stub, token_file, "claude", env={"ROOMS_ALIAS": "from-env"})
    run_against(stub, token_file, "claude", "--alias", "flag", env={"ROOMS_ALIAS": "from-env"})
    assert [r["query"].get("alias") for r in stub.requests] == [None, ["reviewer-1"], ["from-env"], ["flag"]]


def test_rooms_url_env_is_honoured(stub, token_file):
    stub.script = [ok_response()]
    result = run_hook("--format", "claude", "--token-file", str(token_file), env={"ROOMS_URL": stub.url})
    assert json.loads(result.stdout)["reason"] == PROMPT
    assert len(stub.requests) == 1


def test_token_file_tilde_is_expanded(stub, tmp_path):
    (tmp_path / "tok").write_text(TOKEN)
    run_hook("--format", "claude", "--token-file", "~/tok", "--url", stub.url, env={"HOME": str(tmp_path)})
    assert stub.requests[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize("stdin", ["", "not json {{{", "\x00\xff garbage", "[1, 2"])
def test_any_stdin_is_ignored(stub, token_file, stdin):
    stub.script = [ok_response()]
    result = run_against(stub, token_file, "codex", stdin=stdin)
    assert result.returncode == 0
    assert json.loads(result.stdout)["reason"] == PROMPT


def test_invalid_arguments_never_exit_nonzero():
    result = run_hook("--format", "bogus")
    assert result.returncode == 0
    assert result.stdout == ""


def test_instant_timeouts_are_paced(stub, token_file):
    stub.default = TIMEOUT
    result = run_against(stub, token_file, "claude", "--max-wait", "7")
    assert result.returncode == 0
    assert result.stdout == ""
    assert 2 <= len(stub.requests) <= 4
