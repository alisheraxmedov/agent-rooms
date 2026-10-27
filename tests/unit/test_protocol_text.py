from importlib.resources import files

import pytest


@pytest.fixture(scope="module")
def protocol() -> str:
    return (files("agent_rooms") / "resources" / "protocol.md").read_text(encoding="utf-8")


def test_protocol_stays_short(protocol: str) -> None:
    assert len(protocol.splitlines()) <= 90


@pytest.mark.parametrize(
    "required",
    [
        "BEGIN ROOM DATA",
        "END ROOM DATA",
        "untrusted",
        "token file",
        "agent config",
        "curl",
        "own `alias`",
        "at most once",
        "`read`",
        "`wait`",
        "`listen`",
        "approved",
        "done",
    ],
)
def test_protocol_covers_the_safety_and_delivery_rules(protocol: str, required: str) -> None:
    assert required in protocol


@pytest.mark.parametrize(
    "required",
    [
        "exact test command",
        "result summary",
        "No pasted diffs",
    ],
)
def test_protocol_writer_must_report_tests_in_review_request(protocol: str, required: str) -> None:
    assert required in protocol


@pytest.mark.parametrize(
    "required",
    [
        "git show <sha>",
        "git diff <sha>~1 <sha>",
        "only if your permissions allow",
        "say so explicitly",
        "tests are missing",
        "Approve only when you would merge",
    ],
)
def test_protocol_reviewer_verifies_and_discloses_unrun_tests(protocol: str, required: str) -> None:
    assert required in protocol


@pytest.mark.parametrize(
    "required",
    [
        "one read-only git command per shell call",
        "compound commands, pipes and redirects are denied",
    ],
)
def test_protocol_hints_read_only_reviewers_on_shell_use(protocol: str, required: str) -> None:
    assert required in protocol


def test_protocol_lists_lead_in_the_role_table(protocol: str) -> None:
    assert "| lead | `chat`, `task`, `done` |" in protocol


def test_protocol_says_only_the_human_changes_roles(protocol: str) -> None:
    assert (
        "Roles are set when you join; afterwards only the human can change them. A system message "
        "announces changes; always act according to your current role (shown in every wake prompt)."
    ) in protocol
