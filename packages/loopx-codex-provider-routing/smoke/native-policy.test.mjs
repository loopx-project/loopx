import assert from "node:assert/strict";
import { test } from "node:test";
import { account, apiRole, decide, modelRoute, observerHealth, stewardRoute } from "../src/loopx_codex_provider_routing/native_policy.ts";

const now = 1000;
test("consumer API roles never make a working Agent API primary", () => {
  const policy = {enabled: false, slots: [], cooldown_seconds: 300, api_fallback: false, steward_api_role: "primary"};
  const native = {policy, active_slot: "a", api_active: false};
  assert.equal(apiRole(policy, "worker"), "disabled");
  assert.equal(apiRole({...policy, api_fallback: true}, "worker"), "fallback");
  assert.equal(apiRole(policy, "unrecognized-consumer"), "disabled");
  assert.equal(apiRole({...policy, api_fallback: true, steward_api_role: "corrupt-role"}, "steward"), "disabled");
  assert.equal(modelRoute({consumer: "worker", native, api_ready: true}).api, false);
  assert.equal(modelRoute({consumer: "steward", native, api_ready: true}).api, true);
  assert.equal(modelRoute({consumer: "steward", native, api_ready: false}).api, false);
  assert.equal(stewardRoute({now, primary: "antigravity", pool: ["antigravity", "codex"],
    current: "antigravity", native, api_ready: true, primary_capacity: null}).endpoint, "codex");
  assert.equal(stewardRoute({now, primary: "antigravity", pool: ["antigravity"],
    current: "antigravity", native, api_ready: true, primary_capacity: null}).endpoint, null);
  assert.equal(stewardRoute({now, primary: "antigravity", pool: ["antigravity", "codex"],
    current: "antigravity", native, api_ready: true, primary_capacity: null, blocked_primary: {corrupt: true}}).endpoint, null);
  for (const role of ["disabled", "fallback", "corrupt-role"]) {
    assert.equal(modelRoute({consumer: "steward", native: {...native, policy: {...policy, steward_api_role: role}}, api_ready: true}).api, false);
  }
  // Legacy fallback remains the account owner's already admitted route.
  const fallback = {...native, policy: {...policy, enabled: true, api_fallback: true, steward_api_role: "fallback"},
    api_active: true, login_matches: true, recovery_required: false, action: "api_active"};
  assert.equal(modelRoute({consumer: "steward", native: fallback, api_ready: true}).api, true);
  assert.equal(modelRoute({consumer: "steward", native: {...fallback, recovery_required: true}, api_ready: true}).api, false);
  assert.equal(modelRoute({consumer: "steward", native: {...fallback, policy: {...fallback.policy, steward_api_role: "disabled"}}, api_ready: true}).api, false);
});
test("observer health requires a held owner and a fresh matching build heartbeat", () => {
  const healthy = {now, owner_locked: true, build: "current",
    heartbeat: {checked_at: now, deadline_at: now + 75, next_check_at: now + 60,
      build: "current", phase: "waiting"}};
  assert.equal(observerHealth(healthy).state, "healthy");
  assert.equal(observerHealth({...healthy, owner_locked: false}).state, "stopped");
  assert.equal(observerHealth({...healthy, owner_locked: null}).state, "unavailable");
  assert.equal(observerHealth({...healthy, heartbeat: null}).state, "unverified");
  assert.equal(observerHealth({...healthy, now: now + 76}).state, "overdue");
  assert.equal(observerHealth({...healthy, heartbeat: {...healthy.heartbeat, build: "old"}}).state, "upgrade_required");
  assert.equal(observerHealth({...healthy, heartbeat: {...healthy.heartbeat, phase: "retrying"}}).state, "retrying");
  assert.equal(observerHealth({...healthy, heartbeat: {...healthy.heartbeat, checked_at: now + 6}}).state, "unverified");
  assert.equal(observerHealth({...healthy, heartbeat: {...healthy.heartbeat, deadline_at: Infinity}}).state, "unverified");
  const corrupt = observerHealth({...healthy, heartbeat: {...healthy.heartbeat,
    checked_at: "private marker detail", next_check_at: {private: "detail"}}});
  assert.equal(corrupt.state, "unverified");
  assert.equal(corrupt.checked_at, null);
  assert.equal(corrupt.next_check_at, null);
});
const sample = (slot, used = 10, extra = {}) => ({ slot, observed_at: now, status: "ok", usage: {
  rate_limit: { primary_window: { used_percent: used, reset_at: 2000 },
    secondary_window: { used_percent: 20, reset_at: 9000 } }, credits: { has_credits: false, balance: "0" }, ...extra,
} });
const input = overrides => ({ now, policy: { enabled: true, slots: ["a", "b", "c"], cooldown_seconds: 300 },
  active: "a", observations: [sample("a", 100), sample("b"), sample("c")],
  last_switch_at: null, recovery_required: false, login_matches: true, ...overrides });

test("only measured exhaustion switches; default-off preserves the selected account", () => {
  assert.equal(decide(input({})).target, "b");
  assert.equal(decide(input({ observations: [sample("a"), sample("b")] })).action, "ready");
  assert.equal(decide(input({ policy: { enabled: false, slots: ["b"], cooldown_seconds: 300 } })).action, "disabled");
  for (const status of ["unavailable", "auth_required"]) {
    assert.equal(decide(input({ observations: [{ slot: "a", observed_at: now, status }, sample("b")] })).target, null);
  }
});

test("automatic handoff prefers more remaining allowance over the next ring slot", () => {
  assert.equal(decide(input({ observations: [sample("a", 100), sample("b", 70), sample("c", 10)] })).target, "c");
});

test("the limiting reported window determines remaining allowance, not a short-window average", () => {
  const windows = (slot, primary, secondary) => ({ slot, observed_at: now, status: "ok", usage: {
    rateLimitsByLimitId: { codex: { primary: { usedPercent: primary, resetsAt: 2000 },
      ...(secondary == null ? {} : { secondary: { usedPercent: secondary, resetsAt: 9000 } }) } },
  } });
  // B has 99% short-window allowance but only 5% long-window allowance.
  // C's limiting window has 55% left, so it can sustain more subscription work.
  assert.equal(decide(input({ observations: [sample("a", 100), windows("b", 1, 95), windows("c", 20, 45)] })).target, "c");
  // A provider reporting only one valid window remains eligible on that window.
  assert.equal(decide(input({ observations: [sample("a", 100), windows("b", 20, 70), windows("c", 50, null)] })).target, "c");
});

test("equal remaining allowance preserves the ring after the active account", () => {
  const result = decide(input({ active: "b", observations: [sample("a", 20), sample("b", 100), sample("c", 20)] }));
  assert.equal(result.target, "c", "The tie follows C then A, not observation order or alphabetic order");
});

test("unknown, stale, future and disallowed quota never outrank a measured candidate", () => {
  for (const candidate of [
    { ...sample("c", 0), observed_at: now - 121 },
    { ...sample("c", 0), observed_at: now + 1 },
    { slot: "c", observed_at: now, status: "unavailable" },
    { slot: "c", observed_at: now, status: "ok", usage: { rate_limit: { primary_window: { used_percent: null } } } },
  ]) assert.equal(decide(input({ observations: [sample("a", 100), sample("b", 70), candidate] })).target, "b");
  assert.equal(decide(input({ policy: { enabled: true, slots: ["a", "b"], cooldown_seconds: 300 },
    observations: [sample("a", 100), sample("b", 70), sample("c", 0)] })).target, "b");
});

test("credit-funded availability does not fabricate remaining subscription allowance", () => {
  const creditFunded = sample("b", 100, { credits: { has_credits: true, balance: "20" } });
  assert.equal(decide(input({ observations: [sample("a", 100), creditFunded, sample("c", 99)] })).target, "c");
  assert.equal(decide(input({ observations: [sample("a", 100), creditFunded] })).target, "b",
    "Credits retain their existing availability semantics when no fuller subscription is eligible");
});

test("quota priority neither preempts an available account nor overrides a manual choice", () => {
  assert.equal(decide(input({ observations: [sample("a", 90), sample("b", 0), sample("c", 0)] })).action, "ready");
  assert.equal(decide(input({ manual_slot: "b", observations: [sample("a", 100), sample("b", 95), sample("c", 0)] })).target, "b");
});

test("credits, null and multi-bucket facts retain their meaning", () => {
  assert.equal(account(sample("a", 100, { credits: { has_credits: true, balance: "12.0" } }), now).availability, "available");
  assert.equal(account({ ...sample("a"), usage: {} }, now).availability, "unknown");
  assert.equal(account(sample("a", 100, { credits: null }), now).availability, "unknown");
  assert.equal(account({ ...sample("a"), usage: { rateLimitsByLimitId: { review: { primary: { usedPercent: 0 } } },
    rateLimits: { primary: { usedPercent: 5 } } } }, now).availability, "unknown");
  const native = { ...sample("a"), usage: { rateLimitsByLimitId: { codex: {
    primary: { usedPercent: 100, resetsAt: 2000 }, secondary: { usedPercent: 100, resetsAt: 9000 }, credits: { hasCredits: false },
  } } } };
  assert.equal(account(native, now).reset_at, 9000);
  assert.equal(account({ ...sample("a", 100), observed_at: 1 }, now).availability, "unknown");
  assert.equal(account(sample("a", 100), 2500).availability, "unknown");
});

test("allowlist, manual fence, cooldown and all-unavailable wait precede effects", () => {
  assert.equal(decide(input({ policy: { enabled: true, slots: ["a", "c"], cooldown_seconds: 300 } })).target, "c");
  assert.equal(decide(input({ login_matches: false })).action, "selection_changed");
  assert.equal(decide(input({ recovery_required: true })).action, "recovery_required");
  assert.equal(decide(input({ last_switch_at: 900 })).action, "cooldown");
  assert.equal(decide(input({ manual_slot: "outside" })).action, "target_unavailable");
  assert.equal(decide(input({ manual_slot: "b", observations: [sample("a"), {
    slot: "b", observed_at: now, status: "auth_required",
  }] })).target, "b", "Explicit selection can open native sign-in; automatic selection cannot");
  const unavailable = decide(input({ observations: [sample("a", 100), sample("b", 100), sample("c", 100)] }));
  assert.equal(unavailable.action, "all_unavailable");
  assert.equal(unavailable.retry_at, 1300);
  assert.equal(decide(input({ observations: [sample("a", 100), { ...sample("b"), observed_at: 0 }] })).target, null);
});

test("normal client restart needs its own permission and the quota proof expires before effects", () => {
  const allowed = { enabled: true, slots: ["a", "b"], cooldown_seconds: 300, restart_clients: true };
  const result = decide(input({ policy: allowed, observations: [sample("a", 100), { ...sample("b"), observed_at: 950 }] }));
  assert.equal(result.restart_clients, true);
  assert.equal(result.valid_until, 1070);
  assert.equal(decide(input({})).restart_clients, false, "Existing policies do not gain client shutdown authority");
  assert.equal(decide(input({ policy: allowed, observations: [sample("a"), sample("b")] })).restart_clients, false);
  assert.equal(decide(input({ policy: allowed, manual_slot: "b" })).valid_until, null,
    "An explicit sign-in choice is not an automatic quota decision");
});

test("API is opt-in and strictly after every measured subscription; recovery returns to subscriptions", () => {
  const api = {slot: "openai-api", ready: true};
  const enabled = {enabled: true, slots: ["a", "b", "c"], cooldown_seconds: 300, api_fallback: true};
  const exhausted = [sample("a", 100), sample("b", 100), sample("c", 100)];
  assert.equal(decide(input({policy: enabled, api})).target, "b", "API never outranks an available subscription");
  assert.equal(decide(input({policy: enabled, api, observations: exhausted})).target, api.slot);
  assert.equal(decide(input({api, observations: exhausted})).target, null, "Absent opt-in preserves subscription-only behavior");
  assert.equal(decide(input({policy: enabled, api, observations: [sample("a", 100), sample("b", 100), {...sample("c", 100), observed_at: now - 121}]})).target, null);
  assert.equal(decide(input({policy: enabled, api, active: api.slot, observations: exhausted})).action, "api_active");
  assert.equal(decide(input({policy: enabled, api, active: api.slot, observations: [sample("a", 80), sample("b", 20), sample("c", 100)]})).target, "b");
  const exhaustedApi = {...api, ready: false};
  assert.equal(decide(input({policy: enabled, api: exhaustedApi, active: api.slot,
    observations: [sample("a", 80), sample("b", 20), sample("c", 100)]})).target, "b",
  "An exhausted API pool returns to an available subscription");
  assert.equal(decide(input({policy: enabled, api: exhaustedApi, active: api.slot,
    observations: exhausted})).action, "all_unavailable",
  "An exhausted API pool is never projected as actively callable");
});
