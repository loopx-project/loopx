import assert from "node:assert/strict";
import test from "node:test";
import { projectPeerOrchestration } from "../../loopx/control_plane/quota/peer_orchestration.ts";

const task = (todo_id: string, extra = {}) => ({ todo_id, claimed_by: "worker",
  status: "open", task_class: "advancement_task", ...extra });
const input = { agent_id: "parent", registered_agents: ["parent", "worker"],
  available_capabilities: ["peer_agent_activation"], agents: [{ agent_id: "worker", state: "running" }] };

test("activation is scoped and preserves distinct tasks without inventing session binding", () => {
  const a = task("old"), b = task("new");
  const result = projectPeerOrchestration({ ...input, items: [a, b, a] })!;
  assert.equal(result.execution_scope, "peer_agent_activation");
  assert.equal(result.task_selection, "canonical_claimed_candidates");
  assert.equal(result.execution_state, "ready");
  assert.deepEqual((result.eligible_peer_lanes as any[]).map(row => row.todo_id), ["new", "old"]);
  assert.deepEqual(result, projectPeerOrchestration({ ...input, items: [b, a] }));
});

test("runtime, activation and dependency gates remain independently enforced", () => {
  const cases = [
    { available_capabilities: [], agents: input.agents, extra: {}, reasons: ["peer_agent_activation_unavailable"] },
    { available_capabilities: input.available_capabilities, agents: [], extra: {}, reasons: ["peer_liveness_unavailable"] },
    { available_capabilities: input.available_capabilities, agents: [{ agent_id: "worker", state: "running", stale_claim_hint: true }], extra: {}, reasons: ["peer_runtime_stale"] },
    { available_capabilities: input.available_capabilities, agents: [{ agent_id: "worker", state: "dormant" }], extra: {}, reasons: ["peer_runtime_not_active"] },
    // Liveness this machine cannot vouch for is not activation evidence.
    { available_capabilities: input.available_capabilities, agents: [{ agent_id: "worker", state: "unknown" }], extra: {}, reasons: ["peer_runtime_not_active"] },
    { available_capabilities: input.available_capabilities, agents: [{ agent_id: "worker" }], extra: {}, reasons: ["peer_runtime_not_active"] },
    { available_capabilities: input.available_capabilities, agents: input.agents, extra: { resume_when: "todo_done:dependency", resume_ready: false }, reasons: ["peer_lane_not_resume_ready"] },
  ];
  for (const row of cases) {
    const result = projectPeerOrchestration({ ...input, ...row, items: [task("task", row.extra)] })!;
    assert.equal(result.execution_state, "blocked");
    assert.deepEqual(result.eligible_peer_lanes, []);
    assert.deepEqual((result.blocked_peer_lanes as any[])[0].reason_codes, row.reasons);
  }
});

test("execution-backed and durable-work states are the only admitted states", () => {
  for (const state of ["executing", "bound", "launchable", "monitoring", "running"]) {
    const result = projectPeerOrchestration({ ...input, agents: [{ agent_id: "worker", state }], items: [task("task")] })!;
    assert.equal(result.execution_state, "ready", state);
  }
  for (const state of ["unknown", "registered", "addressable", "blocked", "waiting", "stale", "scope_wait"]) {
    const result = projectPeerOrchestration({ ...input, agents: [{ agent_id: "worker", state }], items: [task("task")] })!;
    assert.equal(result.execution_state, "blocked", state);
    assert.deepEqual((result.blocked_peer_lanes as any[])[0].reason_codes, ["peer_runtime_not_active"]);
  }
});

test("closed, deferred, unregistered, self and non-advancement rows never grant activation", () => {
  const items = [task("done", { done: true }), task("blocked", { status: "blocked" }),
    task("deferred", { status: "deferred" }), task("foreign", { claimed_by: "foreign" }),
    task("self", { claimed_by: "parent" }), task("monitor", { task_class: "continuous_monitor" }),
    task("unclaimed", { claimed_by: null }), task("")];
  assert.equal(projectPeerOrchestration({ ...input, items }), null);
  assert.equal(projectPeerOrchestration({ ...input, items: [] }), null);
});
