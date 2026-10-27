import subprocess
import zipfile
from pathlib import Path

import pytest

REQUIRED_FILES = (
    "agent_rooms/__init__.py",
    "agent_rooms/resources/protocol.md",
    "agent_rooms/presentation/http/web/index.html",
)


@pytest.mark.integration
def test_wheel_contains_package_data(tmp_path: Path) -> None:
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp_path)], check=True, capture_output=True)

    (wheel,) = tmp_path.glob("agent_rooms-*.whl")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert set(REQUIRED_FILES) <= names
