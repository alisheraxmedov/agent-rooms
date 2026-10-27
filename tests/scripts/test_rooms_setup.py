from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rooms_setup.py"
spec = importlib.util.spec_from_file_location("rooms_setup", SCRIPT)
assert spec is not None
assert spec.loader is not None
rs = importlib.util.module_from_spec(spec)
sys.modules["rooms_setup"] = rs
spec.loader.exec_module(rs)

REAL_QUERY = getattr(rs, "query_codex_hooks", None)
URL = "http://127.0.0.1:8765"
ROOMS = Path("/r/agent-mcp-rooms")
HOME = Path("/h")
TOKEN = "tok-123"
TOKEN_DIR = "/h/.config/agent-rooms"
HOOK = "python3 /r/agent-mcp-rooms/hooks/room_hook.py"


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_render_claude_mcp_exact_payload():
    assert rs.render_claude_mcp(URL, TOKEN) == {
        "mcpServers": {
            "rooms": {
                "type": "http",
                "url": "http://127.0.0.1:8765/mcp",
                "headers": {"Authorization": "Bearer tok-123"},
            }
        }
    }


def test_render_claude_settings_exact_payload():
    assert rs.render_claude_settings(ROOMS, Path(TOKEN_DIR) / "claude.token", URL) == {
        "permissions": {"allow": ["mcp__rooms__*"]},
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{HOOK} --format claude --token-file {TOKEN_DIR}/claude.token",
                            "timeout": 3600,
                        }
                    ]
                }
            ]
        },
    }


def test_hook_command_passes_url_only_when_not_default():
    token_file = Path(TOKEN_DIR) / "claude.token"
    custom = rs.hook_command(ROOMS, "claude", token_file, "http://127.0.0.1:9999")
    assert custom.endswith("--url http://127.0.0.1:9999")
    assert "--url" not in rs.hook_command(ROOMS, "claude", token_file, URL)


def test_hook_command_quotes_paths_with_spaces():
    command = rs.hook_command(Path("/a b/rooms"), "codex", Path("/h/t.token"), URL)
    assert command.startswith("python3 '/a b/rooms/hooks/room_hook.py' --format codex")


CODEX_MCP = """[mcp_servers.rooms]
url = "http://127.0.0.1:8765/mcp"
http_headers = { Authorization = "Bearer tok-123" }
default_tools_approval_mode = "approve"
tool_timeout_sec = 90
startup_timeout_sec = 20
"""
CODEX_HOOK = f"""[[hooks.Stop]]
[[hooks.Stop.hooks]]
type = "command"
command = "{HOOK} --format codex --token-file {TOKEN_DIR}/codex.token"
timeout = 3600
"""
PROFILE_PATH = Path("/h/.codex/rooms.config.toml")


def test_render_codex_hook_block_exact_text():
    assert rs.render_codex_hook_block(ROOMS, Path(TOKEN_DIR) / "codex.token", URL) == CODEX_HOOK


def test_render_codex_profile_without_trust():
    text = rs.render_codex_profile(URL, TOKEN, ROOMS, Path(TOKEN_DIR) / "codex.token", PROFILE_PATH)
    assert text == CODEX_MCP + "\n" + CODEX_HOOK


def test_render_codex_profile_with_trust_seed():
    text = rs.render_codex_profile(
        URL, TOKEN, ROOMS, Path(TOKEN_DIR) / "codex.token", PROFILE_PATH, trusted_hash="sha256:abc"
    )
    state = '\n[hooks.state."/h/.codex/rooms.config.toml:stop:0:0"]\ntrusted_hash = "sha256:abc"\n'
    assert text == CODEX_MCP + "\n" + CODEX_HOOK + state


def test_render_codex_profile_is_valid_toml_when_available():
    tomllib = pytest.importorskip("tomllib")
    text = rs.render_codex_profile(
        URL, TOKEN, ROOMS, Path(TOKEN_DIR) / "codex.token", PROFILE_PATH, trusted_hash="sha256:abc"
    )
    data = tomllib.loads(text)
    assert data["mcp_servers"]["rooms"]["http_headers"] == {"Authorization": "Bearer tok-123"}
    assert data["hooks"]["Stop"][0]["hooks"][0]["timeout"] == 3600
    assert data["hooks"]["state"]["/h/.codex/rooms.config.toml:stop:0:0"]["trusted_hash"] == "sha256:abc"


def test_render_opencode_project_exact_payload():
    ref = "~/.config/agent-rooms/opencode.token"
    assert rs.render_opencode_project(URL, ROOMS, ref) == {
        "$schema": "https://opencode.ai/config.json",
        "plugins": [
            {
                "package": "/r/agent-mcp-rooms/integrations/opencode/agent-rooms",
                "options": {"url": URL, "tokenFile": ref},
            }
        ],
        "mcp": {
            "servers": {
                "rooms": {
                    "type": "remote",
                    "url": "http://127.0.0.1:8765/mcp",
                    "oauth": False,
                    "codemode": False,
                    "headers": {"Authorization": "Bearer {file:~/.config/agent-rooms/opencode.token}"},
                    "timeout": {"startup": 15000, "catalog": 15000, "execution": 120000},
                }
            }
        },
        "permissions": [{"action": "rooms_*", "resource": "*", "effect": "allow"}],
        "agents": {
            "rooms-reviewer": {
                "description": "Agent Rooms reviewer: reads and comments, never edits files",
                "mode": "primary",
                "permissions": [{"action": "edit", "resource": "*", "effect": "deny"}],
            }
        },
        "experimental": {"location_idle_timeout": "2 hours"},
    }


def test_render_agy_mcp_exact_payload():
    assert rs.render_agy_mcp(URL, TOKEN) == {
        "mcpServers": {
            "rooms": {
                "serverUrl": "http://127.0.0.1:8765/mcp",
                "headers": {"Authorization": "Bearer tok-123"},
                "timeoutSeconds": 120,
            }
        }
    }


def test_render_agy_hooks_exact_payload():
    assert rs.render_agy_hooks(ROOMS, Path(TOKEN_DIR) / "agy.token", URL) == {
        "agent-rooms": {
            "Stop": [
                {
                    "type": "command",
                    "command": f"{HOOK} --format agy --token-file {TOKEN_DIR}/agy.token",
                    "timeout": 3600,
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "call_mcp_tool",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 /r/agent-mcp-rooms/hooks/agy_approve_rooms.py",
                            "timeout": 10,
                        }
                    ],
                }
            ],
        }
    }


def test_render_wrapper_claude_exact_text():
    assert rs.render_wrapper("claude-rooms") == (
        "#!/bin/sh\n"
        "# Generated by agent-rooms rooms_setup.py\n"
        'exec claude --mcp-config "$HOME/.config/agent-rooms/claude/mcp.json"'
        ' --settings "$HOME/.config/agent-rooms/claude/settings.json" "$@"\n'
    )


def test_render_wrapper_codex_exact_text():
    assert rs.render_wrapper("codex-rooms") == (
        '#!/bin/sh\n# Generated by agent-rooms rooms_setup.py\nexec codex -p rooms "$@"\n'
    )


FORBIDDEN_RELATIVE = [
    ".claude/settings.json",
    ".claude.json",
    ".codex/config.toml",
    ".codex/hooks.json",
    ".config/opencode/opencode.json",
    ".config/opencode/plugins/x/index.js",
    ".gemini/config/mcp_config.json",
    ".gemini/config/hooks.json",
    ".gemini/antigravity-cli/settings.json",
    ".gemini/antigravity/mcp_config.json",
]


@pytest.fixture(autouse=True)
def fake_codex(monkeypatch):
    """No test may start the real `codex app-server`."""

    def fake_query(codex_home: Path):
        text = (codex_home / "config.toml").read_text(encoding="utf-8")
        command = next(line for line in text.splitlines() if line.startswith("command = "))
        digest = "sha256:" + hashlib.sha256(command.encode()).hexdigest()
        key = f"{codex_home}/config.toml:stop:0:0"
        trusted = f'[hooks.state."{key}"]' in text and f'trusted_hash = "{digest}"' in text
        return [
            {
                "key": key,
                "eventName": "stop",
                "currentHash": digest,
                "trustStatus": "trusted" if trusted else "untrusted",
            }
        ]

    monkeypatch.setattr(rs, "query_codex_hooks", fake_query)


@pytest.fixture
def env(tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    rooms = tmp_path / "rooms-repo"
    (rooms / "config").mkdir(parents=True)

    class Env:
        pass

    e = Env()
    e.tmp = tmp_path
    e.home = home
    e.rooms = rooms
    e.cfg = home / ".config" / "agent-rooms"
    e.agents_file = rooms / "config" / "agents.json"

    def run(*args: str) -> int:
        return rs.main(["--home", str(home), "--rooms-repo", str(rooms), *args])

    e.run = run

    def out() -> str:
        captured = capsys.readouterr()
        return captured.out + captured.err

    e.out = out
    return e


def make_decoys(home: Path) -> dict[Path, bytes]:
    decoys = {}
    for relative in FORBIDDEN_RELATIVE:
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"decoy {relative}\n".encode())
        decoys[path] = path.read_bytes()
    return decoys


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("relative", FORBIDDEN_RELATIVE)
def test_guard_refuses_forbidden_global_paths(tmp_path, relative):
    with pytest.raises(rs.SetupError, match="refusing"):
        rs.ensure_not_global(tmp_path / relative, tmp_path)


def test_guard_allows_our_own_paths(tmp_path):
    rs.ensure_not_global(tmp_path / ".config/agent-rooms/claude/mcp.json", tmp_path)
    rs.ensure_not_global(tmp_path / ".codex/rooms.config.toml", tmp_path)
    rs.ensure_not_global(tmp_path / ".local/bin/claude-rooms", tmp_path)


def test_guard_follows_symlinked_directories(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / "bin").symlink_to(tmp_path / ".claude")
    with pytest.raises(rs.SetupError, match="refusing"):
        rs.ensure_not_global(tmp_path / "bin" / "settings.json", tmp_path)


def test_fs_write_is_atomic_mode_exact_and_leaves_no_temp_files(tmp_path):
    fs = rs.Fs(tmp_path)
    target = tmp_path / "d" / "f.txt"
    assert fs.write(target, "hello", 0o600) == "created"
    assert mode_of(target) == 0o600
    assert fs.write(target, "hello", 0o600) == "unchanged"
    assert fs.write(target, "bye", 0o600) == "updated"
    assert target.read_text() == "bye"
    assert [p.name for p in target.parent.iterdir()] == ["f.txt"]


def test_fs_write_fixes_loose_mode_even_when_text_is_equal(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("x")
    target.chmod(0o644)
    assert rs.Fs(tmp_path).write(target, "x", 0o600) == "updated"
    assert mode_of(target) == 0o600


def test_fs_write_refuses_forbidden_path(tmp_path):
    with pytest.raises(rs.SetupError, match="refusing"):
        rs.Fs(tmp_path).write(tmp_path / ".claude" / "settings.json", "{}", 0o600)
    assert not (tmp_path / ".claude").exists()


def test_fs_remove_refuses_forbidden_path(tmp_path):
    target = tmp_path / ".codex" / "config.toml"
    target.parent.mkdir()
    target.write_text("keep")
    with pytest.raises(rs.SetupError, match="refusing"):
        rs.Fs(tmp_path).remove(target)
    assert target.read_text() == "keep"


def test_fs_dry_run_changes_nothing_and_reports(tmp_path, capsys):
    fs = rs.Fs(tmp_path, dry_run=True)
    target = tmp_path / "d" / "f.txt"
    assert fs.write(target, "secret-value", 0o600) == "created"
    assert not target.parent.exists()
    existing = tmp_path / "e.txt"
    existing.write_text("a")
    assert fs.remove(existing) is True
    assert existing.exists()
    out = capsys.readouterr().out
    assert "would create" in out
    assert str(target) in out
    assert "would remove" in out
    assert "secret-value" not in out


AGENT_NAMES = ["agy", "claude", "codex", "opencode"]
HUMAN_LINE = re.compile(r"^    (\S{32,})$", re.MULTILINE)


def sha(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def printed_human_tokens(out: str) -> list[str]:
    return HUMAN_LINE.findall(out)


def plain_tokens(e) -> dict[str, str]:
    return {a: (e.cfg / f"{a}.token").read_text().strip() for a in AGENT_NAMES}


def test_tokens_stores_hashes_in_the_registry_and_plain_tokens_in_agent_files(env):
    assert env.run("tokens") == 0
    out = env.out()
    agents = json.loads(env.agents_file.read_text())
    assert sorted(agents) == ["agy", "claude", "codex", "human", "opencode"]
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in agents.values())
    assert len(set(agents.values())) == 5
    assert mode_of(env.agents_file) == 0o600
    assert mode_of(env.cfg) == 0o700
    for name, token in plain_tokens(env).items():
        assert len(token) >= 32
        assert agents[name] == sha(token)
        assert mode_of(env.cfg / f"{name}.token") == 0o600
        assert token not in out
        assert token not in env.agents_file.read_text()
    assert not (env.cfg / "human.token").exists()


def test_tokens_prints_the_human_token_once_with_instructions_and_never_stores_it(env):
    env.run("tokens")
    out = env.out()
    (human,) = printed_human_tokens(out)
    assert json.loads(env.agents_file.read_text())["human"] == sha(human)
    assert "password manager" in out
    assert "web UI" in out
    for path in env.tmp.rglob("*"):
        if path.is_file():
            assert human not in path.read_text(errors="replace")


def test_tokens_second_run_changes_nothing_and_prints_no_secret(env):
    env.run("tokens")
    (human,) = printed_human_tokens(env.out())
    before = snapshot(env.tmp)
    assert env.run("tokens") == 0
    assert snapshot(env.tmp) == before
    out = env.out()
    assert "unchanged" in out
    assert human not in out
    assert printed_human_tokens(out) == []
    for token in plain_tokens(env).values():
        assert token not in out


def test_tokens_hashes_legacy_plain_entries_and_keeps_other_keys(env):
    env.agents_file.write_text(json.dumps({"claude": "c" * 32, "extra": "e" * 32}))
    assert env.run("tokens") == 0
    agents = json.loads(env.agents_file.read_text())
    assert agents["claude"] == sha("c" * 32)
    assert agents["extra"] == "e" * 32
    assert {"codex", "opencode", "agy", "human"} <= set(agents)
    assert (env.cfg / "claude.token").read_text() == "c" * 32 + "\n"


LEGACY = {name: f"{name}-" + "x" * 30 for name in [*AGENT_NAMES, "human"]}


def test_tokens_migrates_a_legacy_plain_registry(env):
    env.agents_file.write_text(json.dumps(LEGACY))
    assert env.run("tokens") == 0
    out = env.out()
    assert printed_human_tokens(out) == [LEGACY["human"]]
    assert json.loads(env.agents_file.read_text()) == {name: sha(token) for name, token in LEGACY.items()}
    for name in AGENT_NAMES:
        assert (env.cfg / f"{name}.token").read_text() == LEGACY[name] + "\n"
        assert LEGACY[name] not in out
    assert LEGACY["human"] not in env.agents_file.read_text()
    assert not (env.cfg / "human.token").exists()
    before = snapshot(env.tmp)
    assert env.run("tokens") == 0
    assert snapshot(env.tmp) == before
    assert LEGACY["human"] not in env.out()


def test_tokens_migration_keeps_an_existing_matching_token_file(env):
    env.agents_file.write_text(json.dumps(LEGACY))
    env.cfg.mkdir(parents=True)
    (env.cfg / "claude.token").write_text(LEGACY["claude"] + "\n")
    (env.cfg / "claude.token").chmod(0o600)
    assert env.run("tokens") == 0
    assert (env.cfg / "claude.token").read_text() == LEGACY["claude"] + "\n"


def test_tokens_migration_removes_a_leftover_human_token_file(env):
    env.agents_file.write_text(json.dumps(LEGACY))
    env.cfg.mkdir(parents=True)
    (env.cfg / "human.token").write_text(LEGACY["human"] + "\n")
    assert env.run("tokens") == 0
    assert not (env.cfg / "human.token").exists()


def test_tokens_reports_a_hashed_agent_whose_token_file_is_missing(env):
    env.run("tokens")
    env.out()
    (env.cfg / "codex.token").unlink()
    assert env.run("tokens") == 1
    out = env.out()
    assert "codex" in out
    assert "tokens --rotate codex" in out
    assert not (env.cfg / "codex.token").exists()


def test_tokens_reports_a_token_file_that_does_not_match_its_hash(env):
    env.run("tokens")
    env.out()
    (env.cfg / "agy.token").write_text("someone-elses-token\n")
    assert env.run("tokens") == 1
    out = env.out()
    assert "agy" in out
    assert "tokens --rotate agy" in out
    assert "someone-elses-token" not in out


def test_tokens_repairs_loose_modes(env):
    env.run("tokens")
    env.agents_file.chmod(0o644)
    (env.cfg / "agy.token").chmod(0o644)
    env.run("tokens")
    assert mode_of(env.agents_file) == 0o600
    assert mode_of(env.cfg / "agy.token") == 0o600


def test_tokens_rejects_invalid_agents_json_without_echoing_it(env):
    env.agents_file.write_text("not json SECRETSTUFF")
    assert env.run("tokens") == 1
    printed = env.out()
    assert "agents.json" in printed
    assert "SECRETSTUFF" not in printed
    assert env.agents_file.read_text() == "not json SECRETSTUFF"


def test_tokens_dry_run_writes_nothing_and_prints_no_token(env):
    assert env.run("--dry-run", "tokens") == 0
    assert not env.agents_file.exists()
    assert not env.cfg.exists()
    out = env.out()
    assert "would create" in out
    assert printed_human_tokens(out) == []


def test_tokens_dry_run_of_a_migration_keeps_the_plain_registry_and_hides_the_human_token(env):
    env.agents_file.write_text(json.dumps(LEGACY))
    before = snapshot(env.tmp)
    assert env.run("--dry-run", "tokens") == 0
    assert snapshot(env.tmp) == before
    out = env.out()
    assert all(token not in out for token in LEGACY.values())


def test_tokens_rotate_an_agent_rewrites_its_file_and_hash_only(env):
    env.run("tokens")
    env.out()
    old_agents = json.loads(env.agents_file.read_text())
    old_tokens = plain_tokens(env)
    assert env.run("tokens", "--rotate", "claude") == 0
    out = env.out()
    new_agents = json.loads(env.agents_file.read_text())
    new_tokens = plain_tokens(env)
    assert new_tokens["claude"] != old_tokens["claude"]
    assert new_agents["claude"] == sha(new_tokens["claude"])
    for name in ("codex", "opencode", "agy", "human"):
        assert new_agents[name] == old_agents[name]
    assert {k: v for k, v in new_tokens.items() if k != "claude"} == {
        k: v for k, v in old_tokens.items() if k != "claude"
    }
    assert new_tokens["claude"] not in out
    assert printed_human_tokens(out) == []
    assert "install" in out
    assert "restart" in out.lower()


@pytest.mark.parametrize(
    ("agent", "reminder"),
    [("codex", "install"), ("agy", "enable-repo"), ("opencode", "opencode")],
)
def test_tokens_rotate_reminds_which_commands_to_re_run(env, agent, reminder):
    env.run("tokens")
    env.out()
    assert env.run("tokens", "--rotate", agent) == 0
    assert reminder in env.out()


def test_tokens_rotate_human_prints_the_new_token_once_and_writes_no_file(env):
    env.run("tokens")
    (old,) = printed_human_tokens(env.out())
    assert env.run("tokens", "--rotate", "human") == 0
    out = env.out()
    (new,) = printed_human_tokens(out)
    assert new != old
    assert json.loads(env.agents_file.read_text())["human"] == sha(new)
    assert not (env.cfg / "human.token").exists()
    assert "web UI" in out


def test_tokens_rotate_rejects_an_unknown_agent(env):
    with pytest.raises(SystemExit):
        env.run("tokens", "--rotate", "nobody")


def test_tokens_rotate_dry_run_changes_nothing(env):
    env.run("tokens")
    env.out()
    before = snapshot(env.tmp)
    assert env.run("--dry-run", "tokens", "--rotate", "human") == 0
    assert snapshot(env.tmp) == before
    assert printed_human_tokens(env.out()) == []


FAKE_CODEX = """#!/usr/bin/env python3
import json, os, sys, time
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
if sys.argv[1:] != ["app-server"]:
    sys.exit(3)
with open(os.environ["FAKE_CODEX_LOG"], "a") as log:
    log.write(os.environ["CODEX_HOME"] + "\\n")
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        print(json.dumps({"id": msg["id"], "result": {"codexHome": os.environ["CODEX_HOME"]}}), flush=True)
    elif msg.get("method") == "hooks/list":
        if mode == "hang":
            time.sleep(60)
        if mode == "die":
            sys.exit(1)
        print(json.dumps({"method": "noise", "params": {}}), flush=True)
        hooks = [{"key": "k:stop:0:0", "eventName": "stop", "currentHash": "sha256:" + "a" * 64,
                  "trustStatus": "untrusted"}]
        data = [{"cwd": msg["params"]["cwds"][0], "hooks": hooks, "warnings": [], "errors": []}]
        print(json.dumps({"id": msg["id"], "result": {"data": data}}), flush=True)
"""


@pytest.fixture
def fake_codex_bin(tmp_path, monkeypatch):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    script = bin_dir / "codex"
    script.write_text(FAKE_CODEX)
    script.chmod(0o755)
    log = tmp_path / "codex-homes.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    return log


def test_query_codex_hooks_speaks_json_rpc_with_the_given_codex_home(tmp_path, fake_codex_bin):
    codex_home = tmp_path / "ch"
    codex_home.mkdir()
    hooks = REAL_QUERY(codex_home)
    assert hooks[0]["key"] == "k:stop:0:0"
    assert hooks[0]["currentHash"] == "sha256:" + "a" * 64
    assert fake_codex_bin.read_text().split() == [str(codex_home)]


def test_query_codex_hooks_reports_missing_binary(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(rs.CodexTrustError, match="codex"):
        REAL_QUERY(tmp_path)


def test_query_codex_hooks_reports_server_that_dies(tmp_path, fake_codex_bin, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "die")
    with pytest.raises(rs.CodexTrustError, match="exited"):
        REAL_QUERY(tmp_path)


def test_query_codex_hooks_times_out_on_a_hung_server(tmp_path, fake_codex_bin, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "hang")
    with pytest.raises(rs.CodexTrustError, match="timed out"):
        REAL_QUERY(tmp_path, timeout=1.0)


HOOK_BLOCK = CODEX_HOOK


def test_seed_codex_trust_returns_verified_hash():
    calls = []

    def query(codex_home: Path):
        config = (codex_home / "config.toml").read_text()
        calls.append(config)
        trusted = "trusted_hash" in config
        return [
            {
                "key": f"{codex_home}/config.toml:stop:0:0",
                "eventName": "stop",
                "currentHash": "sha256:h1",
                "trustStatus": "trusted" if trusted else "untrusted",
            }
        ]

    assert rs.seed_codex_trust(HOOK_BLOCK, query) == "sha256:h1"
    assert calls[0] == HOOK_BLOCK
    assert 'trusted_hash = "sha256:h1"' in calls[1]
    assert calls[1].startswith(HOOK_BLOCK)


def test_seed_codex_trust_uses_a_temp_codex_home_that_is_removed():
    seen = []

    def query(codex_home: Path):
        seen.append(codex_home)
        return [
            {
                "key": f"{codex_home}/config.toml:stop:0:0",
                "eventName": "stop",
                "currentHash": "sha256:h",
                "trustStatus": "trusted",
            }
        ]

    rs.seed_codex_trust(HOOK_BLOCK, query)
    assert seen[0] == seen[1]
    assert not seen[0].exists()


def hook_entry(**overrides):
    entry = {
        "key": "/x/config.toml:stop:0:0",
        "eventName": "stop",
        "currentHash": "sha256:h",
        "trustStatus": "trusted",
    }
    entry.update(overrides)
    return entry


@pytest.mark.parametrize(
    "answer",
    [
        [],
        [hook_entry(), hook_entry()],
        [hook_entry(key="/x/config.toml:stop:1:0")],
        [hook_entry(currentHash="")],
        [hook_entry(trustStatus="untrusted")],
    ],
)
def test_seed_codex_trust_fails_loudly_on_unexpected_answers(answer):
    with pytest.raises(rs.CodexTrustError):
        rs.seed_codex_trust(HOOK_BLOCK, lambda _home: answer)


@pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI not installed")
def test_seed_codex_trust_against_the_real_codex_app_server():
    digest = rs.seed_codex_trust(HOOK_BLOCK, REAL_QUERY)
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


@pytest.fixture
def installed(env):
    env.run("tokens")
    env.out()
    assert env.run("install") == 0
    env.install_output = env.out()
    env.agents = plain_tokens(env)
    env.profile = env.home / ".codex" / "rooms.config.toml"
    env.claude_dir = env.cfg / "claude"
    env.bin = env.home / ".local" / "bin"
    return env


def own_files(e) -> list[Path]:
    return [
        e.claude_dir / "mcp.json",
        e.claude_dir / "settings.json",
        e.profile,
        e.bin / "claude-rooms",
        e.bin / "codex-rooms",
    ]


def test_install_requires_tokens(env):
    assert env.run("install") == 1
    assert "run tokens first" in env.out()
    assert not (env.home / ".codex").exists()
    assert not (env.home / ".local").exists()


def test_install_writes_exactly_the_five_files_plus_record(installed):
    e = installed
    written = {p for p in e.home.rglob("*") if p.is_file()}
    tokens = {e.cfg / f"{a}.token" for a in rs.TOKEN_FILE_AGENTS}
    assert written == set(own_files(e)) | tokens | {e.cfg / "installed.json"}


def test_install_claude_files_have_exact_payload_and_mode_0600(installed):
    e = installed
    mcp = json.loads((e.claude_dir / "mcp.json").read_text())
    assert mcp == rs.render_claude_mcp(URL, e.agents["claude"])
    settings = json.loads((e.claude_dir / "settings.json").read_text())
    assert settings == rs.render_claude_settings(e.rooms, e.cfg / "claude.token", URL)
    command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert command == (
        f"python3 {e.rooms}/hooks/room_hook.py --format claude --token-file {e.cfg}/claude.token"
    )
    assert mode_of(e.claude_dir / "mcp.json") == 0o600
    assert mode_of(e.claude_dir / "settings.json") == 0o600
    assert mode_of(e.claude_dir) == 0o700


def test_install_codex_profile_has_seeded_trust_and_mode_0600(installed):
    e = installed
    text = e.profile.read_text()
    hook = rs.render_codex_hook_block(e.rooms, e.cfg / "codex.token", URL)
    command = hook.splitlines()[3]
    digest = "sha256:" + hashlib.sha256(command.encode()).hexdigest()
    assert text == rs.render_codex_profile(
        URL, e.agents["codex"], e.rooms, e.cfg / "codex.token", e.profile, trusted_hash=digest
    )
    assert f'[hooks.state."{e.home}/.codex/rooms.config.toml:stop:0:0"]' in text
    assert mode_of(e.profile) == 0o600
    assert "trusted" in e.install_output


def test_install_wrappers_are_executable_posix_sh(installed):
    e = installed
    for name in ("claude-rooms", "codex-rooms"):
        wrapper = e.bin / name
        assert wrapper.read_text() == rs.render_wrapper(name)
        assert mode_of(wrapper) == 0o755
        assert wrapper.read_text().startswith("#!/bin/sh\n")


def test_install_records_created_files(installed):
    e = installed
    record = json.loads((e.cfg / "installed.json").read_text())
    assert sorted(record["files"]) == sorted(str(p) for p in own_files(e))


def test_install_second_run_changes_nothing_and_says_so(installed):
    e = installed
    before = snapshot(e.tmp)
    assert e.run("install") == 0
    assert snapshot(e.tmp) == before
    assert "Nothing to change" in e.out()


def test_install_reuses_trust_without_asking_codex_again(installed, monkeypatch):
    e = installed
    monkeypatch.setattr(rs, "query_codex_hooks", lambda _home: pytest.fail("codex must not be asked again"))
    (e.cfg / "codex.token").write_text("rotated-token-value-1234567890abcdef\n")
    agents = json.loads(e.agents_file.read_text())
    e.agents_file.write_text(json.dumps({**agents, "codex": sha("rotated-token-value-1234567890abcdef")}))
    assert e.run("install") == 0
    text = e.profile.read_text()
    assert "rotated-token-value-1234567890abcdef" in text
    assert "trusted_hash" in text


def test_install_regenerates_trust_when_the_hook_changes(installed):
    e = installed
    old_hash = next(line for line in e.profile.read_text().splitlines() if "trusted_hash" in line)
    assert e.run("--url", "http://127.0.0.1:9999", "install") == 0
    new_text = e.profile.read_text()
    assert "--url http://127.0.0.1:9999" in new_text
    assert old_hash not in new_text
    assert "trusted_hash" in new_text


def test_install_without_codex_writes_profile_untrusted_and_prints_manual_fallback(env, monkeypatch):
    env.run("tokens")
    env.out()

    def missing(_home):
        raise rs.CodexTrustError("cannot run codex: not found")

    monkeypatch.setattr(rs, "query_codex_hooks", missing)
    assert env.run("install") == 0
    profile = env.home / ".codex" / "rooms.config.toml"
    assert "trusted_hash" not in profile.read_text()
    assert "[mcp_servers.rooms]" in profile.read_text()
    out = env.out()
    assert "/hooks" in out
    assert "codex-rooms" in out
    assert "not found" in out


def test_install_untrusted_answer_from_codex_falls_back_without_trust(env, monkeypatch):
    env.run("tokens")
    env.out()
    monkeypatch.setattr(
        rs,
        "query_codex_hooks",
        lambda home: [
            {
                "key": f"{home}/config.toml:stop:0:0",
                "eventName": "stop",
                "currentHash": "sha256:h",
                "trustStatus": "untrusted",
            }
        ],
    )
    assert env.run("install") == 0
    assert "trusted_hash" not in (env.home / ".codex" / "rooms.config.toml").read_text()
    assert "/hooks" in env.out()


def test_install_never_touches_forbidden_global_files(env):
    decoys = make_decoys(env.home)
    env.run("tokens")
    env.run("install")
    env.run("install")
    for path, content in decoys.items():
        assert path.read_bytes() == content
    assert set((env.home / ".codex").iterdir()) == {
        env.home / ".codex" / "config.toml",
        env.home / ".codex" / "hooks.json",
        env.home / ".codex" / "rooms.config.toml",
    }


def test_install_refuses_to_overwrite_a_foreign_file(env):
    env.run("tokens")
    foreign = env.home / ".local" / "bin" / "claude-rooms"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("#!/bin/sh\necho mine\n")
    env.out()
    assert env.run("install") == 1
    assert "not created by rooms_setup" in env.out()
    assert foreign.read_text() == "#!/bin/sh\necho mine\n"
    assert not (env.home / ".codex" / "rooms.config.toml").exists()


def test_install_dry_run_writes_nothing_even_without_tokens(env):
    assert env.run("--dry-run", "install") == 0
    assert list(env.home.iterdir()) == []
    out = env.out()
    assert "would create" in out
    assert "claude-rooms" in out
    assert "run tokens first" in out


def test_install_dry_run_with_tokens_changes_nothing_and_hides_tokens(env):
    env.run("tokens")
    env.out()
    before = snapshot(env.tmp)
    assert env.run("--dry-run", "install") == 0
    assert snapshot(env.tmp) == before
    out = env.out()
    for token in plain_tokens(env).values():
        assert token not in out


def test_install_never_prints_token_values(installed):
    for token in installed.agents.values():
        assert token not in installed.install_output


def test_uninstall_removes_exactly_what_install_created(installed):
    e = installed
    other_bin = e.bin / "my-tool"
    other_codex = e.home / ".codex" / "notes.toml"
    other_bin.write_text("mine")
    other_codex.write_text("mine")
    decoys = make_decoys(e.home)
    assert e.run("uninstall") == 0
    remaining = {p for p in e.home.rglob("*") if p.is_file()}
    tokens = {e.cfg / f"{a}.token" for a in rs.TOKEN_FILE_AGENTS}
    assert remaining == tokens | {other_bin, other_codex} | set(decoys)
    assert not e.claude_dir.exists()
    for path, content in decoys.items():
        assert path.read_bytes() == content


def test_uninstall_keeps_unrelated_files_in_our_directories(installed):
    e = installed
    extra = e.claude_dir / "user-notes.txt"
    extra.write_text("mine")
    e.run("uninstall")
    assert extra.read_text() == "mine"


def test_uninstall_without_record_removes_nothing(env):
    foreign = env.home / ".local" / "bin" / "claude-rooms"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("mine")
    env.out()
    assert env.run("uninstall") == 0
    assert "Nothing to uninstall" in env.out()
    assert foreign.read_text() == "mine"


def test_uninstall_ignores_foreign_paths_added_to_the_record(installed):
    e = installed
    decoys = make_decoys(e.home)
    record_path = e.cfg / "installed.json"
    record = json.loads(record_path.read_text())
    record["files"] += [str(p) for p in decoys]
    record_path.write_text(json.dumps(record))
    e.run("uninstall")
    for path, content in decoys.items():
        assert path.read_bytes() == content
    assert not (e.bin / "claude-rooms").exists()


def test_uninstall_leaves_tokens_unless_purged(installed):
    e = installed
    e.run("uninstall")
    assert all((e.cfg / f"{a}.token").is_file() for a in rs.TOKEN_FILE_AGENTS)
    assert e.agents_file.is_file()


def test_uninstall_purge_tokens_removes_token_files_but_not_the_server_registry(installed):
    e = installed
    assert e.run("uninstall", "--purge-tokens") == 0
    assert not e.cfg.exists()
    assert e.agents_file.is_file()


def test_uninstall_dry_run_changes_nothing(installed):
    e = installed
    before = snapshot(e.tmp)
    e.out()
    assert e.run("--dry-run", "uninstall", "--purge-tokens") == 0
    assert snapshot(e.tmp) == before
    assert "would remove" in e.out()


def test_uninstall_twice_is_fine(installed):
    e = installed
    assert e.run("uninstall") == 0
    assert e.run("uninstall") == 0


USER_OPENCODE = {
    "$schema": "https://opencode.ai/config.json",
    "theme": "tokyonight",
    "plugins": ["/other/plugin"],
    "mcp": {"servers": {"other": {"type": "remote", "url": "http://localhost:1/mcp"}}, "keep": True},
    "permissions": [{"action": "shell", "resource": "*", "effect": "ask"}],
    "agents": {"mine": {"mode": "primary"}},
    "experimental": {"foo": 1},
}
USER_AGY_MCP = {"mcpServers": {"other": {"serverUrl": "http://localhost:2/mcp"}}}
USER_AGY_HOOKS = {"my-hooks": {"Stop": [{"type": "command", "command": "echo hi", "timeout": 5}]}}


def ours_opencode(rooms=ROOMS, ref="~/.config/agent-rooms/opencode.token"):
    return rs.render_opencode_project(URL, rooms, ref)


def test_opencode_token_ref_uses_tilde_for_the_real_home_and_absolute_otherwise(tmp_path):
    assert rs.opencode_token_ref(Path.home()) == "~/.config/agent-rooms/opencode.token"
    assert rs.opencode_token_ref(tmp_path) == f"{tmp_path}/.config/agent-rooms/opencode.token"


def test_merge_opencode_keeps_unrelated_keys_and_adds_ours():
    merged = rs.merge_opencode(USER_OPENCODE, ours_opencode())
    assert merged["theme"] == "tokyonight"
    assert merged["plugins"][0] == "/other/plugin"
    assert merged["plugins"][1] == ours_opencode()["plugins"][0]
    assert merged["mcp"]["servers"]["other"] == USER_OPENCODE["mcp"]["servers"]["other"]
    assert merged["mcp"]["keep"] is True
    assert merged["mcp"]["servers"]["rooms"] == ours_opencode()["mcp"]["servers"]["rooms"]
    assert merged["permissions"][0] == USER_OPENCODE["permissions"][0]
    assert ours_opencode()["permissions"][0] in merged["permissions"]
    assert merged["agents"]["mine"] == {"mode": "primary"}
    assert "rooms-reviewer" in merged["agents"]
    assert merged["experimental"] == {"foo": 1, "location_idle_timeout": "2 hours"}
    assert USER_OPENCODE["plugins"] == ["/other/plugin"]


def test_merge_opencode_is_idempotent():
    once = rs.merge_opencode(USER_OPENCODE, ours_opencode())
    assert rs.merge_opencode(once, ours_opencode()) == once


def test_merge_opencode_keeps_a_users_own_idle_timeout():
    user = {"experimental": {"location_idle_timeout": "30 minutes"}}
    assert rs.merge_opencode(user, ours_opencode())["experimental"] == {"location_idle_timeout": "30 minutes"}


def test_merge_opencode_rejects_unexpected_shapes():
    with pytest.raises(rs.SetupError, match="plugins"):
        rs.merge_opencode({"plugins": "oops"}, ours_opencode())


def test_unmerge_opencode_restores_the_users_config():
    merged = rs.merge_opencode(USER_OPENCODE, ours_opencode())
    assert rs.unmerge_opencode(merged, ours_opencode()) == USER_OPENCODE


def test_unmerge_opencode_leaves_a_changed_idle_timeout():
    merged = rs.merge_opencode({}, ours_opencode())
    merged["experimental"]["location_idle_timeout"] = "3 hours"
    assert rs.unmerge_opencode(merged, ours_opencode())["experimental"] == {
        "location_idle_timeout": "3 hours"
    }


def test_unmerge_opencode_of_a_pure_rooms_config_leaves_only_schema():
    merged = rs.merge_opencode({}, ours_opencode())
    assert rs.unmerge_opencode(merged, ours_opencode()) == {"$schema": "https://opencode.ai/config.json"}


def test_merge_and_unmerge_agy_files():
    mcp = rs.merge_agy_mcp(USER_AGY_MCP, rs.render_agy_mcp(URL, TOKEN))
    assert set(mcp["mcpServers"]) == {"other", "rooms"}
    assert rs.unmerge_agy_mcp(mcp) == USER_AGY_MCP
    assert rs.unmerge_agy_mcp(rs.merge_agy_mcp({}, rs.render_agy_mcp(URL, TOKEN))) == {}
    hooks_ours = rs.render_agy_hooks(ROOMS, Path(TOKEN_DIR) / "agy.token", URL)
    hooks = rs.merge_agy_hooks(USER_AGY_HOOKS, hooks_ours)
    assert set(hooks) == {"my-hooks", "agent-rooms"}
    assert rs.unmerge_agy_hooks(hooks) == USER_AGY_HOOKS
    assert rs.unmerge_agy_hooks(rs.merge_agy_hooks({}, hooks_ours)) == {}


def git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


@pytest.fixture
def repo(env):
    env.run("tokens")
    env.out()
    env.repo = git_init(env.tmp / "proj")
    return env


def enable(e, path=None):
    return e.run("enable-repo", str(path or e.repo))


def opencode_file(e) -> Path:
    return e.repo / ".opencode" / "opencode.jsonc"


def test_enable_repo_requires_tokens(env):
    project = env.tmp / "proj"
    project.mkdir()
    assert env.run("enable-repo", str(project)) == 1
    assert "run tokens first" in env.out()
    assert list(project.iterdir()) == []


def test_enable_repo_rejects_a_path_that_is_not_a_directory(repo):
    assert enable(repo, repo.tmp / "missing") == 1
    assert "not a directory" in repo.out()


def test_enable_repo_writes_exact_payloads_with_modes(repo):
    e = repo
    assert enable(e) == 0
    agents = plain_tokens(e)
    ref = f"{e.home}/.config/agent-rooms/opencode.token"
    assert json.loads(opencode_file(e).read_text()) == rs.render_opencode_project(URL, e.rooms, ref)
    mcp = e.repo / ".agents" / "mcp_config.json"
    assert json.loads(mcp.read_text()) == rs.render_agy_mcp(URL, agents["agy"])
    hooks = e.repo / ".agents" / "hooks.json"
    assert json.loads(hooks.read_text()) == rs.render_agy_hooks(e.rooms, e.cfg / "agy.token", URL)
    assert mode_of(mcp) == 0o600
    assert agents["opencode"] not in opencode_file(e).read_text()
    assert agents["agy"] not in hooks.read_text()


def test_enable_repo_never_prints_tokens(repo):
    e = repo
    enable(e)
    enable(e)
    printed = e.out()
    for token in plain_tokens(e).values():
        assert token not in printed


def test_enable_repo_second_run_changes_nothing_and_says_so(repo):
    e = repo
    enable(e)
    e.out()
    before = snapshot(e.tmp)
    assert enable(e) == 0
    assert snapshot(e.tmp) == before
    assert "Nothing to change" in e.out()


def test_enable_repo_merges_into_existing_files_keeping_user_keys(repo):
    e = repo
    (e.repo / ".opencode").mkdir()
    opencode_file(e).write_text(json.dumps(USER_OPENCODE))
    (e.repo / ".agents").mkdir()
    (e.repo / ".agents" / "mcp_config.json").write_text(json.dumps(USER_AGY_MCP))
    (e.repo / ".agents" / "hooks.json").write_text(json.dumps(USER_AGY_HOOKS))
    assert enable(e) == 0
    merged = json.loads(opencode_file(e).read_text())
    assert merged["theme"] == "tokyonight"
    assert merged["plugins"][0] == "/other/plugin"
    mcp = json.loads((e.repo / ".agents" / "mcp_config.json").read_text())
    assert set(mcp["mcpServers"]) == {"other", "rooms"}
    assert mode_of(e.repo / ".agents" / "mcp_config.json") == 0o600
    hooks = json.loads((e.repo / ".agents" / "hooks.json").read_text())
    assert set(hooks) == {"my-hooks", "agent-rooms"}


COMMENTED = '{\n  // my own comment\n  "theme": "dark",\n}\n'


def test_enable_repo_refuses_jsonc_with_comments_and_prints_the_snippet(repo):
    e = repo
    (e.repo / ".opencode").mkdir()
    opencode_file(e).write_text(COMMENTED)
    e.out()
    assert enable(e) == 1
    out = e.out()
    assert opencode_file(e).read_text() == COMMENTED
    assert "comments" in out
    assert '"rooms-reviewer"' in out
    assert '"location_idle_timeout": "2 hours"' in out
    assert "Bearer {file:" in out
    assert plain_tokens(e)["agy"] not in out


def test_enable_repo_refusal_does_not_block_the_agy_files(repo):
    e = repo
    (e.repo / ".opencode").mkdir()
    opencode_file(e).write_text(COMMENTED)
    assert enable(e) == 1
    assert (e.repo / ".agents" / "hooks.json").is_file()


def test_enable_repo_adds_git_excludes_exactly_once(repo):
    e = repo
    exclude = e.repo / ".git" / "info" / "exclude"
    before = exclude.read_text()
    enable(e)
    enable(e)
    lines = exclude.read_text().splitlines()
    assert lines.count(".opencode/") == 1
    assert lines.count(".agents/") == 1
    assert exclude.read_text().startswith(before)


def test_enable_repo_does_not_duplicate_a_pattern_the_user_already_excluded(repo):
    e = repo
    exclude = e.repo / ".git" / "info" / "exclude"
    exclude.write_text(exclude.read_text() + ".agents/\n")
    enable(e)
    assert exclude.read_text().splitlines().count(".agents/") == 1
    assert exclude.read_text().splitlines().count(".opencode/") == 1


def test_enable_repo_outside_git_creates_no_git_metadata(repo):
    e = repo
    plain = e.tmp / "plain"
    plain.mkdir()
    assert enable(e, plain) == 0
    assert not (plain / ".git").exists()
    assert (plain / ".agents" / "hooks.json").is_file()


def test_enable_repo_works_in_a_git_worktree(repo):
    e = repo
    subprocess.run(
        [
            "git",
            "-C",
            str(e.repo),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )
    worktree = e.tmp / "wt"
    subprocess.run(["git", "-C", str(e.repo), "worktree", "add", "-q", str(worktree), "-b", "wt"], check=True)
    assert enable(e, worktree) == 0
    status = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"], capture_output=True, text=True, check=True
    )
    assert status.stdout.strip() == ""


def test_enable_repo_warns_when_files_are_tracked_by_git(repo):
    e = repo
    (e.repo / ".agents").mkdir()
    (e.repo / ".agents" / "mcp_config.json").write_text("{}")
    subprocess.run(["git", "-C", str(e.repo), "add", ".agents/mcp_config.json"], check=True)
    e.out()
    assert enable(e) == 0
    out = e.out()
    assert "tracked by git" in out
    assert ".agents/mcp_config.json" in out


def test_enable_repo_does_not_warn_when_nothing_is_tracked(repo):
    enable(repo)
    assert "tracked by git" not in repo.out()


def test_enable_repo_never_touches_forbidden_global_files(repo):
    decoys = make_decoys(repo.home)
    enable(repo)
    enable(repo)
    for path, content in decoys.items():
        assert path.read_bytes() == content


def test_enable_repo_dry_run_writes_nothing(repo):
    e = repo
    before = snapshot(e.tmp)
    assert e.run("--dry-run", "enable-repo", str(e.repo)) == 0
    assert snapshot(e.tmp) == before
    assert not (e.repo / ".agents").exists()
    assert "would create" in e.out()


def test_disable_repo_removes_every_rooms_file_and_exclude_line(repo):
    e = repo
    exclude = e.repo / ".git" / "info" / "exclude"
    before = exclude.read_text()
    enable(e)
    assert e.run("disable-repo", str(e.repo)) == 0
    assert not (e.repo / ".opencode").exists()
    assert not (e.repo / ".agents").exists()
    assert exclude.read_text() == before


def test_disable_repo_removes_only_ours_from_merged_files(repo):
    e = repo
    (e.repo / ".opencode").mkdir()
    opencode_file(e).write_text(json.dumps(USER_OPENCODE))
    (e.repo / ".agents").mkdir()
    (e.repo / ".agents" / "mcp_config.json").write_text(json.dumps(USER_AGY_MCP))
    (e.repo / ".agents" / "hooks.json").write_text(json.dumps(USER_AGY_HOOKS))
    enable(e)
    assert e.run("disable-repo", str(e.repo)) == 0
    assert json.loads(opencode_file(e).read_text()) == USER_OPENCODE
    assert json.loads((e.repo / ".agents" / "mcp_config.json").read_text()) == USER_AGY_MCP
    assert json.loads((e.repo / ".agents" / "hooks.json").read_text()) == USER_AGY_HOOKS


def test_disable_repo_deletes_a_file_with_only_schema_left_but_keeps_user_dirs_content(repo):
    e = repo
    enable(e)
    keep = e.repo / ".agents" / "user-notes.md"
    keep.write_text("mine")
    e.run("disable-repo", str(e.repo))
    assert not opencode_file(e).exists()
    assert keep.read_text() == "mine"
    assert not (e.repo / ".agents" / "hooks.json").exists()


def test_disable_repo_keeps_exclude_lines_the_user_added_themselves(repo):
    e = repo
    exclude = e.repo / ".git" / "info" / "exclude"
    exclude.write_text(exclude.read_text() + ".agents/\n")
    before = exclude.read_text()
    enable(e)
    e.run("disable-repo", str(e.repo))
    assert exclude.read_text() == before


def test_disable_repo_on_a_repo_that_was_never_enabled_does_nothing(repo):
    before = snapshot(repo.tmp)
    repo.out()
    assert repo.run("disable-repo", str(repo.repo)) == 0
    assert snapshot(repo.tmp) == before
    assert "Nothing to disable" in repo.out()


def test_disable_repo_refuses_commented_jsonc(repo):
    e = repo
    enable(e)
    text = "// mine\n" + opencode_file(e).read_text()
    opencode_file(e).write_text(text)
    e.out()
    assert e.run("disable-repo", str(e.repo)) == 1
    assert opencode_file(e).read_text() == text
    assert "comments" in e.out()
    assert not (e.repo / ".agents" / "hooks.json").exists()


def test_disable_repo_dry_run_changes_nothing(repo):
    e = repo
    enable(e)
    e.out()
    before = snapshot(e.tmp)
    assert e.run("--dry-run", "disable-repo", str(e.repo)) == 0
    assert snapshot(e.tmp) == before
    assert "would remove" in e.out()


def test_disable_repo_never_touches_forbidden_global_files(repo):
    decoys = make_decoys(repo.home)
    enable(repo)
    repo.run("disable-repo", str(repo.repo))
    for path, content in decoys.items():
        assert path.read_bytes() == content


def test_disable_repo_keeps_user_files_it_never_changed_even_when_empty(repo):
    e = repo
    (e.repo / ".agents").mkdir()
    hooks = e.repo / ".agents" / "hooks.json"
    hooks.write_text("{}")
    assert e.run("disable-repo", str(e.repo)) == 0
    assert hooks.read_text() == "{}"


def status_lines(text: str) -> dict[str, str]:
    """Map every printed path to its state word (first word of the line)."""
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in ("present", "missing", "unknown"):
            result[parts[-1]] = parts[0]
    return result


def test_status_on_a_clean_home_reports_missing_and_writes_nothing(env):
    before = snapshot(env.tmp)
    assert env.run("status", str(env.tmp)) == 0
    assert snapshot(env.tmp) == before
    states = status_lines(env.out())
    assert states[str(env.cfg / "claude" / "mcp.json")] == "missing"
    assert states[str(env.home / ".local" / "bin" / "codex-rooms")] == "missing"
    assert states[str(env.cfg / "claude.token")] == "missing"
    assert set(states.values()) == {"missing"}


def test_status_after_install_reports_present_and_hides_tokens(installed):
    e = installed
    assert e.run("--dry-run", "status", str(e.tmp)) == 0
    out = e.out()
    states = status_lines(out)
    for path in own_files(e):
        assert states[str(path)] == "present"
    assert states[str(e.agents_file)] == "present"
    assert "Codex hook trust: seeded" in out
    for token in e.agents.values():
        assert token not in out


def test_status_reports_untrusted_codex_profile(env, monkeypatch):
    env.run("tokens")

    def missing(_home):
        raise rs.CodexTrustError("nope")

    monkeypatch.setattr(rs, "query_codex_hooks", missing)
    env.run("install")
    env.out()
    env.run("status", str(env.tmp))
    assert "Codex hook trust: NOT seeded" in env.out()


def test_status_reports_enabled_repo_entries(repo):
    e = repo
    enable(e)
    e.out()
    assert e.run("status", str(e.repo)) == 0
    states = status_lines(e.out())
    assert states[str(opencode_file(e))] == "present"
    assert states[str(e.repo / ".agents" / "mcp_config.json")] == "present"
    assert states[str(e.repo / ".agents" / "hooks.json")] == "present"
    assert states[str(e.repo / ".git" / "info" / "exclude")] == "present"


def test_status_reports_disabled_repo_as_missing(repo):
    e = repo
    enable(e)
    e.run("disable-repo", str(e.repo))
    e.out()
    e.run("status", str(e.repo))
    states = status_lines(e.out())
    assert states[str(opencode_file(e))] == "missing"
    assert states[str(e.repo / ".git" / "info" / "exclude")] == "missing"


def test_status_defaults_to_the_current_directory(repo, monkeypatch):
    enable(repo)
    repo.out()
    monkeypatch.chdir(repo.repo)
    repo.run("status")
    assert status_lines(repo.out())[str(opencode_file(repo))] == "present"


def test_status_marks_commented_jsonc_as_unknown(repo):
    e = repo
    (e.repo / ".opencode").mkdir()
    opencode_file(e).write_text(COMMENTED)
    e.out()
    e.run("status", str(e.repo))
    assert status_lines(e.out())[str(opencode_file(e))] == "unknown"


def test_script_is_executable_with_a_python3_shebang():
    assert SCRIPT.read_text().startswith("#!/usr/bin/env python3\n")
    assert os.access(SCRIPT, os.X_OK)


def test_script_runs_as_a_command_line_program(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--home", str(tmp_path), "--dry-run", "status", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "missing" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_every_command_leaves_global_client_configs_untouched(repo):
    e = repo
    decoys = make_decoys(e.home)
    watched = [e.home / ".claude", e.home / ".config" / "opencode", e.home / ".gemini"]
    listing = {d: sorted(str(p) for p in d.rglob("*")) for d in watched}
    e.run("tokens")
    e.run("install")
    enable(e)
    e.run("status", str(e.repo))
    e.run("disable-repo", str(e.repo))
    e.run("uninstall", "--purge-tokens")
    for path, content in decoys.items():
        assert path.read_bytes() == content
    assert {d: sorted(str(p) for p in d.rglob("*")) for d in watched} == listing


def test_tokens_dry_run_does_not_promise_a_human_token_when_none_is_pending(env):
    env.run("tokens")
    env.out()
    assert env.run("--dry-run", "tokens") == 0
    assert "human token" not in env.out().lower()
