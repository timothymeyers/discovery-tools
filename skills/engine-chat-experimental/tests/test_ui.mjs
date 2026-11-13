import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const html = readFileSync(new URL("../assets/engine-chat/index.html", import.meta.url), "utf8");
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map((match) => match[1])
  .find((source) => source.includes("const POLL_INTERVAL_MS"));
assert.ok(script, "Engine Chat application script is present");

function loadRenderer() {
  const nodes = new Map();
  const document = {
    getElementById(id) {
      if (!nodes.has(id)) {
        nodes.set(id, {
          innerHTML: "",
          textContent: "",
          listeners: new Map(),
          addEventListener(type, callback) { this.listeners.set(type, callback); },
          setAttribute() {},
          querySelectorAll() { return []; },
          scrollTo() {},
        });
      }
      return nodes.get(id);
    },
  };
  const sandbox = {
    document,
    setInterval() {},
    requestAnimationFrame(callback) { callback(); },
    // Keep startup polling inert: these tests exercise rendering, not networking.
    fetch: () => new Promise(() => {}),
  };
  const instrumented = script.replace(/\}\)\(\);\s*$/, `
    globalThis.renderer = {
      state, renderFeed, renderSteeringMessages, turnStatus, actionState,
      activeAttention, effectiveRunStatus, updateHeaderStatus, updateActionRequired,
      reviewLatestUpdate, renderRunOptions, refreshSelectedRun,
    };
  })();`);
  vm.runInNewContext(instrumented, sandbox, { timeout: 1000 });
  assert.ok(sandbox.renderer, "Private rendering functions were exposed to the test harness");
  return { ...sandbox.renderer, nodes, setFetch(fetch) { sandbox.fetch = fetch; } };
}

const receivedAt = "2026-10-24T10:00:02+00:00";
const message = {
  id: "steering-2",
  text: "Did you assign and start agents for the leaf tasks?",
  queuedAt: "2026-10-24T10:00:01+00:00",
  receivedAt,
  delivery: "applied",
};

function interruptedRun() {
  return {
    run: {
      displayName: "Mission Control",
      instanceId: "abc123",
      shortId: "abc123",
      startedAt: "2026-10-24T10:00:00+00:00",
      updatedAt: "2026-10-24T10:00:03+00:00",
      stateLabel: "Working",
      isLive: true,
    },
    stats: { turns: 2, actions: 2 },
    queuedMessages: [],
    turns: [
      {
        id: "turn-1",
        number: 1,
        startedAt: "2026-10-24T10:00:01+00:00",
        endedAt: receivedAt,
        endReason: "steering",
        intro: "Waking up. Getting the lay of the land.",
        summary: "Reviewing meeting notes",
        queuedMessagesBefore: [],
        timeline: [{ id: "steering-received-2", type: "steering", summary: "Received steering message", startedAt: receivedAt }],
      },
      {
        id: "turn-2",
        number: 2,
        startedAt: "2026-10-24T10:00:03+00:00",
        endedAt: null,
        startReason: "steering",
        intro: "Gathering context after your steering message.",
        queuedMessagesBefore: [message],
        timeline: [{ id: "context-4", type: "group", groupKind: "context", summary: "Gathering context", status: "pending", actionCount: 2, children: [] }],
      },
    ],
  };
}

test("interrupted turn, received notice, user bubble, and new context turn are ordered", () => {
  const ui = loadRenderer();
  const data = interruptedRun();
  ui.state.runData = data;
  ui.renderFeed(data);
  const markup = ui.nodes.get("feed-inner").innerHTML;
  const notice = markup.indexOf("Received steering message");
  const firstTurnEnd = markup.indexOf("</details>", notice);
  const bubble = markup.indexOf('data-message-id="steering-2"');
  const nextTurn = markup.indexOf('data-open-key="abc123-turn-2"');
  const context = markup.indexOf('class="timeline-entry context-rollup"', nextTurn);
  assert.ok(notice >= 0 && firstTurnEnd > notice && bubble > firstTurnEnd);
  assert.ok(nextTurn > bubble && context > nextTurn);
  assert.match(markup, /· Interrupted/);
  assert.match(markup, /class="starting-prompt user-message" aria-label="Steering message"/);
  assert.equal(markup.split(message.text).length - 1, 1);
  assert.doesNotMatch(markup, /Communication applied|Turn interrupted by steering message — restarting/);
});

test("applied messages waiting for new activity are not labelled queued", () => {
  const ui = loadRenderer();
  const markup = ui.renderSteeringMessages([message], "after-interruption", true);
  assert.match(markup, /Steering message/);
  assert.match(markup, new RegExp(`datetime="${receivedAt.replaceAll("+", "\\+")}"`));
  assert.doesNotMatch(markup, /Queued message|user-message pending/);
});

test("pending queued messages retain their label and accent", () => {
  const ui = loadRenderer();
  const queued = { ...message, id: "queued-3", delivery: "queued", receivedAt: null };
  const markup = ui.renderSteeringMessages([message, queued], "mixed", true);
  assert.equal(markup.split('class="starting-prompt user-message pending"').length - 1, 1);
  assert.equal(markup.split('aria-label="Queued message"').length - 1, 1);
  assert.equal(markup.split('aria-label="Steering message"').length - 1, 1);
});

test("steering text remains escaped and preserves line breaks", () => {
  const ui = loadRenderer();
  const markup = ui.renderSteeringMessages([{ ...message, text: '<img src=x onerror=alert(1)> & "review"\nThen delegate.' }], "escaped");
  assert.match(markup, /&lt;img src=x onerror=alert\(1\)&gt; &amp; &quot;review&quot;\nThen delegate\./);
  assert.doesNotMatch(markup, /<img/);
});

test("interrupted actions and turns do not show completion or a spinner", () => {
  const ui = loadRenderer();
  const turn = interruptedRun().turns[0];
  assert.equal(ui.turnStatus({ ...turn, pendingActionCount: 1 }, true, { isLive: true }), "Interrupted");
  const action = ui.actionState({ type: "action", status: "interrupted" });
  assert.match(action, /aria-label="Interrupted by steering"/);
  assert.doesNotMatch(action, /action-spinner|Completed/);
  assert.doesNotMatch(ui.actionState({ type: "steering" }), /Completed|action-spinner/);
});

test("polling rerenders do not duplicate steering bubbles", () => {
  const ui = loadRenderer();
  const data = interruptedRun();
  ui.state.runData = data;
  ui.renderFeed(data);
  ui.renderFeed(data);
  const markup = ui.nodes.get("feed-inner").innerHTML;
  assert.equal(markup.split('data-message-id="steering-2"').length - 1, 1);
  assert.equal(markup.split('data-open-key="abc123-turn-2"').length - 1, 1);
});

function approvalRun() {
  return {
    run: {
      definitionId: "mission-control",
      displayName: "Mission Control",
      instanceId: "approval123",
      shortId: "approval1",
      state: "Idle",
      stateLabel: "Idle",
      isLive: false,
      version: "logs-live-tasks-1",
      startedAt: receivedAt,
      updatedAt: receivedAt,
      attention: {
        kind: "approval",
        label: "Needs your action",
        title: "1 task needs your review",
        description: "This turn is finished. Review the current tasks in Discovery, then approve or request changes.",
        excerpt: "DX-10 — Collect voice-of-customer evidence",
        source: "Current task board",
        tasks: [{ id: "DX-10", title: "Collect voice-of-customer evidence", status: "executionDone" }],
        turnId: "turn-1",
        entryId: "observation-3",
        requestedAt: receivedAt,
      },
    },
    stats: { turns: 1, actions: 0 },
    queuedMessages: [],
    turns: [{
      id: "turn-1", number: 1, startedAt: receivedAt, endedAt: receivedAt,
      intro: "Waking up. Getting the lay of the land.", summary: "Updated DX-2 progress",
      pendingActionCount: 0, failedActionCount: 0,
      timeline: [{
        id: "observation-3", type: "observation", summary: "Waiting for your approval.",
        detail: "The deliverables are ready. Review the output and follow-up notes in Discovery.",
        detailLabel: "Observation", startedAt: receivedAt,
      }],
    }],
  };
}

test("approval handoff is prominent and keeps the Idle lifecycle visible", () => {
  const ui = loadRenderer();
  const data = approvalRun();
  ui.state.runData = data;
  ui.state.runs = [data.run];
  ui.state.selectedKey = "mission-control/approval123";
  ui.renderFeed(data);
  ui.renderRunOptions();
  assert.equal(ui.nodes.get("status-label").textContent, "Needs your action");
  assert.equal(ui.nodes.get("status-dot").className, "status-dot needs-your-action");
  assert.equal(ui.nodes.get("engine-lifecycle").textContent, "Engine: Idle");
  assert.equal(ui.nodes.get("engine-lifecycle").hidden, false);
  assert.equal(ui.nodes.get("action-required").hidden, false);
  assert.equal(ui.nodes.get("composer-controls").hidden, true);
  assert.equal(ui.nodes.get("action-required-title").textContent, "1 task needs your review");
  assert.match(ui.nodes.get("action-required-source").textContent, /Current task board · checked live/);
  assert.match(ui.nodes.get("feed-inner").innerHTML, /class="turn-thread needs-action"/);
  assert.match(ui.nodes.get("feed-inner").innerHTML, /data-entry-id="observation-3"/);
  assert.match(ui.nodes.get("run-picker").innerHTML, /Needs your action \(Idle\)/);
  assert.equal(data.run.state, "Idle");
});

test("resolved approvals remove the banner and restore the disabled composer", () => {
  const ui = loadRenderer();
  const data = approvalRun();
  ui.state.runData = data;
  ui.renderFeed(data);
  data.run.attention = null;
  ui.renderFeed(data);
  assert.equal(ui.nodes.get("status-label").textContent, "Idle");
  assert.equal(ui.nodes.get("action-required").hidden, true);
  assert.equal(ui.nodes.get("engine-lifecycle").hidden, true);
  assert.equal(ui.nodes.get("composer-controls").hidden, false);
  assert.equal(ui.nodes.get("action-required-title").textContent, "");
  assert.doesNotMatch(ui.nodes.get("feed-inner").innerHTML, /needs-action/);
  assert.match(html, /<textarea disabled aria-label="Steering is disabled"/);
});

test("callout content uses text, never HTML from task titles or observations", () => {
  const ui = loadRenderer();
  const data = approvalRun();
  const untrusted = '<img src=x onerror=alert(1)> & "review"';
  data.run.attention.excerpt = untrusted;
  data.run.attention.title = untrusted;
  ui.state.runData = data;
  ui.renderFeed(data);
  assert.equal(ui.nodes.get("action-required-quote").textContent, untrusted);
  assert.equal(ui.nodes.get("action-required-quote").innerHTML, "");
  assert.equal(ui.nodes.get("action-required-title").innerHTML, "");
  assert.doesNotMatch(ui.nodes.get("feed-inner").innerHTML, /<img/);
  assert.match(ui.nodes.get("feed-inner").innerHTML, /&lt;img/);
});

test("review button expands the exact turn and observation and moves keyboard focus", () => {
  const ui = loadRenderer();
  ui.state.runData = approvalRun();
  let scrolled = false;
  let focused = false;
  const entry = {
    tagName: "DETAILS", open: false,
    dataset: { entryId: "observation-3", openKey: "approval123-turn-1-observation-3" },
    scrollIntoView(options) { scrolled = options.block === "start"; },
    querySelector(selector) {
      assert.equal(selector, "summary");
      return { focus(options) { focused = options.preventScroll; } };
    },
  };
  const turn = {
    tagName: "DETAILS", open: false, dataset: { openKey: "approval123-turn-1" },
    querySelectorAll() { return [entry]; },
  };
  ui.nodes.get("feed-inner").querySelectorAll = () => [turn, entry];
  ui.nodes.get("review-update-button").listeners.get("click")();
  assert.equal(turn.open, true);
  assert.equal(entry.open, true);
  assert.equal(scrolled, true);
  assert.equal(focused, true);
  assert.equal(ui.state.following, false);
  assert.ok(ui.state.openKeys.has(entry.dataset.openKey));
  assert.ok(ui.state.touchedKeys.has(turn.dataset.openKey));
  // Rendering replaces these synthetic DOM nodes; expansion survives in state.
  ui.nodes.get("feed-inner").querySelectorAll = () => [];
  ui.renderFeed(ui.state.runData);
  assert.match(ui.nodes.get("feed-inner").innerHTML, /data-open-key="approval123-turn-1-observation-3" open/);
});

test("review button also navigates a short observation without details", () => {
  const ui = loadRenderer();
  ui.state.runData = approvalRun();
  let tabIndex;
  let focused = false;
  const entry = {
    tagName: "DIV", dataset: { entryId: "observation-3" },
    scrollIntoView() {}, querySelector() { return null; },
    setAttribute(name, value) { if (name === "tabindex") tabIndex = value; },
    focus() { focused = true; },
  };
  const turn = {
    tagName: "DETAILS", dataset: { openKey: "approval123-turn-1" },
    querySelectorAll() { return [entry]; },
  };
  ui.nodes.get("feed-inner").querySelectorAll = () => [turn];
  ui.reviewLatestUpdate();
  assert.equal(tabIndex, "-1");
  assert.equal(focused, true);
});

test("sleep and terminal states cannot be overwritten by a stale handoff", () => {
  const ui = loadRenderer();
  const future = new Date(Date.now() + 600_000).toISOString();
  for (const [state, label] of [["Running", "Working"], ["Completed", "Complete"], ["Failed", "Needs attention"], ["Stopped", "Stopped"], ["Cancelled", "Cancelled"]]) {
    const data = approvalRun();
    data.run.state = state;
    data.run.stateLabel = label;
    if (state !== "Running") data.turns[0].sleep = { status: "scheduled", until: future };
    ui.updateActionRequired(data);
    assert.equal(ui.effectiveRunStatus(data), label);
    assert.equal(ui.nodes.get("action-required").hidden, true);
  }
  const sleeping = approvalRun();
  sleeping.turns[0].sleep = { status: "scheduled", until: future };
  ui.updateActionRequired(sleeping);
  assert.equal(ui.effectiveRunStatus(sleeping), "Sleeping");
  assert.equal(ui.nodes.get("action-required").hidden, true);
});

test("unfinished work, new turns, and queued steering never inherit a handoff", () => {
  const ui = loadRenderer();
  const cases = [
    (data) => { data.turns[0].endedAt = null; },
    (data) => { data.turns[0].pendingActionCount = 1; },
    (data) => { data.turns[0].failedActionCount = 1; },
    (data) => { data.turns[0].endReason = "steering"; },
    (data) => { data.turns[0].id = "turn-2"; },
    (data) => { data.queuedMessages = [message]; },
    (data) => { data.turns[0].sleep = { status: "pending" }; },
  ];
  for (const change of cases) {
    const data = approvalRun();
    change(data);
    assert.equal(ui.activeAttention(data), null);
    ui.updateActionRequired(data);
    assert.equal(ui.nodes.get("action-required").hidden, true);
  }
});

test("general input requests identify their historical observation source", () => {
  const ui = loadRenderer();
  const data = approvalRun();
  data.run.attention.source = "Latest observation";
  data.run.attention.title = "Waiting for your input";
  ui.updateActionRequired(data);
  assert.match(ui.nodes.get("action-required-source").textContent, /Latest observation/);
  assert.doesNotMatch(ui.nodes.get("action-required-source").textContent, /checked live/);
  ui.state.runData = null;
  ui.updateHeaderStatus();
  ui.updateActionRequired(null);
  assert.equal(ui.nodes.get("action-required").hidden, true);
  assert.equal(ui.nodes.get("status-label").textContent, "Connecting");
});

test("task-only version changes update the callout on polling", async () => {
  const ui = loadRenderer();
  const data = approvalRun();
  ui.state.selectedKey = "mission-control/approval123";
  ui.state.following = false;
  ui.setFetch(async () => ({ ok: true, json: async () => data }));
  await ui.refreshSelectedRun();
  assert.equal(ui.nodes.get("action-required").hidden, false);
  const resolved = approvalRun();
  resolved.run.version = "logs-live-tasks-2";
  resolved.run.attention = null;
  ui.setFetch(async () => ({ ok: true, json: async () => resolved }));
  await ui.refreshSelectedRun();
  assert.equal(ui.nodes.get("action-required").hidden, true);
  assert.equal(ui.nodes.get("status-label").textContent, "Idle");
});

test("a delayed response for another run cannot restore its approval banner", async () => {
  const ui = loadRenderer();
  ui.state.selectedKey = "mission-control/approval123";
  let resolve;
  ui.setFetch(() => new Promise((done) => { resolve = done; }));
  const pending = ui.refreshSelectedRun();
  ui.state.selectedKey = "mission-control/another-run";
  resolve({ ok: true, json: async () => approvalRun() });
  await pending;
  assert.equal(ui.state.runData, null);
  assert.equal(ui.state.version, null);
  assert.equal(ui.state.loading, false);
});