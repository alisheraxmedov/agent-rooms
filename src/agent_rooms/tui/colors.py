"""Colours for message headers and members, chosen to read on black and dark-grey terminals."""

import hashlib

SENDER_COLORS = {
    "claude": "#ff9f43",
    "codex": "#5fff87",
    "opencode": "#5fd7ff",
    "agy": "#ff87ff",
    "human": "#ffffff",
    "system": "#8a8a8a",
}
OTHER_COLORS = ("#ffd75f", "#87afff", "#ff8787", "#af87ff", "#d7ff5f", "#d7af87")

_DARK_TEXT = "#1a1a1a"
KIND_TAGS = {
    "review_request": f"bold {_DARK_TEXT} on #b794f6",
    "changes_requested": f"bold {_DARK_TEXT} on #ff7b72",
    "approved": f"bold {_DARK_TEXT} on #4ade80",
    "done": f"bold {_DARK_TEXT} on #60a5fa",
    "system": "bold #e5e5e5 on #4b5563",
}
NEUTRAL_KINDS = {"chat": "#9a9a9a", "task": "bold #d0d0d0"}
FALLBACK_KIND = "#9a9a9a"


def sender_color(name: str) -> str:
    """Fixed colour for the known identities; a stable hash into a separate set for any other name."""
    if name in SENDER_COLORS:
        return SENDER_COLORS[name]
    digest = hashlib.sha256(name.encode()).digest()
    return OTHER_COLORS[int.from_bytes(digest[:4]) % len(OTHER_COLORS)]


def kind_tag_style(kind: str) -> str:
    return KIND_TAGS.get(kind) or NEUTRAL_KINDS.get(kind, FALLBACK_KIND)
