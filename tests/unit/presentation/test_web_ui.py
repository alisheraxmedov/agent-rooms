import re
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parents[3] / "src/agent_rooms/presentation/http/web/index.html"


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "forbidden",
    ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"],
)
def test_page_never_uses_unsafe_dom_or_code_execution(page: str, forbidden: str) -> None:
    assert forbidden not in page


def test_page_makes_no_external_requests(page: str) -> None:
    assert not re.search(r"https?://", page)


@pytest.mark.parametrize(
    "required",
    ["localStorage", "textContent", "/api/rooms", "/messages", "/status", "Authorization", "2000"],
)
def test_page_contains_required_contract_pieces(page: str, required: str) -> None:
    assert required in page


def test_page_polls_every_two_seconds(page: str) -> None:
    assert re.search(r"POLL_INTERVAL_MS\s*=\s*2000\b", page)


@pytest.mark.parametrize("button_id", ["pause", "resume", "close"])
def test_page_has_room_control_buttons(page: str, button_id: str) -> None:
    assert re.search(rf"<button\b[^>]*\bid=\"{button_id}\"", page)


@pytest.mark.parametrize("form_id", ["create-room", "post-form"])
def test_page_has_forms(page: str, form_id: str) -> None:
    assert re.search(rf"<form\b[^>]*\bid=\"{form_id}\"", page)
