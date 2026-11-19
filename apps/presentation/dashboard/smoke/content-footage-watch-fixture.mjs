// Actual React component; synthetic transport and a deterministic elapsed clock.
// This fixture cannot issue network requests or qualify a live model/provider.
import React from "react";
import {flushSync} from "react-dom";
import {createRoot} from "react-dom/client";
import {ContentGeneratedFootage} from "../src/features/personal-workspace/content-generated-footage";
import {WorkspaceI18nProvider} from "../src/features/personal-workspace/i18n";

const realTimeout = window.setTimeout.bind(window);
let now = 0, sequence = 0, root, goal = "watch-a", remoteStatus = "running", failRead = false;
let calls = [];
const timers = new Map();
const flush = async () => {
  await new Promise(resolve => realTimeout(resolve, 0));
  // Drain actual React commits/effects before moving the independent clock.
  flushSync(() => {});
};
const schedule = (callback, delay, period, args) => {
  const id = ++sequence;
  timers.set(id, {at: now + Math.max(0, Number(delay) || 0), period, callback, args});
  return id;
};
window.setTimeout = (callback, delay, ...args) => schedule(callback, delay, null, args);
window.setInterval = (callback, delay, ...args) => schedule(callback, delay, Math.max(1, Number(delay) || 1), args);
window.clearTimeout = window.clearInterval = id => timers.delete(id);
Object.defineProperty(performance, "now", {value: () => now});
localStorage.setItem("loopx-pw-locale", "en");

function library() {
  return {ok: true, provider: {status: "ready"}, generations: [{
    generation_id: "gen_watch", revision: 1, title: "A bounded observation", status: remoteStatus,
    seconds: 5, resolution: "720p", prompt: "Synthetic observation only", model: "synthetic",
    task_id: "cgt-synthetic-watch", error_code: null, estimate: {cny: 0},
    reported_usage_estimate_cny: null, quote: {source_url: "https://example.com", checked_at: "synthetic"},
    watermark_requested: true, references: [], result: null,
  }]};
}

window.fetch = async (_url, options) => {
  const body = JSON.parse(options.body);
  calls.push({action: body.action, goal: body.goal_id, at: now});
  if (body.action === "generation_check") {
    // Longer than the component's ten-second observation interval.
    await new Promise(resolve => setTimeout(resolve, 16000));
    return {ok: true, json: async () => ({ok: true})};
  }
  if (body.action === "generation_list") {
    if (failRead) return {ok: false};
    return {ok: true, json: async () => library()};
  }
  throw new Error("Unexpected effect in read-only observation fixture: " + body.action);
};

function mount() {
  root?.unmount();
  root = createRoot(document.getElementById("root"));
  root.render(React.createElement(WorkspaceI18nProvider, null,
    React.createElement(ContentGeneratedFootage, {goalId: goal})));
}

window.footageWatchFixture = {
  async reset() {
    root?.unmount(); root = null;
    timers.clear(); now = 0; calls = []; goal = "watch-a"; remoteStatus = "running"; failRead = false;
    mount(); await flush(); await flush();
  },
  async advance(milliseconds) {
    const end = now + milliseconds;
    let steps = 0;
    while (true) {
      const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      if (++steps > 2000) throw new Error("Synthetic timer budget exceeded");
      const [id, timer] = next;
      now = timer.at;
      if (timer.period === null) timers.delete(id); else timer.at += timer.period;
      timer.callback(...timer.args);
      await flush(); await flush();
    }
    now = end; await flush(); await flush();
  },
  async terminal() {remoteStatus = "succeeded";},
  async unreadable() {failRead = true;},
  async changeGoal() {goal = "watch-b"; mount(); await flush(); await flush();},
  async unmount() {root.unmount(); root = null; await flush();},
  read() {return {now, calls: [...calls], text: document.body.innerText};},
};
await window.footageWatchFixture.reset();
