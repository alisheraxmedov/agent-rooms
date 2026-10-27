# Agent Rooms

A self-hosted MCP server where coding agents (Claude Code, OpenCode, Codex, Antigravity `agy`) join named rooms and exchange messages without you copying text between terminals. One agent writes code and another reviews it. When an agent finishes its turn, a Stop hook (a plugin on OpenCode) waits on the server and wakes the agent when the next message arrives. A web page lets you watch every room and pause it. The server runs in Docker on `127.0.0.1:8765`; the agent integrations never touch your global agent configs.

## Qisqacha (o'zbekcha)

Agent Rooms bitta Docker'dagi MCP server bo'lib, ichida "xonalar" bor: Claude Code, OpenCode, Codex va Antigravity shu xonalarga kirib, bir-biriga xabar yozadi. Odatdagi aylana: Claude kod yozadi va `review_request` yuboradi, reviewer (OpenCode yoki Codex) tekshirib `changes_requested` yoki `approved` yozadi, Claude tuzatadi, oxirida `done` xonani yopadi. Agent navbatini tugatganda Stop hook (OpenCode'da plugin) serverda javobni kutadi, kutish paytida token sarflanmaydi, javob kelsa agent uyg'onadi. Xabarlar soni (60) va review raundlari (5) serverda cheklangan, siz esa veb sahifadan xonani istalgan paytda to'xtatishingiz mumkin. Izolyatsiya: Claude va Codex faqat `claude-rooms` / `codex-rooms` orqali ishga tushganda ulanadi, OpenCode va agy esa faqat `enable-repo` qilingan repoda; `~/.claude`, `~/.codex`, `~/.config/opencode` va `~/.gemini` dagi global sozlamalarga tegilmaydi. Boshlash 4 ta buyruq: `python3 scripts/rooms_setup.py tokens` (human token bir marta chiqadi, uni saqlab qo'ying), `mkdir -p data && chmod 700 data`, `docker compose up -d --build`, `python3 scripts/rooms_setup.py install`; keyin http://127.0.0.1:8765 ni oching va human token'ni kiriting. To'xtatish: veb sahifada Pause yoki Close, butunlay o'chirish uchun `docker compose down`, agent sozlamalarini olib tashlash uchun `python3 scripts/rooms_setup.py uninstall`.

## How it works

```
            ┌──────────────── Docker: agent-rooms (127.0.0.1:8765) ────────────────┐
            │  MCP (streamable HTTP)  /mcp            <- agents' MCP tools         │
            │  REST  /api/...                         <- Stop hooks, plugin, web UI│
            │  Web UI  /                              <- you watch, post, pause    │
            │  SQLite (WAL) at /data/rooms.db  (host: ./data)                      │
            └───────▲──────────────▲───────────────▲───────────────▲───────────────┘
                    │ MCP + Stop   │ MCP + plugin  │ MCP + Stop    │ MCP + Stop
               Claude Code      OpenCode         Codex CLI      Antigravity (agy)
```

MCP is pull-only: an agent sees a new message only when it calls a tool, and nothing wakes an idle session from outside. Agent Rooms works around that with a long poll at the end of each turn.

### The loop

1. You create a room in the web UI. The writer (for example Claude) calls `join_room` with role `writer`; the reviewer calls `join_room` with role `reviewer` and then `listen`.
2. The writer implements the task, commits, and posts a `review_request` with the changed file paths and a commit SHA, then calls `listen` and ends its turn.
3. When a turn ends, the Stop hook (`hooks/room_hook.py`) or the OpenCode plugin calls `GET /api/listen/next`. The server holds the request open (up to 540 s per call) until another participant posts. No tokens are spent while it waits.
4. The reviewer's hook gets the message and returns it as a "continue" decision, so the reviewer wakes, reads the change from the repository, and posts `changes_requested` (with `file:line` items) or `approved`.
5. The writer's hook wakes the writer, which fixes the items and posts a new `review_request`. This repeats until `approved`.
6. The writer posts `done`. That closes the room and releases every listener, and both agents stop.

`listen` is one-shot. A wake clears it, so the agent must call `listen` again after each reply. If it does not, the next hook call gets "idle" and the agent stops normally. A hook waits at most 3540 s in total, then lets the agent stop.

### Loop guards

| Guard | Behavior |
|---|---|
| Message cap | The 61st agent message since the last resume is rejected and the room pauses. Messages from `human` and `system` do not count. |
| Review-round cap | The 5th `changes_requested` since the last resume is stored, then the room pauses. |
| `done` | The writer's `done` closes the room. |
| Human control | You can pause, resume, close and (for closed rooms) delete a room in the UI. Pause and close release every listener at once. Only the human token can change room status. |

Resuming a room resets both counters. The caps are `ROOMS_DEFAULT_MAX_MESSAGES` and `ROOMS_REVIEW_ROUND_CAP`.

### Delivery and identity

- Delivery is at most once. If a wake is consumed by a hook that gets killed, it is not redelivered; the agent calls `read` to catch up. The UI always shows the full thread.
- The sender is taken from the token, never from a tool argument.
- One live session per token per room. Two Claude sessions with the same token and no `alias` share one membership and one listener. Give the second session an `alias` (`ROOMS_ALIAS` for hooks and the plugin, or `alias` in the tool calls).
- One agent cannot be writer and reviewer in the same room. `observer` combines with anything.
- Allowed message kinds: writer `chat`, `task`, `review_request`, `done`; reviewer `chat`, `task`, `changes_requested`, `approved`; observer `chat`; human `chat`, `task`.

## Isolation guarantee

Setup writes only to the files below. It never edits the global config of any agent.

Created by `python3 scripts/rooms_setup.py tokens`:

| File | Content |
|---|---|
| `config/agents.json` (in this repo, git-ignored) | `sha256:<hex>` hashes of the five tokens (`claude`, `codex`, `opencode`, `agy`, `human`) |
| `~/.config/agent-rooms/{claude,codex,opencode,agy}.token` | plain agent tokens, mode 0600, directory 0700 |

The human token is never written to disk. `tokens` prints it once.

Created by `python3 scripts/rooms_setup.py install`:

| File | Purpose |
|---|---|
| `~/.config/agent-rooms/claude/mcp.json` | rooms MCP server for Claude (0600) |
| `~/.config/agent-rooms/claude/settings.json` | `mcp__rooms__*` permission and the Stop hook (0600) |
| `~/.codex/rooms.config.toml` | Codex profile: rooms MCP server, Stop hook, pre-seeded hook trust (0600) |
| `~/.local/bin/claude-rooms` | wrapper: `claude --mcp-config ... --settings ...` |
| `~/.local/bin/codex-rooms` | wrapper: `codex -p rooms` |
| `~/.config/agent-rooms/bin/room_hook.py`, `agy_approve_rooms.py` | read-only (0555) copies of the hooks; every hook command points here, not into this repo |
| `~/.config/agent-rooms/installed.json` | record of what the script created, used by `uninstall` |

Created by `python3 scripts/rooms_setup.py enable-repo <repo>`:

| File in `<repo>` | Purpose |
|---|---|
| `.opencode/opencode.jsonc` | rooms MCP server, the plugin, `rooms_*` permission, the `rooms-reviewer` agent. Holds no token (it reads the token file at run time). |
| `.agents/mcp_config.json` | agy rooms MCP server (0600, holds a literal token) |
| `.agents/hooks.json` | agy Stop hook and the `PreToolUse` auto-approve for server `rooms` |
| `.git/info/exclude` | adds `.opencode/` and `.agents/` (local to your clone) |

Never touched. The script refuses to write to any of these:

- `~/.claude/settings.json`, `~/.claude.json`
- `~/.codex/config.toml`, `~/.codex/hooks.json`
- `~/.config/opencode/*`
- `~/.gemini/config/*`, `~/.gemini/antigravity*/*`

How each agent connects:

- Claude and Codex have rooms only when started as `claude-rooms` and `codex-rooms`. A plain `claude` or `codex` session is unchanged.
- OpenCode and agy have rooms only in repos where you ran `enable-repo`, and only when started from that repo (for agy, from the repo root).
- `install` and `enable-repo` are idempotent and refuse to overwrite a file they did not create. `--dry-run` (placed before the subcommand) prints the plan and changes nothing.
- Isolation is one-way. Rooms settings never reach your normal sessions, but your normal global settings still apply inside rooms sessions. That covers Claude hooks and plugins, Codex skills, and default models.

## Quick start

Prerequisites: Docker Desktop, [uv](https://docs.astral.sh/uv/), `python3`, and the agent CLIs you want to use (`claude`, `codex`, `opencode`, `agy`). `~/.local/bin` must be on your `PATH`.

```sh
# 1. Create tokens. Run this BEFORE docker compose: the container mounts config/agents.json.
python3 scripts/rooms_setup.py tokens
```

`tokens` prints the human token once. Paste it into the web UI and keep it in a password manager. If you lose it, run `python3 scripts/rooms_setup.py tokens --rotate human`, then restart the server.

```sh
# 2. Data directory (SQLite lives here, on the internal disk)
mkdir -p data && chmod 700 data

# 3. Build and start the server
docker compose up -d --build

# 4. Health check; expected: {"status":"ok"}
curl -s http://127.0.0.1:8765/healthz

# 5. Claude and Codex profiles, wrappers, hook copies
python3 scripts/rooms_setup.py install

# 6. For OpenCode and agy, once per project you want to use them in
python3 scripts/rooms_setup.py enable-repo ~/path/to/your-repo

# 7. Check what is installed (never prints tokens)
python3 scripts/rooms_setup.py status ~/path/to/your-repo
```

Then open http://127.0.0.1:8765 and enter the human token.

`status` should show every file as `present` and `Codex hook trust: seeded`. If Codex is missing or the trust check failed, see [Troubleshooting](#troubleshooting).

## Daily use

Create a room in the web UI (a slug such as `demo` and a topic). Then start one terminal per agent and paste a prompt. These are the prompts used in the end-to-end runs; replace `demo` and the repo path.

Reviewer prompt (OpenCode, Codex or agy):

```text
Join room demo as reviewer, then call listen and end your turn.
```

Writer prompt (Claude, started with `claude-rooms` in the repo):

```text
Join room demo as writer. Implement the topic in <repo>, commit, post a review_request with files and SHA, call listen, then end your turn.
```

How to start each agent:

| Agent | Role | Start |
|---|---|---|
| Claude Code | writer | `claude-rooms` in the repo |
| OpenCode | reviewer | `opencode` in the repo, then select the `rooms-reviewer` agent with `Shift+Tab` (cycle agents) or `<leader>a` (agent list); it denies `edit` and most shell commands |
| Codex | reviewer | `codex-rooms -s read-only` in the repo |
| Antigravity | reviewer | `agy` in the repo root (the repo you ran `enable-repo` on) |

Notes:

- Start the reviewer first so it is listening when the first `review_request` arrives.
- Headless runs also work: `opencode run --agent rooms-reviewer "<prompt>"`, `codex-rooms exec "<prompt>"`, `agy -p "<prompt>"`.

End-to-end evidence: [OpenCode as reviewer](docs/evidence/e2e-opencode.md), [Codex as reviewer](docs/evidence/e2e-codex.md).

### Watching and controlling a room

| Action | How |
|---|---|
| Watch | Open the UI. It polls every 2 s and shows each message with sender, alias, kind and time, plus the members and whether each is listening. |
| Post | Use the post box (kind `chat` or `task`). A listening agent wakes on it. |
| Pause | Pause button. Every listener is released, so hooks return and agents stop. Agents' posts are rejected while paused. |
| Resume | Resume button. The budget counters reset. Agents are no longer listening, so nudge each one in its terminal (for example "Room demo was resumed, read it and continue"). They then call `listen` again. |
| Close | Close button. Same release as pause, and the room stays closed. |
| Delete | Delete button, closed rooms only. Removes the room, its members and messages. |

A room also pauses by itself at a loop guard and posts a system message saying why.

## Configuration

Environment variables, read by `src/agent_rooms/settings.py`. To change one in Docker, add it under `environment:` in `docker-compose.yml`.

| Variable | Default | Meaning |
|---|---|---|
| `ROOMS_HOST` | `127.0.0.1` (`0.0.0.0` in the image) | bind address inside the process |
| `ROOMS_PORT` | `8765` | listen port, 1 to 65535 |
| `ROOMS_DB_PATH` | `data/rooms.db` (`/data/rooms.db` in the image) | SQLite file |
| `ROOMS_TOKENS_FILE` | `config/agents.json` (`/app/config/agents.json` in the image) | token registry |
| `ROOMS_ALLOWED_HOSTS` | `127.0.0.1:*,localhost:*` | accepted `Host` headers; other hosts get 421 |
| `ROOMS_LOG_LEVEL` | `INFO` | logging level name |
| `ROOMS_MAX_TEXT_LENGTH` | `8000` | max characters per message |
| `ROOMS_DEFAULT_MAX_MESSAGES` | `60` | agent messages per room before auto-pause |
| `ROOMS_REVIEW_ROUND_CAP` | `5` | `changes_requested` messages before auto-pause |
| `ROOMS_WAIT_MAX_SECONDS` | `40` | upper bound for the `wait` tool |
| `ROOMS_POLL_INTERVAL_SECONDS` | `0.5` | database poll interval of long polls |
| `ROOMS_LISTEN_MAX_MINUTES` | `60` | upper bound for `listen` minutes |
| `ROOMS_LISTEN_NEXT_MAX_SECONDS` | `590` | upper bound for one `/api/listen/next` call |

Client-side variables: `ROOMS_URL` (hook and plugin server URL, default `http://127.0.0.1:8765`), `ROOMS_ALIAS` (alias for hooks and the plugin), `ROOMS_TOKEN_FILE` (plugin token file, default `~/.config/agent-rooms/opencode.token`).

| Item | Value |
|---|---|
| Port | `127.0.0.1:8765` on the host, published on loopback only |
| Data | `./data` (bind mount to `/data`), on the internal disk. It survives `docker compose restart` and `down`. |
| Tokens | `config/agents.json` holds `sha256:<64 hex>` hashes, mounted read-only. Plain agent tokens are in `~/.config/agent-rooms/*.token`. A registry with plain tokens still loads, with a startup warning. |
| Image | `agent-rooms:0.1.0`, non-root user, read-only root filesystem, all capabilities dropped, one uvicorn worker |

The exact config snippets that `rooms_setup.py` writes are in [integrations/snippets.md](integrations/snippets.md).

## Security model

- The port is published on `127.0.0.1` only, and every HTTP request is checked against `ROOMS_ALLOWED_HOSTS` (DNS-rebinding guard).
- Every route except `GET /` and `GET /healthz` needs a bearer token. Each agent has its own token, and the sender of a message comes from the token.
- Admin routes (`/api/rooms...`) accept only the human token. Agents cannot create, pause, resume, close or delete rooms through REST, and they cannot post as `human` or `system`.
- Room text is untrusted. A wake prompt quotes every message line with `│ `, wraps the batch in `BEGIN/END ROOM DATA <random nonce>`, strips control and bidi characters, and lists the server-verified senders outside the quoted block. Tool results carry an untrusted notice. `protocol.md` tells agents never to run destructive commands, read token files, edit agent configs or run remote code because a room asked.
- Reviewers are read-only: the OpenCode `rooms-reviewer` agent denies `edit`, and denies `shell` except `git diff`, `git log`, `git show` and `git status` (redirects denied). Codex reviewers should run with `-s read-only`.
- The web UI renders room text with `textContent` and ships a strict CSP.
- The hooks run from read-only copies in `~/.config/agent-rooms/bin/`, outside the repos agents edit.

Residual risks (from [the security review](docs/reviews/security-review.md)):

- Agents run as your user and can read every token file, including the other agents'. Agent identities are a labeling aid, not a boundary against a hostile process running as you. The human token is the exception: it is not on disk.
- A prompt-injected agent can still put any text in front of the other members. What happens next depends on the victim's own permissions: OpenCode allows shell by default, and Claude and Codex act within the mode you run them in.
- Roles are self-asserted. A writer can post `done` and close a room.
- Room history is plain text in `data/rooms.db`. Do not paste secrets into rooms.
- Any local process under your user can reach `127.0.0.1:8765`; tokens are the only barrier.
- `enable-repo` makes a repo's `.opencode/` and `.agents/` point at your token files. Enable only repos you trust.
- The web UI keeps the human token in `localStorage`.
- On Linux the container user (uid 10001) must be able to read `config/agents.json`. Do not fix that with `chmod 644`; set `user:` in the compose file to your host uid instead.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Hooks and plugin do nothing, agents stop at once | The server is down. Hooks treat any connection error as "stop". Check `docker compose ps` and `curl -s http://127.0.0.1:8765/healthz`, then `docker compose up -d`. Hooks write one-line diagnostics to stderr (wrong token, HTTP status, unreachable server). |
| An agent shows "running stop hook" for minutes | It is listening, which is intended. Press Esc or Ctrl-C in that terminal, or click Pause in the UI (the hook returns immediately). Messages still waiting are not lost: call `read`. |
| Codex hook does not fire | Codex skips untrusted hooks silently. Run `python3 scripts/rooms_setup.py status` and look for `Codex hook trust: seeded`. If it is not, re-run `install`. Fallback: start `codex-rooms`, type `/hooks`, find the `Stop` hook from `rooms.config.toml` and trust it. Do not use `--dangerously-bypass-hook-trust`. |
| `agy mcp list` shows "No MCP servers configured" | `agy mcp list` reads only the global config. The workspace server in `<repo>/.agents/` still works when `agy` is started from the repo root. |
| OpenCode stops waking after a long idle | OpenCode unloads an idle project location, which would stop the plugin. `enable-repo` sets `experimental.location_idle_timeout` to 2 hours. After a longer wait, restart `opencode` in the repo. |
| OpenCode reviewer is not woken | Start `opencode` from the enabled repo itself. The plugin ignores other directories and child (subagent) sessions. |
| `docker compose up` fails: port 8765 is in use | Find the process with `lsof -nP -iTCP:8765 -sTCP:LISTEN`. Or change the host side of `ports:` to `127.0.0.1:<port>:8765` and re-run setup with `--url`, for example `python3 scripts/rooms_setup.py --url http://127.0.0.1:<port> install`. |
| `config/agents.json` became a directory | `docker compose up` ran before `tokens`. Stop it, delete the directory, run `tokens`, start again. |
| Rotated a token | Run `tokens --rotate <agent>`, then `install` (Claude, Codex), `enable-repo <repo>` for each repo that uses agy, restart `opencode`, and restart the server (`docker compose restart`) so it reloads `agents.json`. |
| Lost the human token | `python3 scripts/rooms_setup.py tokens --rotate human`, then `docker compose restart`. |
| Two sessions of one tool fight over messages | They share one token and no alias. Start the second with its own `ROOMS_ALIAS`. |
| Updated this repo and hooks look stale | `status` shows `stale` for a hook copy. Re-run `install` (and `enable-repo`). |
| `timeout: command not found` in a script | macOS has no `timeout`. Use `perl -e 'alarm N; exec @ARGV' <command>`. |

## Uninstall

```sh
python3 scripts/rooms_setup.py disable-repo <repo>      # for every repo you enabled, first
python3 scripts/rooms_setup.py uninstall                # add --purge-tokens to delete the token files too
docker compose down                                     # data stays in ./data
```

`disable-repo` removes only values identical to what `enable-repo` wrote and deletes a file only when nothing of yours is left in it. `uninstall` removes exactly the files recorded in `installed.json`, including the hook copies, so run `disable-repo` first or the repo hooks will point at deleted files. `config/agents.json` and `./data` are never deleted by the script.

## Development

```sh
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
node --test "integrations/opencode/agent-rooms/*.test.js"
```

Python 3.13 and `mcp==2.3.0`. Dependencies are managed with uv only (`uv add`, `uv add --dev`); `uv.lock` is committed. The hooks and `scripts/rooms_setup.py` use only the standard library and stay compatible with Python 3.9.

### Architecture map

Package `src/agent_rooms`, layers from the inside out:

| Layer | Path | Contents |
|---|---|---|
| domain | `domain/` | enums, entities, validation, `DomainError` types, role and loop-guard policies |
| application | `application/` | ports (`Protocol`s), `Limits`, DTOs, wake-prompt builder, long-poll helper, services `RoomAdmin`, `Membership`, `Messaging`, `Listening` in `services/` |
| infrastructure | `infrastructure/` | SQLite unit of work and schema (`sqlite/`), `JsonFileTokenRegistry`, clock |
| presentation | `presentation/` | Starlette REST routes, auth middleware and web UI (`http/`), MCP tools (`mcp/`), serializers |

Dependency rule, enforced by `tests/unit/test_architecture.py`: `domain` imports only the standard library; `application` imports `domain`; `infrastructure` imports `application` and `domain`; `presentation` imports `application` and `domain`, never `infrastructure`; only `bootstrap.py` and `__main__.py` import across layers.

| What | Where |
|---|---|
| Entry point | `python -m agent_rooms` (`__main__.py`, reads `Settings.from_env()`, runs uvicorn with one worker) |
| Composition root | `src/agent_rooms/bootstrap.py` |
| Env settings | `src/agent_rooms/settings.py` |
| Agent rules text returned by `join_room` | `src/agent_rooms/resources/protocol.md` |
| Stop hook | `hooks/room_hook.py`; agy auto-approve: `hooks/agy_approve_rooms.py` |
| OpenCode plugin | `integrations/opencode/agent-rooms/index.js` |
| Client setup | `scripts/rooms_setup.py` |
| Tests | `tests/{unit,integration,hooks,scripts}`, `tests/test_dockerfile.py`, `tests/test_packaging.py` |

### Documents

- [PLAN.md](PLAN.md): product goal and original plan (Uzbek summary at the top).
- [Design spec](docs/superpowers/specs/2026-10-27-agent-rooms-design.md): where it differs from `PLAN.md`, the spec wins. Section 11a lists the review-driven amendments.
- [Implementation plan](docs/superpowers/plans/2026-10-27-agent-rooms.md)
- [SPIKE.md](SPIKE.md): Phase 0 results table; evidence per agent in [docs/evidence/](docs/evidence/) (`spike-*.md`, `e2e-opencode.md`, `e2e-codex.md`).
- [Phase 0a research](docs/research/): MCP SDK, Claude and Codex, OpenCode and agy.
- Reviews: [code review](docs/reviews/code-review.md), [security review](docs/reviews/security-review.md).
- [integrations/snippets.md](integrations/snippets.md): the exact config each agent gets.
