import base64
import hashlib
from html.parser import HTMLParser
from importlib.resources import files

import pytest

from agent_rooms.presentation.http.csp import content_security_policy

INDEX = (files("agent_rooms") / "presentation" / "http" / "web" / "index.html").read_text(encoding="utf-8")


def sha256_token(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"


class _InlineBlocks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.blocks: dict[str, list[str]] = {"script": [], "style": []}
        self.problems: list[str] = []
        self._open: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, _ in attrs:
            if name.startswith("on"):
                self.problems.append(f"<{tag}> has inline event handler {name}")
            if name == "style":
                self.problems.append(f"<{tag}> has an inline style attribute")
        values = {name: value or "" for name, value in attrs}
        if tag in self.blocks:
            if tag == "script" and "src" in values:
                self.problems.append("external <script src>")
            self._open = tag
            self.blocks[tag].append("")
        for value in values.values():
            if value.lower().startswith("javascript:"):
                self.problems.append(f"<{tag}> uses a javascript: URL")

    def handle_endtag(self, tag: str) -> None:
        if tag == self._open:
            self._open = None

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self.blocks[self._open][-1] += data


def parse(html: str) -> _InlineBlocks:
    parser = _InlineBlocks()
    parser.feed(html)
    return parser


def test_the_page_has_exactly_one_inline_script_and_one_inline_style_and_nothing_csp_would_block() -> None:
    parsed = parse(INDEX)
    assert len(parsed.blocks["script"]) == 1
    assert len(parsed.blocks["style"]) == 1
    assert parsed.problems == []


def test_the_policy_hashes_the_blocks_exactly_as_the_browser_sees_them() -> None:
    parsed = parse(INDEX)
    policy = content_security_policy(INDEX)
    assert f"script-src {sha256_token(parsed.blocks['script'][0])};" in policy
    assert f"style-src {sha256_token(parsed.blocks['style'][0])};" in policy


def test_policy_has_no_unsafe_directives_and_keeps_the_strict_baseline() -> None:
    policy = content_security_policy(INDEX)
    assert "unsafe" not in policy
    assert policy.startswith("default-src 'none'; script-src ")
    assert policy.endswith(
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    )


def test_editing_a_block_changes_the_hash() -> None:
    edited = INDEX.replace('"use strict";', '"use strict"; ', 1)
    assert content_security_policy(edited) != content_security_policy(INDEX)


def test_several_blocks_are_all_listed() -> None:
    html = "<style>a{}</style><style>b{}</style><script>1</script><script>2</script>"
    policy = content_security_policy(html)
    assert f"script-src {sha256_token('1')} {sha256_token('2')};" in policy
    assert f"style-src {sha256_token('a{}')} {sha256_token('b{}')};" in policy


def test_a_page_without_inline_blocks_gets_none_sources() -> None:
    policy = content_security_policy("<p>hi</p>")
    assert "script-src 'none';" in policy
    assert "style-src 'none';" in policy


@pytest.mark.parametrize(
    "html",
    ["<script src='x.js'></script>", "<p onclick='x()'>", "<p style='color:red'>"],
)
def test_the_blocking_detector_itself_works(html: str) -> None:
    assert parse(html).problems
