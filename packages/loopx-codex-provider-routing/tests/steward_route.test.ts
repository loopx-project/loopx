import test from "node:test";
import assert from "node:assert/strict";
import {stewardRoute} from "../src/loopx_codex_provider_routing/native_policy.ts";

const now = Date.parse("2026-01-01T00:00:00Z") / 1000;
const capacity = {observed_at: now, account_ref: "a".repeat(64), model_id: "fixture-argon",
  execution_id: "failed-execution", remaining_fraction: 0.001, reset_time: "2026-01-02T00:00:00Z",
  quota_exhausted: true, normal_completion: false};
const input = {now, primary: "antigravity", pool: ["antigravity", "codex"], current: "antigravity",
  primary_capacity: capacity, native: {policy: {enabled: true, slots: ["subscription-b"]},
    active_slot: "subscription-a", action: "ready", login_matches: true, recovery_required: false,
    accounts: [{slot: "subscription-a", observed_at: now, availability: "available"}]}};

test("steward endpoint order does not borrow the native account candidate ring", () => {
  const saved = structuredClone(input);
  assert.equal(stewardRoute(input).endpoint, "codex");
  assert.deepEqual(input, saved);
  assert.equal(stewardRoute({...input, primary_capacity: {...capacity,
    quota_exhausted: false, normal_completion: true, remaining_fraction: 0.6}}).endpoint, "antigravity");
  assert.equal(stewardRoute({...input, pool: ["subscription-a", "subscription-b"]}).endpoint, null);
});

test("positive remainder never erases an observed hard failure in the same quota epoch", () => {
  const blocked = stewardRoute(input).blocked_primary;
  assert.equal(stewardRoute({...input, current: "codex", blocked_primary: blocked,
    primary_capacity: {...capacity, remaining_fraction: 0.01}}).endpoint, "codex");
  assert.equal(stewardRoute({...input, current: "codex", blocked_primary: blocked,
    primary_capacity: {...capacity, reset_time: "2026-01-03T00:00:00Z", remaining_fraction: 0.9}}).endpoint, "antigravity");
  assert.equal(stewardRoute({...input, current: "codex", blocked_primary: blocked,
    primary_capacity: {...capacity, execution_id: "another-failed-turn", reset_time: "2026-01-03T00:00:00Z"}}).endpoint, "codex");
});

test("unknown, stale, unauthenticated allowance cannot authorize fallback", () => {
  for (const primary_capacity of [null, {...capacity, observed_at: now - 121},
    {...capacity, observed_at: now + 1}, {...capacity, remaining_fraction: NaN}]) {
    assert.equal(stewardRoute({...input, primary_capacity}).endpoint, null);
  }
  for (const native of [{...input.native, login_matches: false}, {...input.native, recovery_required: true},
    {...input.native, policy: {enabled: false, slots: []}}, {...input.native, accounts: []},
    {...input.native, accounts: [{slot: "subscription-a", observed_at: now - 121, availability: "available"}]}]) {
    assert.equal(stewardRoute({...input, native}).endpoint, null);
  }
});


test("invalid retained epochs and ambiguous observations stay unselected", () => {
  for (const blocked_primary of [{}, {...capacity, model_id: null}, {...capacity, reset_time: "invalid"}]) {
    assert.equal(stewardRoute({...input, blocked_primary}).endpoint, null);
  }
  for (const primary_capacity of [{...capacity, quota_exhausted: false}, {...capacity, normal_completion: true}]) {
    assert.equal(stewardRoute({...input, primary_capacity}).endpoint, null);
  }
  assert.equal(stewardRoute({...input, native: {...input.native, action: "switch"}}).endpoint, null);
  assert.equal(stewardRoute({...input, native: {...input.native, api_active: true,
    action: "api_active", policy: {...input.native.policy, api_fallback: true}}}).endpoint, "codex");
  assert.equal(stewardRoute({...input, native: {...input.native, api_active: true,
    action: "api_active", policy: {...input.native.policy, api_fallback: false}}}).endpoint, null);
});
