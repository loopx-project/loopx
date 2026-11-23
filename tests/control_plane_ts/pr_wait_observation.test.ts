import assert from "node:assert/strict";
import test from "node:test";
import {planPrWaitObservations, PR_WAIT_OBSERVATION_REQUEST} from "../../loopx/control_plane/todos/pr_wait_observation.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const waiting = {todo_id: "todo_waiting", role: "agent", status: "deferred", task_class: "advancement_task",
  claimed_by: "agent-a", resume_when: "pr_merged:owner/repo#42"};
const request = {schema_version: PR_WAIT_OBSERVATION_REQUEST, agent_id: "agent-a",
  generated_at: "2026-10-01T01:00:00Z", items: [waiting], rollout_events: []};

test("full dependency facts select one repository-qualified poll without a queue row", () => {
  const plan = planPrWaitObservations({...request, items: [waiting, {...waiting, todo_id: "todo_duplicate"},
    {...waiting, todo_id: "todo_inherited", resume_when: "pr_merged:#42", task_repository: "git:github.com/owner/repo"},
    {...waiting, todo_id: "todo_done", status: "done"}, {...waiting, todo_id: "todo_foreign", claimed_by: "agent-b"},
    {...waiting, todo_id: "todo_archived", archive_state: "archived"},
    {...waiting, todo_id: "todo_excluded", excluded_agents: ["agent-a"]},
    {...waiting, todo_id: "todo_monitor", task_class: "continuous_monitor"},
    {...waiting, todo_id: "todo_missing_repo", resume_when: "pr_merged:#42"},
    {...waiting, todo_id: "todo_scheduled", resume_when: "resume_at:2026-11-01T00:00:00Z"}]});
  const targets = plan.targets as JsonObject[];
  assert.equal(targets.length, 1);
  assert.equal(targets[0].pr_ref, "owner/repo#42");
  assert.deepEqual(targets[0].todo_ids, ["todo_waiting", "todo_duplicate", "todo_inherited"]);
  assert.deepEqual(plan.unresolved, [{todo_id: "todo_missing_repo", resume_when: "pr_merged:#42"}]);
});

test("successful and failed probes persist one restart-safe 30 minute cadence", () => {
  for (const status of ["OPEN", "CLOSED", "FAILED"]) {
    const event = {event_kind: "validation", classification: "pr_wait_observation", status,
      recorded_at: "2026-10-01T00:40:00Z", code_refs: {pr_ref: "owner/repo#42"}};
    assert.deepEqual(planPrWaitObservations({...request, rollout_events: [event]}).targets, []);
    assert.equal((planPrWaitObservations({...request, generated_at: "2026-10-01T01:10:00Z",
      rollout_events: [event]}).targets as JsonObject[]).length, 1);
    for (const patch of [{code_refs: {pr_ref: "other/repo#42"}}, {classification: "other"},
      {recorded_at: "invalid"}, {recorded_at: "2026-10-01T02:00:00Z"}]) {
      assert.equal((planPrWaitObservations({...request, rollout_events: [{...event, ...patch}]}).targets as JsonObject[]).length, 1);
    }
  }
});

test("exact merged event stops future polls; wrong repository and queue disappearance do not resume", () => {
  const merge = {event_kind: "pr_merge", event_id: "merged-42", code_refs: {pr_ref: "owner/repo#42"}};
  assert.deepEqual(planPrWaitObservations({...request, rollout_events: [merge]}).targets, []);
  assert.equal((planPrWaitObservations({...request, rollout_events: [{...merge,
    code_refs: {pr_ref: "other/repo#42"}}]}).targets as JsonObject[]).length, 1);
});

test("bound batch is ordered by due time, so repeated wakes cannot starve later dependencies", () => {
  const items = Array.from({length: 7}, (_, i) => ({...waiting, todo_id: `todo_wait${i}`,
    resume_when: `pr_merged:owner/repo#${i + 1}`}));
  const first = planPrWaitObservations({...request, items});
  assert.equal((first.targets as JsonObject[]).length, 4);
  const events = (first.targets as JsonObject[]).map(target => ({event_kind: "validation",
    classification: "pr_wait_observation", recorded_at: request.generated_at, code_refs: {pr_ref: target.pr_ref}}));
  const next = planPrWaitObservations({...request, items, rollout_events: events});
  assert.equal((next.targets as JsonObject[]).length, 3);
});

test("blocked PR waits and transitive todo_done prerequisites are observed while preserving exclusions", () => {
  const blockedOwn = {...waiting, todo_id: "todo_blocked_own", status: "blocked", resume_when: "pr_merged:owner/repo#77"};
  const peerPrereq = {...waiting, todo_id: "todo_peer_prereq", status: "deferred", claimed_by: "agent-b",
    resume_when: "pr_merged:owner/repo#88"};
  const excludedPrereq = {...waiting, todo_id: "todo_excluded_prereq", status: "deferred", claimed_by: "agent-b",
    excluded_agents: ["agent-a"], resume_when: "pr_merged:owner/repo#99"};
  const ownBlockedOnPeer = {todo_id: "todo_own_downstream", role: "agent", status: "open",
    task_class: "advancement_task", claimed_by: "agent-a", resume_when: "todo_done:todo_peer_prereq"};
  const ownBlockedOnExcluded = {todo_id: "todo_own_excluded_downstream", role: "agent", status: "open",
    task_class: "advancement_task", claimed_by: "agent-a", resume_when: "todo_done:todo_excluded_prereq"};
  const unrelatedForeign = {...waiting, todo_id: "todo_unrelated_foreign", claimed_by: "agent-b",
    resume_when: "pr_merged:owner/repo#105"};
  const monitorPeerPrereq = {...waiting, todo_id: "todo_monitor_peer_prereq", status: "deferred",
    claimed_by: "agent-b", resume_when: "pr_merged:owner/repo#106"};
  const notYetDueMonitor = {todo_id: "todo_future_monitor", role: "agent", status: "open",
    task_class: "continuous_monitor", claimed_by: "agent-a", next_due_at: "2026-11-01T00:00:00Z",
    resume_when: "todo_done:todo_monitor_peer_prereq"};
  const monitorIntermediate = {todo_id: "todo_monitor_relay", role: "agent", status: "open",
    task_class: "continuous_monitor", claimed_by: "agent-b", next_due_at: "2026-11-01T00:00:00Z",
    resume_when: "todo_done:todo_monitor_peer_prereq"};
  const ownViaMonitorRelay = {todo_id: "todo_own_via_monitor", role: "agent", status: "open",
    task_class: "advancement_task", claimed_by: "agent-a", resume_when: "todo_done:todo_monitor_relay"};
  const cycleA = {todo_id: "todo_cycle_a", role: "agent", status: "open",
    task_class: "advancement_task", claimed_by: "agent-a", resume_when: "todo_done:todo_cycle_b"};
  const cycleB = {todo_id: "todo_cycle_b", role: "agent", status: "deferred",
    task_class: "advancement_task", claimed_by: "agent-b", resume_when: "todo_done:todo_cycle_a"};
  const plan = planPrWaitObservations({...request, items: [
    blockedOwn, peerPrereq, excludedPrereq, ownBlockedOnPeer, ownBlockedOnExcluded,
    unrelatedForeign, monitorPeerPrereq, notYetDueMonitor, monitorIntermediate, ownViaMonitorRelay,
    cycleA, cycleB,
  ]});
  const refs = (plan.targets as JsonObject[]).map(target => target.pr_ref);
  assert.deepEqual(refs, ["owner/repo#77", "owner/repo#88"]);
});
