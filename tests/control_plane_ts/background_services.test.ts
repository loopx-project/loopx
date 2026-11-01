import assert from "node:assert/strict";
import test from "node:test";
import {projectBackgroundTask, projectBackgroundServices} from "../../loopx/control_plane/runtime/background_services.ts";

const now = 1000;
const worker = {alive: true, stop_requested: false, terminal: false, executing: true,
  started_at: 900, last_success_at: null, last_failure_at: null, next_check_at: null,
  deadline_at: 1100, failure_count: 0};
const project = (patch = {}) => projectBackgroundTask({available: true, enabled: true,
  worker: {...worker, ...patch}}, now);

test("live work is checking, successful scan is healthy, stalled live thread is overdue", () => {
  assert.equal(project().state, "checking");
  assert.equal(project({executing: false, last_success_at: 950, next_check_at: 1050}).state, "healthy");
  assert.equal(project({deadline_at: 999}).state, "overdue");
  assert.equal(project({deadline_at: null}).state, "unverified");
  assert.equal(project({last_success_at: 2000}).state, "unverified");
});

test("backoff, disabled, terminal completion and shutdown are distinct", () => {
  assert.equal(project({executing: false, failure_count: 1, last_failure_at: 980}).state, "retrying");
  assert.equal(project({alive: false, terminal: true, last_success_at: 950}).state, "completed");
  assert.equal(project({alive: false, terminal: true, failure_count: 1}).state, "stopped");
  assert.equal(project({stop_requested: true}).state, "stopping");
  assert.equal(projectBackgroundTask({available: true, enabled: false, worker}, now).state, "disabled");
});

test("provider election probe wins over the passive local contender, including unknown and stale evidence", () => {
  const input = {available: true, enabled: true, worker: {...worker, deadline_at: 999},
    probe: {state: "healthy" as const, checked_at: 990, next_check_at: 1050}};
  assert.equal(projectBackgroundTask(input, now).state, "healthy");
  assert.equal(projectBackgroundTask({...input, probe: {...input.probe, state: "overdue"}}, now).state, "overdue");
  assert.equal(projectBackgroundTask({...input, probe: {...input.probe, checked_at: null}}, now).state, "unverified");
});

test("RPC decoder rejects malformed facts and emits only content-free fields", () => {
  const task = {id: "synthetic", service_id: "fixture", available: true, enabled: true, worker,
    private_content: "must not escape"};
  const input = {now, supervisor: task, tasks: [task]};
  assert.equal(JSON.stringify(projectBackgroundServices(input)).includes("must not escape"), false);
  assert.throws(() => projectBackgroundServices({...input, tasks: [{...task, enabled: "true"}]}));
  assert.throws(() => projectBackgroundServices({...input, tasks: [{...task, worker: {...worker, alive: "true"}}]}));
});
