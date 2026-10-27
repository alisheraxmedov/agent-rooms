import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = "agent_rooms"
LAYERS = ("domain", "application", "infrastructure", "presentation")
STDLIB = set(sys.stdlib_module_names)


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_relative(package: str, level: int, module: str | None) -> str:
    base = package.split(".")
    base = base[: len(base) - (level - 1)]
    return ".".join([*base, *([module] if module else [])])


def _absolute_imports(path: Path) -> set[str]:
    module = _module_name(path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                found.add(_resolve_relative(package, node.level, node.module))
            elif node.module:
                found.add(node.module)
    return found


def _layer_imports(layer: str) -> list[tuple[Path, str]]:
    return [
        (path, imported)
        for path in sorted((SRC / PACKAGE / layer).rglob("*.py"))
        for imported in sorted(_absolute_imports(path))
    ]


def _is_under(imported: str, prefix: str) -> bool:
    return imported == prefix or imported.startswith(prefix + ".")


def _is_stdlib(imported: str) -> bool:
    return imported.split(".", maxsplit=1)[0] in STDLIB


def _violations(layer: str, allowed: tuple[str, ...]) -> list[str]:
    return [
        f"{path.relative_to(SRC)} imports {imported}"
        for path, imported in _layer_imports(layer)
        if not _is_stdlib(imported) and not any(_is_under(imported, prefix) for prefix in allowed)
    ]


def _forbidden(layer: str, forbidden: tuple[str, ...]) -> list[str]:
    return [
        f"{path.relative_to(SRC)} imports {imported}"
        for path, imported in _layer_imports(layer)
        if any(_is_under(imported, prefix) for prefix in forbidden)
    ]


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_exists(layer: str) -> None:
    assert (SRC / PACKAGE / layer / "__init__.py").is_file()


def test_domain_imports_only_stdlib_and_itself() -> None:
    assert _violations("domain", (f"{PACKAGE}.domain",)) == []


def test_application_imports_only_domain_and_itself() -> None:
    assert _violations("application", (f"{PACKAGE}.domain", f"{PACKAGE}.application")) == []


def test_application_never_imports_sqlite_or_web_frameworks() -> None:
    assert _forbidden("application", ("sqlite3", "starlette", "mcp", "uvicorn")) == []


def test_infrastructure_never_imports_presentation_or_web_frameworks() -> None:
    forbidden = (f"{PACKAGE}.presentation", f"{PACKAGE}.bootstrap", "starlette", "mcp", "uvicorn")
    assert _forbidden("infrastructure", forbidden) == []


def test_presentation_never_imports_infrastructure_or_composition_root() -> None:
    assert _forbidden("presentation", (f"{PACKAGE}.infrastructure", f"{PACKAGE}.bootstrap")) == []


def test_relative_imports_are_resolved_against_the_importing_package() -> None:
    package = "agent_rooms.application.services"
    assert _resolve_relative(package, 1, "rooms") == "agent_rooms.application.services.rooms"
    assert _resolve_relative(package, 2, "ports") == "agent_rooms.application.ports"
    assert _resolve_relative(package, 3, "infrastructure") == "agent_rooms.infrastructure"
    assert _resolve_relative(package, 2, None) == "agent_rooms.application"
