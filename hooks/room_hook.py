#!/usr/bin/env python3
"""Stop hook: wait for an Agent Rooms message and, if one arrives, keep the agent going."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8765"
DEFAULT_MAX_WAIT = 3540
LONG_POLL_MAX = 540
RESERVE = 5
SOCKET_SLACK = 15
MIN_POLL_SECONDS = 1

MESSAGE = "message"
TIMEOUT = "timeout"
STOP = "stop"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agent Rooms Stop hook")
    parser.add_argument("--format", required=True, choices=["claude", "codex", "agy"])
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--url", default=os.environ.get("ROOMS_URL") or DEFAULT_URL)
    parser.add_argument("--alias", default=os.environ.get("ROOMS_ALIAS") or "")
    parser.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT)
    return parser.parse_args(argv)


def diag(message: str, token: str = "") -> None:
    """One line on stderr; never the token."""
    with contextlib.suppress(Exception):
        line = " ".join(message.split())
        if token:
            line = line.replace(token, "***")
        print(f"[agent-rooms hook] {line}", file=sys.stderr, flush=True)


def read_token(path: str) -> str:
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as handle:
            token = handle.read().strip()
    except (OSError, ValueError):
        diag(f"cannot read token file {path}")
        return ""
    if not token:
        diag(f"token file {path} is empty")
    return token


def build_url(base: str, timeout: int, alias: str) -> str:
    query = {"timeout": str(timeout)}
    if alias:
        query["alias"] = alias
    return base.rstrip("/") + "/api/listen/next?" + urllib.parse.urlencode(query)


def fetch_next(base: str, token: str, alias: str, timeout: int) -> tuple[str, str]:
    """Return (MESSAGE, prompt), (TIMEOUT, "") or (STOP, "")."""
    headers = {"Authorization": "Bearer " + token}
    try:  # the hook must never crash the agent
        request = urllib.request.Request(build_url(base, timeout, alias), headers=headers)  # noqa: S310
        with urllib.request.urlopen(request, timeout=timeout + SOCKET_SLACK) as response:  # noqa: S310
            if response.status == 204 and response.headers.get("X-Listen-State") == "timeout":
                return TIMEOUT, ""
            if response.status == 200:
                body = json.loads(response.read().decode("utf-8"))
                prompt = body.get("prompt") if isinstance(body, dict) else None
                if isinstance(prompt, str) and prompt:
                    return MESSAGE, prompt
                diag("listen/next answered 200 without a prompt", token)
            elif response.status != 204:
                diag(f"listen/next answered HTTP {response.status}", token)
    except urllib.error.HTTPError as error:
        diag(f"listen/next answered HTTP {error.code}", token)
    except urllib.error.URLError as error:
        diag(f"server unreachable at {base}: {error.reason}", token)
    except ValueError:
        diag("listen/next returned invalid JSON", token)
    except Exception as error:
        diag(f"listen/next failed at {base}: {type(error).__name__}: {error}", token)
    return STOP, ""


def render_continue(fmt: str, prompt: str) -> str:
    decision = "continue" if fmt == "agy" else "block"
    return json.dumps({"decision": decision, "reason": prompt})


def render_stop(fmt: str) -> str:
    return json.dumps({"decision": "stop"}) if fmt == "agy" else ""


def wait_for_message(args: argparse.Namespace, token: str) -> str | None:
    deadline = time.monotonic() + args.max_wait
    while True:
        poll = int(min(LONG_POLL_MAX, deadline - time.monotonic() - RESERVE + 0.5))
        if poll < 1:
            return None
        started = time.monotonic()
        state, prompt = fetch_next(args.url, token, args.alias, poll)
        if state == MESSAGE:
            return prompt
        if state == STOP:
            return None
        if time.monotonic() - started < MIN_POLL_SECONDS:
            time.sleep(MIN_POLL_SECONDS)


def emit(text: str) -> None:
    if text:
        print(text, flush=True)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit:
        return 0
    with contextlib.suppress(Exception):
        sys.stdin.buffer.read()
    try:
        token = read_token(args.token_file)
        prompt = wait_for_message(args, token) if token else None
    except BaseException:  # includes KeyboardInterrupt
        prompt = None
    emit(render_stop(args.format) if prompt is None else render_continue(args.format, prompt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
