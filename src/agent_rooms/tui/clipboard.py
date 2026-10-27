"""System clipboard through the platform tools. Text goes to the tool's stdin, never to its command line."""

import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Any, Protocol

TIMEOUT_S = 2.0
WRITERS = {
    "darwin": (("pbcopy",),),
    "linux": (("wl-copy",), ("xclip", "-selection", "clipboard"), ("xsel", "--clipboard", "--input")),
}
READERS = {
    "darwin": (("pbpaste",),),
    "linux": (
        ("wl-paste", "--no-newline"),
        ("xclip", "-selection", "clipboard", "-o"),
        ("xsel", "--clipboard", "--output"),
    ),
}


class Clipboard(Protocol):
    def write(self, text: str) -> bool: ...

    def read(self) -> str | None: ...


class SystemClipboard:
    def __init__(
        self,
        platform: str | None = None,
        run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ) -> None:
        name = platform or sys.platform
        self._platform = "linux" if name.startswith("linux") else name
        self._run = run

    def _attempt(self, commands: tuple[tuple[str, ...], ...], **kwargs: Any) -> bytes | None:
        for command in commands:
            executable = shutil.which(command[0])
            if executable is None:
                continue
            try:
                done = self._run(
                    [executable, *command[1:]], capture_output=True, check=True, timeout=TIMEOUT_S, **kwargs
                )
            except (OSError, subprocess.SubprocessError):
                continue
            return done.stdout or b""
        return None

    def write(self, text: str) -> bool:
        return self._attempt(WRITERS.get(self._platform, ()), input=text.encode()) is not None

    def read(self) -> str | None:
        data = self._attempt(READERS.get(self._platform, ()), stdin=subprocess.DEVNULL)
        return None if data is None else data.decode(errors="replace")
