import assert from "node:assert/strict";
import test from "node:test";
import {planChatMode} from "../../loopx/control_plane/collaboration/chat_mode.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const session = {agent_id: "codex", goal_id: "research", channel_id: "goal.research"};
const input: JsonObject = {
  session, origin: "web", operation: "start", native: {status: "absent"},
  settings: {agent_id: "lead", token_budget: 1000}, registered_agents: ["lead"],
  goal_active: true, execution_binding_valid: true,
};

test("execution is explicit and preserves native lifecycle/allowance", () => {
  assert.equal(planChatMode(input).enabled, true);
  assert.equal(planChatMode({...input, operation: "configure"}).enabled, false);
  assert.equal(planChatMode({...input, operation: "exit"}).enabled, false);
  assert.throws(() => planChatMode({...input, operation: "pause"}), /enabled/);
  assert.equal(planChatMode({...input, operation: "pause", session: {...session, loopx_mode: {enabled: true}}}).enabled, true);
  assert.throws(() => planChatMode({...input, native: {status: "paused"}}), /unfinished/);
  assert.equal(planChatMode({...input, operation: "resume", native: {status: "budgetLimited", tokensUsed: 999}}).enabled, true);
  assert.throws(() => planChatMode({...input, operation: "resume", native: {status: "paused", tokensUsed: 1000}}), /consumed/);
  assert.throws(() => planChatMode({...input, operation: "resume", native: {status: "active"}}), /paused/);
});

test("neither registration nor a role name grants execution", () => {
  for (const changes of [
    {origin: "external"}, {goal_active: false}, {execution_binding_valid: false},
    {registered_agents: []}, {settings: {agent_id: "lead", token_budget: 0}},
    {settings: {agent_id: "lead", token_budget: true}},
    {session: {...session, active_turn_id: "other"}},
    {session: {...session, session_mode: "attached_host"}},
    {session: {...session, channel_id: "manager"}},
  ]) assert.throws(() => planChatMode({...input, ...changes}));
});

test("all three delivery modes require the exact active execution turn", () => {
  const active = {...session, active_turn_id: "one", loopx_mode: {enabled: true}};
  const message: JsonObject = {...input, operation: "message", session: active,
    turn: {turn_id: "one", loopx_execution: true}};
  for (const delivery_mode of ["queue", "inbox", "steer"]) {
    assert.equal(planChatMode({...message, delivery_mode}).delivery_mode, delivery_mode);
  }
  assert.throws(() => planChatMode({...message, delivery_mode: "unknown"}));
  assert.throws(() => planChatMode({...message, delivery_mode: "queue", turn: {turn_id: "one"}}));
  assert.throws(() => planChatMode({...message, delivery_mode: "queue", turn: {turn_id: "other", loopx_execution: true}}));
  assert.throws(() => planChatMode({...message, delivery_mode: "queue", session: {...active, loopx_mode: {enabled: true, paused: true}}}));
});

const enabled = {...session, session_id: "origin", loopx_mode: {enabled: true, paused: false}};
const intent = {intent_id: "a".repeat(64), requester: {goal_id: "research", agent_id: "lead"},
  conversation: {session_id: "origin", turn_id: "lead-turn"}};
const wake: JsonObject = {...input, operation: "wake", origin: "host", session: enabled, intent,
  native: {status: "paused", tokensUsed: 400}};

test("a host wake reuses the resume facts and returns a typed outcome, never an owner change", () => {
  const admitted = planChatMode(wake);
  assert.equal(admitted.state, "admitted");
  assert.equal(admitted.reason, null);
  assert.equal(admitted.dispatch, "create");
  assert.deepEqual(admitted.settings, input.settings);
  // Terminal refusals: the intent is settled and nothing waits.
  const refused: [JsonObject, string][] = [
    [{goal_active: false}, "goal_stopped"],
    [{session: {...enabled, loopx_mode: {enabled: false}}}, "no_wake_owner"],
    [{session: {...enabled, status: "closed"}}, "no_wake_owner"],
    // Pinned: another conversation or coordinator of the requester is not the origin.
    [{session: {...enabled, session_id: "other"}}, "wake_identity_conflict"],
    [{session: {...enabled, session_id: "origin"}, intent: {...intent, requester: {goal_id: "other", agent_id: "lead"}}},
      "wake_identity_conflict"],
    [{settings: {agent_id: "other", token_budget: 1000}, registered_agents: ["other"]}, "wake_identity_conflict"],
    [{registered_agents: ["other"]}, "lead_unbound"],
    [{execution_binding_valid: false}, "binding_revoked"],
    [{native: {status: "complete", tokensUsed: 400}}, "native_goal_complete"],
    [{native: {status: "absent"}}, "native_goal_absent"],
  ];
  for (const [changes, reason] of refused) {
    assert.deepEqual(planChatMode({...wake, ...changes}), {operation: "wake", state: "refused", reason}, reason);
  }
  // Pending: the intent waits for a later tick; a wake never unpauses the lead.
  const pending: [JsonObject, string][] = [
    [{session: {...enabled, loopx_mode: {enabled: true, paused: true}}}, "lead_paused"],
    [{session: {...enabled, active_turn_id: "running"}}, "lead_turn_active"],
    [{native: {status: "active", tokensUsed: 400}}, "lead_turn_active"],
    [{native: {status: "paused", tokensUsed: 1000}}, "allowance_exhausted"],
    [{settings: {agent_id: "lead", token_budget: 0}}, "allowance_exhausted"],
    // An owner start that is still activating: native facts are not yet stable.
    [{session: {...enabled, active_turn_id: "start"}, native: {status: "absent"}}, "lead_turn_active"],
    [{session: {...enabled, active_turn_id: "start"}, native: {status: "complete", tokensUsed: 400}}, "lead_turn_active"],
    [{session: {...enabled, active_turn_id: "pausing", loopx_mode: {enabled: true, paused: true}}}, "lead_turn_active"],
  ];
  for (const [changes, reason] of pending) {
    assert.deepEqual(planChatMode({...wake, ...changes}), {operation: "wake", state: "pending", reason}, reason);
  }
  // The host origin is only for wake, and wake only for the host: an owner cannot request one.
  assert.throws(() => planChatMode({...input, origin: "host"}), /local managed/);
  assert.throws(() => planChatMode({...wake, origin: "web"}), /local managed/);
  assert.throws(() => planChatMode({...wake, origin: "external"}), /local managed/);
  for (const changes of [{channel_id: "manager"}, {channel_id: "manager.external.test"},
    {session_mode: "attached_host"}, {agent_id: "other"}]) {
    assert.deepEqual(planChatMode({...wake, session: {...enabled, ...changes}}),
      {operation: "wake", state: "refused", reason: "no_wake_owner"});
  }
});

test("only a provider-dispatched wake Turn is dispatch evidence; a queued or merely starting one is replayed", () => {
  const own = {turn_id: "wake-turn", loopx_execution: true, operation: "wake", intent_id: intent.intent_id};
  const queued = {...own, status: "queued", started_at: null};
  // Queued, not started: replay the same Turn; its own active id does not block it.
  const replay = planChatMode({...wake, wake_turn: queued, session: {...enabled, active_turn_id: "wake-turn"}});
  assert.equal(replay.state, "admitted");
  assert.equal(replay.dispatch, "replay");
  // The replay keeps every boundary of a new wake.
  const held: [JsonObject, string, string][] = [
    [{session: {...enabled, active_turn_id: "wake-turn", loopx_mode: {enabled: true, paused: true}}}, "pending", "lead_paused"],
    [{session: {...enabled, active_turn_id: "other"}}, "pending", "lead_turn_active"],
    [{session: {...enabled, loopx_mode: {enabled: false}}}, "refused", "no_wake_owner"],
    [{execution_binding_valid: false}, "refused", "binding_revoked"],
    [{goal_active: false}, "refused", "goal_stopped"],
  ];
  for (const [changes, state, reason] of held) {
    assert.deepEqual(planChatMode({...wake, wake_turn: queued, ...changes}), {operation: "wake", state, reason}, reason);
  }
  // The provider-start fact, in any later status and even after the mode changed.
  for (const status of ["starting", "running", "completed", "failed"]) {
    assert.deepEqual(planChatMode({...wake, wake_turn: {...own, status, upstream_turn_id: "upstream-1"},
      session: {...enabled, loopx_mode: {enabled: false}}}),
    {operation: "wake", state: "woken", reason: null, dispatch: "recorded"}, status);
  }
  // `started_at` lands before the provider is reached, so it is not dispatch
  // evidence: a Turn carrying it but no `upstream_turn_id` stays pending rather
  // than claiming a wake the provider never accepted.
  for (const status of ["starting", "failed", "completed"]) {
    assert.deepEqual(planChatMode({...wake, wake_turn: {...own, status, started_at: "2026-09-30T00:00:00Z"},
      session: {...enabled, loopx_mode: {enabled: false}}}),
    status === "starting"
      ? {operation: "wake", state: "pending", reason: "wake_dispatch_pending"}
      : {operation: "wake", state: "refused", reason: "wake_turn_ended_unstarted"}, status);
  }
  // Ended without a provider dispatch: its client id cannot admit another Turn.
  for (const status of ["interrupted", "failed", "timed_out", "completed"]) {
    assert.deepEqual(planChatMode({...wake, wake_turn: {...own, status, started_at: null}}),
      {operation: "wake", state: "refused", reason: "wake_turn_ended_unstarted"}, status);
  }
  // Accepted but not yet dispatched: still pending, so the next tick re-reads the fact.
  for (const status of ["interrupting", "completing"]) {
    assert.deepEqual(planChatMode({...wake, wake_turn: {...own, status, started_at: null}}),
      {operation: "wake", state: "pending", reason: "wake_dispatch_pending"}, status);
  }
  // A client id owned by another request is never claimed.
  for (const other of [{loopx_execution: false}, {operation: "resume"}, {intent_id: "b".repeat(64)}]) {
    assert.deepEqual(planChatMode({...wake, wake_turn: {...queued, ...other, upstream_turn_id: "upstream-1"}}),
      {operation: "wake", state: "refused", reason: "wake_identity_conflict"});
  }
  // An intent without its origin conversation cannot be decided by any conversation.
  assert.throws(() => planChatMode({...wake, intent: {...intent, conversation: null}}), /conversation/);
  assert.throws(() => planChatMode({...wake, intent: {...intent, intent_id: "short"}}), /intent/);
});
