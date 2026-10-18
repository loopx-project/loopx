import assert from "node:assert/strict";
import { test } from "node:test";
import { account, decide } from "../src/loopx_codex_provider_routing/native_policy.ts";

const now = 1000;
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
