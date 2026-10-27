import { readFile as fsReadFile } from "node:fs/promises";
import { homedir as osHomedir } from "node:os";
import { join } from "node:path";

const PLUGIN_ID = "agent-rooms";
const WAKE_EVENT = "session.execution.succeeded";
const DEFAULT_URL = "http://127.0.0.1:8765";
const DEFAULT_TOKEN_FILE = "~/.config/agent-rooms/opencode.token";
const POLL_TIMEOUT_SECONDS = 540;
const LOOP_CAP_MS = 65 * 60 * 1000;
const FAST_ANSWER_MS = 1000;
const FAST_ANSWER_PAUSE_MS = 1000;
const PROMPT_RETRY_MS = 1000;
const RESUBSCRIBE_BACKOFF_MS = 2000;
const PROMPT_BANNER =
  "[agent-rooms plugin] Delivered automatically by the agent-rooms plugin; not typed by the user.";

function expandHome(path, homedir) {
  if (path === "~") return homedir();
  if (path.startsWith("~/")) return join(homedir(), path.slice(2));
  return path;
}

function resolveOptions(options, env, homedir) {
  const given = options ?? {};
  const url = given.url || env.ROOMS_URL || DEFAULT_URL;
  const tokenFile = given.tokenFile || env.ROOMS_TOKEN_FILE || DEFAULT_TOKEN_FILE;
  return {
    url: String(url).replace(/\/+$/, ""),
    tokenFile: expandHome(String(tokenFile), homedir),
    alias: given.alias || env.ROOMS_ALIAS || "",
  };
}

async function isOwnTopLevelSession(ctx, sessionID) {
  const session = await ctx.session.get({ sessionID });
  if (!session || session.parentID) return false;
  return session.location?.directory === ctx.location.directory;
}

function listenUrl(options, sessionID) {
  const params = new URLSearchParams({ timeout: String(POLL_TIMEOUT_SECONDS), session: sessionID });
  if (options.alias) params.set("alias", options.alias);
  return `${options.url}/api/listen/next?${params}`;
}

async function listenOnce({ fetch, options, token, sessionID, signal, log }) {
  const response = await fetch(listenUrl(options, sessionID), {
    headers: { Authorization: `Bearer ${token}` },
    signal,
  });
  if (response.status === 204) {
    return response.headers.get("x-listen-state") === "timeout" ? { kind: "timeout" } : { kind: "stop" };
  }
  if (response.status !== 200) {
    log(`listen/next answered ${response.status}`);
    return { kind: "stop" };
  }
  const body = await response.json();
  if (typeof body?.prompt !== "string" || body.prompt === "") {
    log("listen/next answered 200 without a prompt");
    return { kind: "stop" };
  }
  return { kind: "message", prompt: body.prompt };
}

function redact(error, token) {
  const text = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return token ? text.split(token).join("***") : text;
}

function abortableSleep(ms, signal) {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done, { once: true });
  });
}

export function createPlugin({
  fetch,
  readFile,
  env = {},
  homedir = osHomedir,
  now = Date.now,
  sleep = abortableSleep,
  log: sink = console.error,
} = {}) {
  const log = (message) => {
    try {
      sink(`[${PLUGIN_ID}] ${message}`);
    } catch {}
  };

  async function readToken(path) {
    try {
      return String(await readFile(path, "utf8")).trim();
    } catch (error) {
      log(`cannot read token file: ${error?.code ?? error?.name ?? "error"}`);
      return "";
    }
  }

  async function listenLoop({ ctx, options, sessionID, signal }) {
    const token = await readToken(options.tokenFile);
    if (!token) return;
    const started = now();
    while (!signal.aborted) {
      const pollStarted = now();
      let result;
      try {
        result = await listenOnce({ fetch, options, token, sessionID, signal, log });
      } catch (error) {
        if (!signal.aborted) log(`listen/next failed: ${redact(error, token)}`);
        return;
      }
      if (result.kind === "message") {
        await wake(ctx, sessionID, result.prompt, signal, token);
        return;
      }
      if (result.kind !== "timeout") return;
      if (now() - started >= LOOP_CAP_MS) return;
      if (now() - pollStarted < FAST_ANSWER_MS) await sleep(FAST_ANSWER_PAUSE_MS, signal);
    }
  }

  async function wake(ctx, sessionID, text, signal, token) {
    if (signal.aborted) return;
    const framed = `${PROMPT_BANNER}\n\n${text}`;
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      try {
        await ctx.session.prompt({ sessionID, text: framed });
        return;
      } catch (error) {
        log(`session.prompt failed (attempt ${attempt}): ${redact(error, token)}`);
      }
      if (attempt === 1) {
        await sleep(PROMPT_RETRY_MS, signal);
        if (signal.aborted) return;
      }
    }
  }

  function setup(ctx) {
    const controller = new AbortController();
    const { signal } = controller;
    const options = resolveOptions(ctx.options, env, homedir);
    const running = new Set();

    async function onEvent(event) {
      if (event?.type !== WAKE_EVENT) return;
      const sessionID = event.data?.sessionID;
      if (typeof sessionID !== "string" || running.has(sessionID)) return;
      if (!(await isOwnTopLevelSession(ctx, sessionID))) return;
      if (signal.aborted) return;
      running.add(sessionID);
      listenLoop({ ctx, options, sessionID, signal })
        .catch((error) => log(`listen loop crashed: ${redact(error)}`))
        .finally(() => running.delete(sessionID));
    }

    async function consume() {
      while (!signal.aborted) {
        try {
          const stream = await ctx.event.subscribe({ signal });
          for await (const event of stream) {
            try {
              await onEvent(event);
            } catch (error) {
              if (!signal.aborted) log(`event handling failed: ${redact(error)}`);
            }
          }
          if (!signal.aborted) log("event stream ended; resubscribing");
        } catch (error) {
          if (!signal.aborted) log(`event subscription failed: ${redact(error)}; resubscribing`);
        }
        await sleep(RESUBSCRIBE_BACKOFF_MS, signal);
      }
    }

    consume().catch((error) => log(`event loop crashed: ${redact(error)}`));

    return () => controller.abort();
  }

  return { id: PLUGIN_ID, setup };
}

export default createPlugin({
  fetch: globalThis.fetch,
  readFile: fsReadFile,
  env: process.env,
  homedir: osHomedir,
});
