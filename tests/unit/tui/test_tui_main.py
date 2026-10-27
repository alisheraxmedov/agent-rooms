import pytest

from agent_rooms.tui.__main__ import DEFAULT_URL, _parser, main


def test_help_works_and_mentions_the_token_sources(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    out = capsys.readouterr().out
    assert "--url" in out
    assert "ROOMS_HUMAN_TOKEN" in out
    assert "keyring" in out


def test_the_url_defaults_to_the_local_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROOMS_URL", raising=False)
    assert _parser().parse_args([]).url == DEFAULT_URL == "http://127.0.0.1:8765"


def test_the_url_default_follows_rooms_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMS_URL", "http://127.0.0.1:9999")
    assert _parser().parse_args([]).url == "http://127.0.0.1:9999"
    assert _parser().parse_args(["--url", "http://localhost:1"]).url == "http://localhost:1"


@pytest.mark.parametrize("url", ["ftp://example.com", "not a url", "http://", "127.0.0.1:8765"])
def test_a_bad_url_is_refused_before_anything_starts(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--url", url]) == 2
    assert "invalid --url" in capsys.readouterr().err
