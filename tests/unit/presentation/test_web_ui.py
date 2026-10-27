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
    [
        "localStorage",
        "textContent",
        "/api/rooms",
        "/messages",
        "/status",
        "/members/role",
        "Authorization",
        "2000",
    ],
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


def test_closed_rooms_get_a_delete_button_that_is_hidden_otherwise(page: str) -> None:
    assert re.search(r"<button\b[^>]*\bid=\"delete\"[^>]*\bhidden\b", page)
    assert '$("delete").hidden = summary.status !== "closed";' in page
    assert '$("delete").addEventListener("click", deleteRoom);' in page


def test_delete_uses_the_room_endpoint_and_confirms_first(page: str) -> None:
    deletion = page[page.index("async function deleteRoom") :]
    deletion = deletion[: deletion.index("\n}\n")]
    assert deletion.index("confirm(") < deletion.index('api("DELETE"')
    assert "encodeURIComponent(state.room)" in deletion


def _function_body(page: str, signature: str) -> str:
    body = page[page.index(signature) :]
    return body[: body.index("\n}\n")]


def test_member_rows_get_a_role_select_with_the_four_roles(page: str) -> None:
    assert re.search(r'const ROLES = \["writer", "reviewer", "observer", "lead"\];', page)
    rendering = _function_body(page, "function renderMembers")
    assert 'el("select", "role-select")' in rendering
    assert "ROLES" in rendering
    assert 'addEventListener("change"' in rendering


def test_changing_a_role_posts_to_the_members_role_endpoint_and_confirms_lead_first(page: str) -> None:
    change = _function_body(page, "async function changeRole")
    assert "/members/role" in change
    assert "encodeURIComponent(state.room)" in change
    assert "{ agent: member.agent, alias: member.alias, role }" in change
    assert change.index("confirm(") < change.index('api("POST"')
    assert 'role === "lead"' in change


def test_role_errors_are_shown_inline_via_textcontent_and_the_select_is_reverted(page: str) -> None:
    change = _function_body(page, "async function changeRole")
    assert '$("member-error").textContent = err.message' in change
    assert re.search(r'<p\b[^>]*\bid="member-error"[^>]*role="alert"', page)
    assert "select.value = member.role" in change


def test_member_panel_is_not_rebuilt_while_a_role_select_has_focus(page: str) -> None:
    rendering = _function_body(page, "function renderMembers")
    assert "state.membersSignature" in rendering
    assert "role-select" in rendering
    assert "document.activeElement" in rendering


def test_role_select_has_an_accessible_label(page: str) -> None:
    rendering = _function_body(page, "function renderMembers")
    assert 'setAttribute("aria-label"' in rendering
