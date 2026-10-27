import assert from "node:assert/strict";
import { test } from "node:test";

import defaultPlugin, { createPlugin } from "./index.js";

const DIR = "/work/repo";
const TOKEN = "tok-secret-123";
const SUCCEEDED = "session.execution.succeeded";
const BANNER =
  "[agent-rooms plugin] Delivered automatically by the agent-rooms plugin; not typed by the user.";
const framed = (prompt) => `${BANNER}\n\n${prompt}`;

function succeeded(sessionID) {
  return { type: SUCCEEDED, data: { sessionID } };
}

function response(status, { state, body, badJson } = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (name) => (name.toLowerCase() === "x-listen-state" ? (state ?? null) : null) },
    json: async () => {
      if (badJson) throw new SyntaxError("Unexpected token");
      return body;
    },
  };
}

const wake = (prompt = "room says hi") =>
  response(200, { body: { room: "r1", alias: "a", messages: [], prompt } });
const timeout = () => response(204, { state: "timeout" });
const idle = () => response(204, { state: "idle" });

function abortError() {
  const error = new Error("aborted");
  error.name = "AbortError";
  return error;
}

function hangUntilAbort(signal) {
  return new Promise((_, reject) => {
    if (signal.aborted) return reject(abortError());
    signal.addEventListener("abort", () => reject(abortError()), { once: true });
  });
}

function makeFetch(script = []) {
  const queue = [...script];
  const calls = [];
  const fetch = async (url, init = {}) => {
    calls.push({ url: new URL(url), init, signal: init.signal });
    if (queue.length === 0) return hangUntilAbort(init.signal);
    const next = queue.shift();
    return typeof next === "function" ? next(init) : next;
  };
  fetch.calls = calls;
  return fetch;
}

function makeEventStream(signal) {
  const items = [];
  const consumed = [];
  let waiting = null;
  let closed = false;
  const finish = () => {
    closed = true;
    if (waiting) waiting({ done: true, value: undefined });
  };
  signal?.addEventListener("abort", finish, { once: true });
  return {
    end: finish,
    push(event) {
      const handled = new Promise((resolve) => consumed.push({ event, resolve }));
      items.push(event);
      if (waiting) {
        const resolve = waiting;
        waiting = null;
        resolve({ done: false, value: items.shift() });
      }
      return handled;
    },
    iterable: {
      [Symbol.asyncIterator]() {
        return {
          async next() {
            for (const entry of consumed.splice(0)) entry.resolve();
            if (closed) return { done: true, value: undefined };
            if (items.length) return { done: false, value: items.shift() };
            return new Promise((resolve) => {
              waiting = resolve;
            });
          },
          async return() {
            return { done: true, value: undefined };
          },
        };
      },
    },
  };
}

function makeCtx({
  sessions = {},
  options = {},
  directory = DIR,
  getImpl,
  promptImpl,
  subscribeImpl,
} = {}) {
  const subscriptions = [];
  const stream = { current: null };
  const ctx = {
    options,
    location: { directory },
    event: {
      subscribe(args) {
        subscriptions.push(args);
        if (subscribeImpl) subscribeImpl(subscriptions.length);
        stream.current = makeEventStream(args?.signal);
        return stream.current.iterable;
      },
    },
    session: {
      getCalls: [],
      promptCalls: [],
      async get(args) {
        ctx.session.getCalls.push(args);
        if (getImpl) return getImpl(args);
        const found = sessions[args.sessionID];
        if (!found) throw new Error("not found");
        return found;
      },
      async prompt(args) {
        ctx.session.promptCalls.push(args);
        if (promptImpl) return promptImpl(args);
        return { id: "msg_1" };
      },
    },
  };
  return {
    ctx,
    subscriptions,
    push: (event) => stream.current.push(event),
    endStream: () => stream.current.end(),
  };
}

function build({
  script = [],
  readFile,
  env = {},
  homedir = () => "/home/me",
  now,
  sleep,
  ctxOptions,
  sessions = { ses_a: { location: { directory: DIR } } },
  getImpl,
  promptImpl,
  subscribeImpl,
  options = { url: "http://rooms.test:9999", tokenFile: "/tokens/opencode.token" },
} = {}) {
  const fetch = makeFetch(script);
  const logs = [];
  const readCalls = [];
  const plugin = createPlugin({
    fetch,
    readFile:
      readFile ??
      (async (path) => {
        readCalls.push(path);
        return `${TOKEN}\n`;
      }),
    env,
    homedir,
    now,
    sleep: sleep ?? (async () => {}),
    log: (message) => logs.push(message),
  });
  const harness = makeCtx({
    sessions,
    options: ctxOptions ?? options,
    getImpl,
    promptImpl,
    subscribeImpl,
  });
  const cleanup = plugin.setup(harness.ctx);
  return { ...harness, plugin, fetch, logs, readCalls, cleanup };
}

async function waitFor(predicate, message = "condition") {
  for (let i = 0; i < 500; i += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
  assert.fail(`timed out waiting for ${message}`);
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 10));

test("exports a plugin definition with id agent-rooms", () => {
  assert.equal(createPlugin({}).id, "agent-rooms");
  assert.equal(typeof createPlugin({}).setup, "function");
  assert.equal(defaultPlugin.id, "agent-rooms");
  assert.equal(typeof defaultPlugin.setup, "function");
});

test("succeeded event polls listen/next with session id and bearer token", async () => {
  const h = build({ script: [idle()] });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch");

  const call = h.fetch.calls[0];
  assert.equal(call.url.origin, "http://rooms.test:9999");
  assert.equal(call.url.pathname, "/api/listen/next");
  assert.equal(call.url.searchParams.get("session"), "ses_a");
  assert.equal(call.url.searchParams.get("timeout"), "540");
  assert.equal(call.url.searchParams.has("alias"), false);
  assert.equal(call.init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.ok(call.signal instanceof AbortSignal);
  h.cleanup();
});

test("subscribes once, with an abort signal", async () => {
  const h = build();
  await h.push(succeeded("ses_missing"));
  await h.push(succeeded("ses_missing"));
  assert.equal(h.subscriptions.length, 1);
  assert.ok(h.subscriptions[0].signal instanceof AbortSignal);
  h.cleanup();
});

test("200 prompts the same session once with the server prompt and ends the loop", async () => {
  const h = build({ script: [wake("hello from room")] });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.ctx.session.promptCalls.length === 1, "prompt");
  await settle();

  assert.deepEqual(h.ctx.session.promptCalls, [
    { sessionID: "ses_a", text: framed("hello from room") },
  ]);
  assert.equal(h.fetch.calls.length, 1);
  h.cleanup();
});

test("204 timeout polls again", async () => {
  const h = build({ script: [timeout(), wake("second poll")] });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.ctx.session.promptCalls.length === 1, "prompt after refetch");

  assert.equal(h.fetch.calls.length, 2);
  assert.equal(h.ctx.session.promptCalls[0].text, framed("second poll"));
  h.cleanup();
});

test("204 idle sends no prompt and does not poll again", async () => {
  const h = build({ script: [idle()] });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch");
  await settle();

  assert.equal(h.ctx.session.promptCalls.length, 0);
  assert.equal(h.fetch.calls.length, 1);
  h.cleanup();
});

test("alias option is forwarded as a query parameter", async () => {
  const h = build({
    script: [idle()],
    ctxOptions: { url: "http://rooms.test", tokenFile: "/t", alias: "oc-reviewer" },
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch");

  assert.equal(h.fetch.calls[0].url.searchParams.get("alias"), "oc-reviewer");
  h.cleanup();
});

test("other event types never fetch", async () => {
  const h = build({ script: [idle()] });
  for (const type of [
    "session.execution.interrupted",
    "session.execution.failed",
    "session.execution.started",
    "session.idle",
    "session.created",
  ]) {
    await h.push({ type, data: { sessionID: "ses_a" } });
  }
  await settle();

  assert.equal(h.fetch.calls.length, 0);
  assert.equal(h.ctx.session.getCalls.length, 0);
  h.cleanup();
});

test("events without a usable session id are ignored", async () => {
  const h = build();
  await h.push({ type: SUCCEEDED });
  await h.push({ type: SUCCEEDED, data: {} });
  await h.push(null);
  await settle();

  assert.equal(h.fetch.calls.length, 0);
  h.cleanup();
});

test("session in another directory never fetches", async () => {
  const h = build({ sessions: { ses_x: { location: { directory: "/other/repo" } } } });
  await h.push(succeeded("ses_x"));
  await settle();

  assert.equal(h.ctx.session.getCalls.length, 1);
  assert.equal(h.fetch.calls.length, 0);
  h.cleanup();
});

test("child session never fetches", async () => {
  const h = build({
    sessions: { ses_child: { location: { directory: DIR }, parentID: "ses_parent" } },
  });
  await h.push(succeeded("ses_child"));
  await settle();

  assert.equal(h.fetch.calls.length, 0);
  h.cleanup();
});

test("session.get rejecting does not throw and later events still work", async () => {
  const h = build({
    script: [idle()],
    sessions: { ses_a: { location: { directory: DIR } } },
  });
  await h.push(succeeded("ses_unknown"));
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch for the healthy session");

  assert.equal(h.fetch.calls[0].url.searchParams.get("session"), "ses_a");
  h.cleanup();
});

test("session.get returning nothing is skipped", async () => {
  const h = build({ getImpl: async () => undefined });
  await h.push(succeeded("ses_a"));
  await settle();
  assert.equal(h.fetch.calls.length, 0);
  h.cleanup();
});

test("fetch rejecting ends the loop quietly and a later event starts a new one", async () => {
  const h = build({
    script: [
      () => {
        throw new Error("ECONNREFUSED");
      },
      idle(),
    ],
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "failed fetch");
  await settle();
  assert.equal(h.ctx.session.promptCalls.length, 0);

  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 2, "second loop");
  h.cleanup();
});

test("non-2xx answer ends the loop without prompting", async () => {
  const h = build({ script: [response(401), wake("never")] });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch");
  await settle();

  assert.equal(h.fetch.calls.length, 1);
  assert.equal(h.ctx.session.promptCalls.length, 0);
  h.cleanup();
});

test("invalid JSON body ends the loop without prompting", async () => {
  const h = build({ script: [response(200, { badJson: true })] });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch");
  await settle();

  assert.equal(h.ctx.session.promptCalls.length, 0);
  h.cleanup();
});

test("200 without a prompt string ends the loop without prompting", async () => {
  const h = build({ script: [response(200, { body: { room: "r", messages: [] } })] });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch");
  await settle();

  assert.equal(h.ctx.session.promptCalls.length, 0);
  h.cleanup();
});

test("session.prompt rejecting does not throw and releases the session", async () => {
  const h = build({
    script: [wake("one"), wake("two")],
    promptImpl: async () => {
      throw new Error("session gone");
    },
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.ctx.session.promptCalls.length === 2, "first prompt and its retry");
  await settle();

  await h.push(succeeded("ses_a"));
  await waitFor(() => h.ctx.session.promptCalls.length === 4, "second prompt and its retry");
  h.cleanup();
});

test("every injected prompt starts with the plugin banner and a blank line", async () => {
  const h = build({ script: [wake("line one\nline two")] });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.ctx.session.promptCalls.length === 1, "prompt");

  const { text } = h.ctx.session.promptCalls[0];
  assert.ok(text.startsWith(`${BANNER}\n\n`));
  assert.equal(text.slice(BANNER.length + 2), "line one\nline two");
  h.cleanup();
});

test("a failed session.prompt is retried once after 1 s and the retry carries the same text", async () => {
  const sleeps = [];
  let attempt = 0;
  const h = build({
    script: [wake("retry me")],
    sleep: async (ms) => {
      sleeps.push(ms);
    },
    promptImpl: async () => {
      attempt += 1;
      if (attempt === 1) throw new Error("busy");
      return { id: "msg_2" };
    },
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.ctx.session.promptCalls.length === 2, "retry");
  await settle();

  assert.deepEqual(sleeps, [1000]);
  assert.equal(h.ctx.session.promptCalls.length, 2);
  assert.equal(h.ctx.session.promptCalls[1].text, h.ctx.session.promptCalls[0].text);
  assert.equal(h.logs.filter((line) => line.includes("failed")).length, 1);
  h.cleanup();
});

test("session.prompt is attempted at most twice per wake", async () => {
  const h = build({
    script: [wake("never lands")],
    promptImpl: async () => {
      throw new Error("gone");
    },
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.ctx.session.promptCalls.length === 2, "retry");
  await settle();

  assert.equal(h.ctx.session.promptCalls.length, 2);
  assert.ok(h.logs.every((line) => line.startsWith("[agent-rooms]")));
  h.cleanup();
});

test("cleanup during the prompt retry pause sends no second prompt", async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const h = build({
    script: [wake("paused")],
    sleep: async () => gate,
    promptImpl: async () => {
      throw new Error("busy");
    },
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.ctx.session.promptCalls.length === 1, "first attempt");
  h.cleanup();
  release();
  await settle();
  assert.equal(h.ctx.session.promptCalls.length, 1);
});

test("an event stream that ends is resubscribed after a 2 s backoff", async () => {
  const sleeps = [];
  const h = build({
    script: [idle()],
    sleep: async (ms) => {
      sleeps.push(ms);
    },
  });
  await h.push(succeeded("ses_missing"));
  assert.equal(h.subscriptions.length, 1);
  h.endStream();
  await waitFor(() => h.subscriptions.length === 2, "resubscription");

  assert.deepEqual(sleeps, [2000]);
  assert.ok(h.subscriptions[1].signal instanceof AbortSignal);
  assert.ok(h.logs.some((line) => line.includes("resubscrib")));
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "events flow again");
  h.cleanup();
});

test("a subscription that throws is retried with a 2 s backoff", async () => {
  const sleeps = [];
  const h = build({
    sleep: async (ms) => {
      sleeps.push(ms);
    },
    subscribeImpl: (count) => {
      if (count === 1) throw new Error("bus down");
    },
  });
  await waitFor(() => h.subscriptions.length === 2, "second subscription");

  assert.deepEqual(sleeps, [2000]);
  h.cleanup();
});

test("cleanup stops resubscribing", async () => {
  const h = build({ sleep: () => new Promise(() => {}) });
  h.cleanup();
  await settle();
  assert.equal(h.subscriptions.length, 1);
});

test("an abort during the backoff does not resubscribe", async () => {
  const h = build();
  h.endStream();
  h.cleanup();
  await settle();
  assert.equal(h.subscriptions.length, 1);
});

test("a second event for a session with a running loop starts no second loop", async () => {
  const h = build({ script: [] });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "first fetch");
  await h.push(succeeded("ses_a"));
  await settle();

  assert.equal(h.fetch.calls.length, 1);
  assert.equal(h.ctx.session.getCalls.length, 1);
  h.cleanup();
});

test("different sessions poll independently", async () => {
  const h = build({
    sessions: {
      ses_a: { location: { directory: DIR } },
      ses_b: { location: { directory: DIR } },
    },
  });
  await h.push(succeeded("ses_a"));
  await h.push(succeeded("ses_b"));
  await waitFor(() => h.fetch.calls.length === 2, "two fetches");

  const ids = h.fetch.calls.map((call) => call.url.searchParams.get("session")).sort();
  assert.deepEqual(ids, ["ses_a", "ses_b"]);
  h.cleanup();
});

test("a new turn end after a finished loop starts a new loop", async () => {
  const h = build({ script: [wake("first"), idle()] });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.ctx.session.promptCalls.length === 1, "prompt");
  await settle();

  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 2, "second loop fetch");
  h.cleanup();
});

test("the event iterator is not blocked by a long poll", async () => {
  const h = build({
    sessions: {
      ses_a: { location: { directory: DIR } },
      ses_b: { location: { directory: DIR } },
    },
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "first fetch hanging");
  await Promise.race([
    h.push(succeeded("ses_b")),
    new Promise((_, reject) => setTimeout(() => reject(new Error("iterator blocked")), 500)),
  ]);
  h.cleanup();
});

test("cleanup aborts the subscription and in-flight fetches", async () => {
  const h = build({
    sessions: {
      ses_a: { location: { directory: DIR } },
      ses_b: { location: { directory: DIR } },
    },
  });
  await h.push(succeeded("ses_a"));
  await h.push(succeeded("ses_b"));
  await waitFor(() => h.fetch.calls.length === 2, "two hanging fetches");
  assert.equal(typeof h.cleanup, "function");

  h.cleanup();

  assert.equal(h.subscriptions[0].signal.aborted, true);
  assert.ok(h.fetch.calls.every((call) => call.signal.aborted));
  await settle();
  assert.equal(h.ctx.session.promptCalls.length, 0);
});

test("cleanup stops a loop that would otherwise poll again", async () => {
  const h = build({ script: [timeout()], sleep: () => new Promise(() => {}) });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "first fetch");
  h.cleanup();
  await settle();
  assert.equal(h.fetch.calls.length, 1);
});

test("cleanup during an in-flight 200 handoff sends no prompt after abort", async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const h = build({ script: [async () => (await gate, wake("late"))] });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch");
  h.cleanup();
  release();
  await settle();
  assert.equal(h.ctx.session.promptCalls.length, 0);
});

test("the loop stops once the 65 minute cap is exceeded", async () => {
  let clock = 0;
  const h = build({
    script: [
      () => {
        clock += 66 * 60 * 1000;
        return timeout();
      },
      wake("too late"),
    ],
    now: () => clock,
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch");
  await settle();

  assert.equal(h.fetch.calls.length, 1);
  assert.equal(h.ctx.session.promptCalls.length, 0);
  h.cleanup();
});

test("the loop keeps polling while under the 65 minute cap", async () => {
  let clock = 0;
  const h = build({
    script: [
      () => {
        clock += 10 * 60 * 1000;
        return timeout();
      },
      wake("in time"),
    ],
    now: () => clock,
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.ctx.session.promptCalls.length === 1, "prompt");
  h.cleanup();
});

test("a timeout answer that returns instantly pauses before polling again", async () => {
  const sleeps = [];
  const h = build({
    script: [timeout(), wake("after pause")],
    now: () => 0,
    sleep: async (ms) => {
      sleeps.push(ms);
    },
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.ctx.session.promptCalls.length === 1, "prompt");

  assert.deepEqual(sleeps, [1000]);
  h.cleanup();
});

test("a timeout answer after a full poll does not pause", async () => {
  let clock = 0;
  const sleeps = [];
  const h = build({
    script: [
      () => {
        clock += 540_000;
        return timeout();
      },
      wake("no pause"),
    ],
    now: () => clock,
    sleep: async (ms) => {
      sleeps.push(ms);
    },
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.ctx.session.promptCalls.length === 1, "prompt");

  assert.deepEqual(sleeps, []);
  h.cleanup();
});

test("token is trimmed and read from the configured file", async () => {
  const h = build({
    script: [idle()],
    ctxOptions: { url: "http://rooms.test", tokenFile: "/tokens/custom.token" },
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch");

  assert.deepEqual(h.readCalls, ["/tokens/custom.token"]);
  assert.equal(h.fetch.calls[0].init.headers.Authorization, `Bearer ${TOKEN}`);
  h.cleanup();
});

test("token is re-read for every wake loop", async () => {
  let token = "first";
  const h = build({
    script: [idle(), idle()],
    readFile: async () => token,
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "first fetch");
  await settle();
  token = "rotated";
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 2, "second fetch");

  assert.equal(h.fetch.calls[0].init.headers.Authorization, "Bearer first");
  assert.equal(h.fetch.calls[1].init.headers.Authorization, "Bearer rotated");
  h.cleanup();
});

test("empty token stops the loop without fetching", async () => {
  const h = build({ readFile: async () => "  \n" });
  await h.push(succeeded("ses_a"));
  await settle();
  assert.equal(h.fetch.calls.length, 0);
  h.cleanup();
});

test("unreadable token file stops the loop without throwing", async () => {
  const h = build({
    readFile: async () => {
      throw new Error("ENOENT");
    },
  });
  await h.push(succeeded("ses_a"));
  await settle();
  assert.equal(h.fetch.calls.length, 0);
  h.cleanup();
});

test("options fall back to env and expand ~ in the token path", async () => {
  const h = build({
    script: [idle()],
    ctxOptions: {},
    env: {
      ROOMS_URL: "http://env.test:1234/",
      ROOMS_TOKEN_FILE: "~/tokens/env.token",
      ROOMS_ALIAS: "env-alias",
    },
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch");

  const { url } = h.fetch.calls[0];
  assert.equal(url.origin, "http://env.test:1234");
  assert.equal(url.pathname, "/api/listen/next");
  assert.equal(url.searchParams.get("alias"), "env-alias");
  assert.deepEqual(h.readCalls, ["/home/me/tokens/env.token"]);
  h.cleanup();
});

test("defaults: local URL and ~/.config/agent-rooms/opencode.token", async () => {
  const h = build({ script: [idle()], ctxOptions: {}, env: {} });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch");

  assert.equal(h.fetch.calls[0].url.origin, "http://127.0.0.1:8765");
  assert.deepEqual(h.readCalls, ["/home/me/.config/agent-rooms/opencode.token"]);
  h.cleanup();
});

test("ctx.options take precedence over env and a configured ~ is expanded", async () => {
  const h = build({
    script: [idle()],
    ctxOptions: { url: "http://opt.test", tokenFile: "~/opt.token", alias: "opt" },
    env: { ROOMS_URL: "http://env.test", ROOMS_TOKEN_FILE: "/env.token", ROOMS_ALIAS: "env" },
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch");

  assert.equal(h.fetch.calls[0].url.origin, "http://opt.test");
  assert.equal(h.fetch.calls[0].url.searchParams.get("alias"), "opt");
  assert.deepEqual(h.readCalls, ["/home/me/opt.token"]);
  h.cleanup();
});

test("the token is never logged", async () => {
  const h = build({
    script: [
      () => {
        throw new Error(`boom Bearer ${TOKEN}`);
      },
    ],
  });
  await h.push(succeeded("ses_a"));
  await waitFor(() => h.fetch.calls.length === 1, "fetch");
  await settle();

  assert.ok(h.logs.length > 0);
  assert.ok(h.logs.every((line) => line.startsWith("[agent-rooms]")));
  assert.ok(h.logs.every((line) => !line.includes(TOKEN)));
  h.cleanup();
});

test("a subscription that fails to start does not throw out of setup", async () => {
  const plugin = createPlugin({ fetch: makeFetch(), readFile: async () => TOKEN, env: {}, homedir: () => "/h", log: () => {} });
  const ctx = {
    options: {},
    location: { directory: DIR },
    event: {
      subscribe() {
        throw new Error("no bus");
      },
    },
    session: {},
  };
  let cleanup;
  assert.doesNotThrow(() => {
    cleanup = plugin.setup(ctx);
  });
  assert.equal(typeof cleanup, "function");
  assert.doesNotThrow(() => cleanup());
});
