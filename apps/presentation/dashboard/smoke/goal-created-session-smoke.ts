import assert from "node:assert/strict";
import {goalCreatedManagedSession} from "../src/features/personal-workspace/goal-created-session";
import {preparedAcceptancePlan} from "../src/data/goal-acceptance-actions";
import {conversationChannelId, conversationContextKey, createChatSession, fetchChatHistory,
  readManagedTaskValidation, type ManagedTaskTurn, type TypedActionProposal} from "../src/data/chat";

const resources = {goal_id: "research", goal_instance_id: null, agent_id: "codex",
  managed_agent_id: "codex", channel_id: "agent.codex", session_id: "original-session"};
const proposal: TypedActionProposal = {schema_version: "loopx_chat_action_proposal_v1", proposal_id: "create-original",
  action_kind: "goal.create", status: "applied", summary: "Create the original work Session",
  normalized_parameters: {goal_id: "research", agent_id: "codex"}, context: {kind: "manager"},
  expected_state_fingerprint: "original-source", permission_classification: "durable_write",
  validation_evidence: ["Original Session identity was read back"], available_transitions: [], stale: null,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:01Z",
  receipt: {outcome: "goal_created", projection_verified: true, resource_ids: resources}};
const address = goalCreatedManagedSession(proposal)!;
assert.deepEqual(address, {target: {goalId: "research", agentId: "codex", sessionId: "original-session"}},
  "The recorded registered owner and explicit null lifetime retain the original Session");
assert.deepEqual(goalCreatedManagedSession({...proposal, receipt: {...proposal.receipt, resource_ids: {...resources, goal_instance_id: "explicit-instance"}}}), address);
assert.equal(goalCreatedManagedSession({...proposal, receipt: {resource_ids: {goal_id: "research", agent_id: "codex", session_id: "legacy"}}}), undefined,
  "Legacy receipts keep their original navigation without inventing managed identity");
for (const changed of [{goal_id: "another-goal"}, {managed_agent_id: null}, {managed_agent_id: ""}, {managed_agent_id: "another-worker"}, {session_id: ""},
  {channel_id: "goal.research"}, {goal_instance_id: undefined}, {goal_instance_id: ""}, {agent_id: "other-executor"}]) {
  assert.deepEqual(goalCreatedManagedSession({...proposal, receipt: {...proposal.receipt, resource_ids: {...resources, ...changed}}}), {invalid: true},
    "A marked but unreadable address is an error, never a legacy fallback");
}
const missingLifetime = {...resources} as Record<string, unknown>; delete missingLifetime.goal_instance_id;
assert.deepEqual(goalCreatedManagedSession({...proposal, receipt: {...proposal.receipt, resource_ids: missingLifetime}}), {invalid: true});
for (const changed of [{outcome: "other"}, {outcome: undefined}, {projection_verified: false}, {projection_verified: undefined}]) {
  assert.deepEqual(goalCreatedManagedSession({...proposal, receipt: {...proposal.receipt, ...changed}}), {invalid: true});
}
for (const changed of [{status: "preview_ready"}, {action_kind: "team.plan"}]) {
  assert.equal(goalCreatedManagedSession({...proposal, ...changed} as TypedActionProposal), undefined);
}

const target = address.target!, context = {kind: "managed" as const, ...target};
assert.equal(conversationChannelId(context), "agent.codex");
assert.equal(conversationContextKey(context), "managed/research/codex/original-session");
const originalFetch = globalThis.fetch, requests: string[] = [];
const session = {session_id: target.sessionId, goal_id: target.goalId, goal_instance_id: resources.goal_instance_id,
  managed_agent_id: target.agentId, channel_id: resources.channel_id, agent_id: "codex", adapter_kind: "codex_app_server",
  session_mode: "managed_runtime", status: "ready", active_turn_id: null, resumable: true, last_error_code: null,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", last_activity_at: "2026-01-01T00:00:00Z"};
let discoveryMode = "original", snapshotMode = "original";
try {
  globalThis.fetch = async (input, init) => {
    assert.equal(init?.method ?? "GET", "GET", "Reopening and retrying never create a Session or Turn");
    const url = new URL(String(input), "http://127.0.0.1");
    requests.push(url.pathname + url.search);
    if (url.pathname === "/api/chat/sessions") {
      assert.equal(url.searchParams.get("goal_id"), "research");
      assert.equal(url.searchParams.get("managed_agent_id"), "codex");
      const listedSession = discoveryMode === "changed" ? {...session, channel_id: "goal.research"} : session;
      return Response.json({ok: true, schema_version: "loopx_chat_session_list_v1", goal_id: "research",
        managed_agent_id: "codex", goal_instance_id: resources.goal_instance_id,
        sessions: discoveryMode === "missing" ? [] : [listedSession]});
    }
    assert.equal(url.pathname, "/api/chat/sessions/original-session");
    return Response.json({ok: true, schema_version: "loopx_chat_store_v1",
      session: snapshotMode === "changed" ? {...session, goal_instance_id: "replacement-instance"} : session,
      active_turn: null, messages: [{message_id: "original-answer", role: "agent", turn_id: null,
        text: "Original result 原始结果", created_at: session.created_at}]});
  };
  assert.equal((await createChatSession(context, "codex")).session_id, "original-session");
  await assert.rejects(() => createChatSession(context, "codex", "new"));
  const history = await fetchChatHistory({channelId: conversationContextKey(context), managed: target});
  assert.deepEqual(history.messages.map(row => [row.session_id, row.text]), [["original-session", "Original result 原始结果"]]);
  assert.equal((await fetchChatHistory({channelId: conversationContextKey(context), managed: target}, history)).sessions[0].session_id, "original-session");
  discoveryMode = "missing";
  await assert.rejects(() => createChatSession(context, "codex"), /no longer available/);
  discoveryMode = "changed";
  await assert.rejects(() => fetchChatHistory({channelId: conversationContextKey(context), managed: target}), /cannot verify/);
  discoveryMode = "original"; snapshotMode = "changed";
  const unavailable = await fetchChatHistory({channelId: conversationContextKey(context), managed: target});
  assert.deepEqual(unavailable.messages, [], "Changed lifetime cannot expose another Goal's transcript");
  assert.deepEqual(unavailable.unavailableSessionIds, ["original-session"]);
  snapshotMode = "original";
  assert.equal((await fetchChatHistory({channelId: conversationContextKey(context), managed: target}, unavailable)).messages[0].text, "Original result 原始结果");
  assert.ok(requests.every(path => path.startsWith("/api/chat/sessions")));
} finally {globalThis.fetch = originalFetch;}

const lineage = {goal_id: target.goalId, agent_id: target.agentId, todo_id: "cart-repair"};
const turn: ManagedTaskTurn = {session_id: target.sessionId, turn_id: "first-task", client_turn_id: "managed.first-task",
  status: "completed", origin: "native_loopx", loopx_execution: true, loopx_request: {
    operation: "native_turn", todo_id: lineage.todo_id,
    actor: {goal_id: target.goalId, agent_id: target.agentId, session_id: target.sessionId},
  }};
const postcondition = {schema_version: "loopx_turn_execution_v0", dry_run: false,
  validation_stage: "task_postcondition", receipt: {ok: true, lineage}};
const passingCheck = {schema_version: "loopx_turn_task_validation_v0", ok: true, status: "passed", summary: "Checks passed"};
const checkedTurn = {...turn, response: {native_loopx: {...postcondition, validation: passingCheck}}};
assert.equal(readManagedTaskValidation(checkedTurn)?.status, "passed");
assert.equal(readManagedTaskValidation({...turn, response: {native_loopx: {...postcondition,
  validation: {...passingCheck, ok: false, status: "failed", summary: "One required check failed"}}}})?.status, "failed",
  "A terminal conversation receipt can carry a failed independent check");
for (const native_loopx of [
  {...postcondition, dry_run: true, validation: passingCheck},
  {...postcondition, validation_stage: "host_result_contract", validation: passingCheck},
  {...postcondition, validation: {...passingCheck, ok: false}},
  {...postcondition, receipt: {ok: true, lineage: {...lineage, todo_id: "another-task"}}, validation: passingCheck},
]) {
  assert.equal(readManagedTaskValidation({...turn, response: {native_loopx}}), undefined,
    "Simulation, unrelated lineage and inconsistent receipts cannot certify the selected Task");
}
assert.equal(readManagedTaskValidation(turn), undefined, "Legacy responses cannot attest a check");
assert.equal(readManagedTaskValidation({...checkedTurn, status: "running"}), undefined);
const preparedCheck = (scope: Record<string, unknown>): TypedActionProposal => ({
  ...proposal, action_kind: "goal.acceptance.check", status: "preview_ready",
  context: {kind: "goal", goal_id: target.goalId},
  permission_classification: "local_command_execution", available_transitions: ["apply"],
  normalized_parameters: {goal_id: target.goalId, document: {
    objective: "Check the repair", scope,
    criteria: [{id: "completion", description: "Use the existing checker", validation_argv: ["loopx:python", "checks.py"]}],
  }},
});
const scoped = preparedCheck({kind: "selected_work", todo_ids: [lineage.todo_id]});
assert.equal(preparedAcceptancePlan([scoped], target.goalId, lineage.todo_id)?.proposal, scoped);
for (const scope of [{kind: "goal"}, {kind: "all_advancement"},
  {kind: "selected_work", todo_ids: ["another-task"]},
  {kind: "selected_work", todo_ids: [lineage.todo_id, "another-task"]}]) {
  const other = preparedCheck(scope);
  assert.equal(preparedAcceptancePlan([other], target.goalId, lineage.todo_id), null);
  assert.ok(preparedAcceptancePlan([other], target.goalId), "The regular Goal check remains available in Overview");
}
assert.equal(preparedAcceptancePlan([scoped,
  {...scoped, status: "applied", proposal_id: "confirmed-plan", created_at: "2026-01-01T00:00:02Z"}],
  target.goalId, lineage.todo_id), null, "A newer applied plan cannot make an earlier confirmation runnable again");

console.log("goal-created-session: original owner/history, replay, legacy parity and identity failure/recovery passed");
