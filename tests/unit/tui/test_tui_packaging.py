import importlib
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _names(requirements: list[str]) -> set[str]:
    return {requirement.split(">")[0].split("=")[0].split("[")[0].strip() for requirement in requirements}


def test_the_console_script_points_at_the_tui_entry_point() -> None:
    assert PYPROJECT["project"]["scripts"] == {"agent-rooms": "agent_rooms.tui.__main__:main"}
    module, _, function = PYPROJECT["project"]["scripts"]["agent-rooms"].partition(":")
    assert callable(getattr(importlib.import_module(module), function))


def test_the_client_libraries_are_an_optional_extra_not_a_base_dependency() -> None:
    assert _names(PYPROJECT["project"]["optional-dependencies"]["tui"]) == {"textual", "keyring"}
    assert _names(PYPROJECT["project"]["dependencies"]) == {"mcp"}


def test_the_dev_group_has_the_client_libraries_so_plain_uv_sync_can_run_the_tests() -> None:
    assert {"textual", "keyring"} <= _names(PYPROJECT["dependency-groups"]["dev"])


def test_the_docker_image_installs_no_extras() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--extra" not in dockerfile
    assert "--all-extras" not in dockerfile
    assert "--no-dev" in dockerfile
