import subprocess
from typing import Any

import pytest

from agent_rooms.tui import clipboard
from agent_rooms.tui.clipboard import SystemClipboard

SECRET = "line one\nline two with 'quotes' and $(dangerous)"


class Runner:
    def __init__(self, *, fail: set[str] | None = None, output: bytes = b"") -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail or set()
        self.output = output

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append({"argv": argv, **kwargs})
        if argv[0].rsplit("/", 1)[-1] in self.fail:
            raise subprocess.CalledProcessError(1, argv)
        return subprocess.CompletedProcess(argv, 0, stdout=self.output)


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_write_pipes_the_text_to_pbcopy_on_stdin_never_on_the_command_line(installed: None) -> None:
    runner = Runner()
    assert SystemClipboard(platform="darwin", run=runner).write(SECRET) is True
    (call,) = runner.calls
    assert call["argv"] == ["/usr/bin/pbcopy"]
    assert call["input"] == SECRET.encode()
    assert SECRET not in " ".join(call["argv"])


def test_write_falls_back_to_the_next_tool_on_linux(installed: None) -> None:
    runner = Runner(fail={"wl-copy"})
    assert SystemClipboard(platform="linux", run=runner).write("x") is True
    assert [call["argv"][0] for call in runner.calls] == ["/usr/bin/wl-copy", "/usr/bin/xclip"]
    assert runner.calls[1]["argv"] == ["/usr/bin/xclip", "-selection", "clipboard"]
    assert all(call["input"] == b"x" for call in runner.calls)


def test_write_reports_failure_when_no_tool_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard.shutil, "which", lambda _name: None)
    runner = Runner()
    assert SystemClipboard(platform="darwin", run=runner).write("x") is False
    assert runner.calls == []


def test_write_reports_failure_when_every_tool_fails(installed: None) -> None:
    runner = Runner(fail={"wl-copy", "xclip", "xsel"})
    assert SystemClipboard(platform="linux", run=runner).write("x") is False


def test_a_hanging_tool_is_given_up_on(installed: None) -> None:
    def hang(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(argv, 2)

    assert SystemClipboard(platform="darwin", run=hang).write("x") is False


def test_read_returns_what_pbpaste_prints(installed: None) -> None:
    runner = Runner(output="héllo\nworld".encode())
    assert SystemClipboard(platform="darwin", run=runner).read() == "héllo\nworld"
    assert runner.calls[0]["argv"] == ["/usr/bin/pbpaste"]


def test_read_is_none_when_nothing_can_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard.shutil, "which", lambda _name: None)
    assert SystemClipboard(platform="darwin", run=Runner()).read() is None


def test_an_unsupported_platform_has_no_clipboard() -> None:
    clip = SystemClipboard(platform="plan9", run=Runner())
    assert clip.write("x") is False
    assert clip.read() is None
