import argparse
import os
import sys
from collections.abc import Sequence
from urllib.parse import urlsplit

DEFAULT_URL = "http://127.0.0.1:8765"
INSTALL_HINT = "The terminal client needs the 'tui' extra: uv tool install --editable '.[tui]'"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-rooms",
        description="Terminal client for Agent Rooms. Sign in with /login; type /help inside for commands.",
        epilog="The token comes from $ROOMS_HUMAN_TOKEN or the OS keyring (service 'agent-rooms').",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("ROOMS_URL", DEFAULT_URL),
        help="server base URL (default: $ROOMS_URL or %(default)s)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    parts = urlsplit(args.url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        print(f"agent-rooms: invalid --url {args.url!r}; expected http(s)://host:port", file=sys.stderr)
        return 2
    if parts.scheme == "http" and parts.hostname not in LOOPBACK:
        print("agent-rooms: warning: the token is sent unencrypted over plain http", file=sys.stderr)
    try:
        from agent_rooms.tui.api import RoomsApi  # noqa: PLC0415 - the TUI libraries are an optional extra
        from agent_rooms.tui.app import AgentRoomsApp  # noqa: PLC0415
        from agent_rooms.tui.credentials import TokenStore  # noqa: PLC0415
    except ModuleNotFoundError as error:
        print(f"agent-rooms: {error}. {INSTALL_HINT}", file=sys.stderr)
        return 1
    store = TokenStore()
    AgentRoomsApp(RoomsApi(args.url, store.load()), store).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
