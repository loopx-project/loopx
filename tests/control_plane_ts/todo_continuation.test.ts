import assert from "node:assert/strict";
import {mkdtemp, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {spawnSync} from "node:child_process";
import test from "node:test";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {sqliteAuthorityRuntime} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {TODO_DOMAIN_ITEM_SCHEMA, TODO_DOMAIN_READ_RECORD_SCHEMA, TODO_DOMAIN_RECORD_CONTRACT} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {executeTodoContinuation} from "../../loopx/control_plane/coordination/todo_continuation.ts";
import {executeCoordinationTodoClaim, evaluateCoordinationTodoClaimDecision} from "../../loopx/control_plane/coordination/todo_claim.ts";
import {computeContinuationTodoFacts} from "../../loopx/control_plane/coordination/continuation_note.ts";
import {prepareCoordinationProjectionCommit, indexCoordinationProjection} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {engageLegacyCoordinationWriterFence, LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA, LEGACY_COORDINATION_WRITER_FENCE_SCHEMA} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {selectLocalSqliteAuthority, openLocalAuthorityStore} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {resolveTestPython} from "../../scripts/test-python.mjs";

const PYTHON = resolveTestPython();

async function fixture(t: test.TestContext, mode = "soft_claim") {
  const root = await mkdtemp(join(tmpdir(), "loopx-continuation-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const directory = join(root, "authority", "file-v0");
  const store = new FileAuthorityStore(directory, "goal-a");
  const todos = [{schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "todo_a", role: "agent",
    status: "open", done: false, text: "Validate the selected implementation", archive_state: "active", claimed_by: "agent-a"}];
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, events: [], receipts: [],
    next_projection: {goal_id: "goal-a", handoff_mode: mode, todos, leases: [], todo_read_model: {
      schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA, todo_count: 1,
      records_sha256: canonicalAuthoritySha256(todos), contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]}}});
  const base = {goal_id: "goal-a", todo_id: "todo_a", agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"],
    session_id: "source-session", workspace: root, artifacts: []};
  const inspect = () => executeTodoContinuation(store, {...base, action: "inspect"});
  const initial = await inspect();
  const prepare = {...base, action: "prepare", operation_id: "prepare-1", expected_provider_revision: initial.provider_revision,
    rationale: "The narrow path preserves the existing authority boundary", source_refs: ["artifact:decision.md"]};
  return {root, directory, store, base, inspect, prepare};
}

async function change(store: FileAuthorityStore, patch: Record<string, unknown>) {
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("missing fixture");
  const todo = (head.head.todos as Record<string, unknown>[])[0]!;
  return store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: "goal-a", operation_id: "change",
    expected_provider_revision: head.provider_revision, projection: head.head,
    mutations: [{kind: "todo_upsert", todo: {...todo, ...patch}}]}));
}

test("prepare survives restart; target uses current stable Todo and replayed claim readback", async t => {
  const f = await fixture(t);
  assert.equal((await f.inspect()).note_state, "missing");
  assert.equal((await executeTodoContinuation(f.store, f.prepare)).ok, true);
  // Lost prepare response: same operation returns its durable result.
  assert.equal((await executeTodoContinuation(f.store, f.prepare)).ok, true);
  const restarted = new FileAuthorityStore(f.directory, "goal-a");
  const packet = await executeTodoContinuation(restarted, {...f.base, action: "inspect", session_id: "target-session"});
  assert.equal(packet.can_adopt, true);
  assert.equal(packet.todo_id, "todo_a");
  assert.deepEqual((packet.evidence_refs as string[]).slice(2), ["artifact:decision.md"]);
  const adopt = {...f.base, action: "adopt", session_id: "target-session", operation_id: "adopt-1", expected_provider_revision: packet.provider_revision};
  const result = await executeTodoContinuation(restarted, adopt);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.current_authority_verified, true);
  assert.equal((await executeTodoContinuation(new FileAuthorityStore(f.directory, "goal-a"), adopt)).ok, true);
  const head = await restarted.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status === "loaded") assert.equal((head.head.todos as unknown[]).length, 1);
});

for (const [patch, code] of [
  [{status: "done", done: true}, "todo_not_open"],
  [{text: "Changed requirements"}, "continuation_not_ready"],
] as const) test(`changed current state rejects adoption: ${code}`, async t => {
  const f = await fixture(t);
  await executeTodoContinuation(f.store, f.prepare);
  const packet = await f.inspect();
  await change(f.store, patch);
  const result = await executeTodoContinuation(f.store, {...f.base, action: "adopt", session_id: "target",
    operation_id: "adopt", expected_provider_revision: packet.provider_revision});
  assert.equal(result.reason_code, code);
  assert.equal((await f.store.readReceipt("adopt")).status, "missing");
});

test("revision-only changes cannot use an old recommendation; missing artifacts and same session cannot adopt", async t => {
  const f = await fixture(t);
  await executeTodoContinuation(f.store, f.prepare);
  const packet = await f.inspect();
  await change(f.store, {updated_at: "2026-09-09T00:00:00Z"});
  const adopt = {...f.base, action: "adopt", session_id: "target", operation_id: "adopt", expected_provider_revision: packet.provider_revision};
  const stale = await executeTodoContinuation(f.store, adopt);
  assert.equal(stale.ok, false);
  assert.equal((stale.claim as Record<string, unknown>).reason_code, "provider_revision_mismatch");
  assert.equal((await executeTodoContinuation(f.store, {...adopt, artifacts: ["missing"]})).reason_code, "continuation_not_ready");
  assert.equal((await executeTodoContinuation(f.store, {...adopt, session_id: "source-session"})).reason_code, "continuation_not_ready");
});

test("failed pre-commit writes leave no handoff; ambiguous commit is recovered by the original operation", async t => {
  const f = await fixture(t);
  const original = f.store.commitAuthority.bind(f.store);
  f.store.commitAuthority = async () => ({status: "failed", reason_code: "test_write_failure", reason: "Synthetic failure"});
  assert.equal((await executeTodoContinuation(f.store, f.prepare)).ok, false);
  assert.equal((await f.inspect()).note_state, "missing");
  f.store.commitAuthority = async input => { await original(input); return {status: "ambiguous", reason_code: "test_lost_ack", reason: "Synthetic lost acknowledgment"}; };
  assert.equal((await executeTodoContinuation(f.store, f.prepare)).ok, true);
});

test("real Python CLI source/target processes use the file backend and do not auto-promote", async t => {
  const f = await fixture(t);
  const registry = join(f.root, "registry.json");
  await writeFile(registry, JSON.stringify({common_runtime_root: f.root, goals: [{id: "goal-a", repo: f.root,
    state_file: "ACTIVE_GOAL_STATE.md", coordination: {agent_model: "peer_v1", registered_agents: ["agent-a", "agent-b"]}}]}));
  const cli = (action: string, session: string, args: string[] = []) => {
    const run = spawnSync(PYTHON, ["-m", "loopx.cli", "--registry", registry, "--runtime-root", f.root, "--format", "json",
      "handoff", action, "--goal-id", "goal-a", "--todo-id", "todo_a", "--agent-id", "agent-a",
      "--session-id", session, "--workspace", f.root, ...args], {encoding: "utf8", env: {...process.env, PYTHONPATH: process.cwd()}, timeout: 30000});
    assert.equal(run.error, undefined);
    assert.ok(run.stdout.trim().startsWith("{"), run.stderr + run.stdout);
    return JSON.parse(run.stdout);
  };
  const unavailable = cli("inspect", "source");
  assert.equal(unavailable.reason_code, "canonical_authority_required", JSON.stringify(unavailable));
  // Isolated synthetic promoted state; no active owner goal is touched.
  const statePath = join(f.root, "ACTIVE_GOAL_STATE.md");
  await writeFile(statePath, "---\ngoal_id: goal-a\n---\n");
  const fenceResult = await engageLegacyCoordinationWriterFence({state_path: statePath, schema_version: LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
    runtime_root: f.root, goal_id: "goal-a", fence: {schema_version: LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
      state: "engaged", goal_id: "goal-a", fence_id: "fixture", source_version: "fixture",
      source_projection_sha256: "sha256:fixture", expected_shadow_provider_revision: "fixture"}});
  assert.equal(fenceResult.status, "applied", JSON.stringify(fenceResult));
  const source = cli("inspect", "source");
  const saved = cli("prepare", "source", ["--operation-id", "prepare-cli", "--expected-revision", source.provider_revision,
    "--rationale", "Preserve the current authority boundary", "--source-ref", "artifact:decision.md"]);
  assert.equal(saved.ok, true, JSON.stringify(saved));
  const target = cli("inspect", "target");
  assert.equal(target.can_adopt, true);
  const adopted = cli("adopt", "target", ["--operation-id", "adopt-cli", "--expected-revision", target.provider_revision]);
  assert.equal(adopted.ok, true, JSON.stringify(adopted));
  assert.equal(adopted.current_authority_verified, true);
});

function runCli(cliRoot: string, registry: string, action: string, session: string, args: string[] = []) {
  const run = spawnSync(PYTHON, ["-m", "loopx.cli", "--registry", registry, "--runtime-root", cliRoot, "--format", "json",
    "handoff", action, "--goal-id", "goal-a", "--todo-id", "todo_a", "--agent-id", "agent-a",
    "--session-id", session, "--workspace", cliRoot, ...args], {encoding: "utf8", env: {...process.env, PYTHONPATH: process.cwd()}, timeout: 30000});
  assert.equal(run.error, undefined, run.stderr);
  assert.ok(run.stdout.trim().startsWith("{"), run.stderr + run.stdout);
  return JSON.parse(run.stdout);
}

async function promotedCliFixture(t: test.TestContext) {
  const f = await fixture(t);
  const registry = join(f.root, "registry.json");
  await writeFile(registry, JSON.stringify({common_runtime_root: f.root, goals: [{id: "goal-a", repo: f.root,
    state_file: "ACTIVE_GOAL_STATE.md", coordination: {agent_model: "peer_v1", registered_agents: ["agent-a", "agent-b"]}}]}));
  const statePath = join(f.root, "ACTIVE_GOAL_STATE.md");
  await writeFile(statePath, "---\ngoal_id: goal-a\n---\n");
  const fenceResult = await engageLegacyCoordinationWriterFence({state_path: statePath, schema_version: LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
    runtime_root: f.root, goal_id: "goal-a", fence: {schema_version: LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
      state: "engaged", goal_id: "goal-a", fence_id: "fixture", source_version: "fixture",
      source_projection_sha256: "sha256:fixture", expected_shadow_provider_revision: "fixture"}});
  assert.equal(fenceResult.status, "applied", JSON.stringify(fenceResult));
  return {f, registry};
}

test("CLI prepare with unknown root key in --from-context is rejected", async t => {
  const {f, registry} = await promotedCliFixture(t);
  const contextPath = join(f.root, "bad-context.json");
  await writeFile(contextPath, JSON.stringify({work_summary: "test", unknown_field: "evil"}));
  const source = runCli(f.root, registry, "inspect", "source");
  const result = runCli(f.root, registry, "prepare", "source", ["--operation-id", "prepare-bad",
    "--expected-revision", source.provider_revision, "--from-context", contextPath]);
  assert.equal(result.ok, false, JSON.stringify(result));
  assert.equal(result.reason_code, "invalid_continuation_request", JSON.stringify(result));
  assert.match(result.reason, /unknown context field: unknown_field/, JSON.stringify(result));
});

test("CLI prepare with wrong type in --from-context is rejected", async t => {
  const {f, registry} = await promotedCliFixture(t);
  const contextPath = join(f.root, "bad-context.json");
  // next_steps must be an array, not a string.
  await writeFile(contextPath, JSON.stringify({work_summary: "test", next_steps: "just do it"}));
  const source = runCli(f.root, registry, "inspect", "source");
  const result = runCli(f.root, registry, "prepare", "source", ["--operation-id", "prepare-bad-type",
    "--expected-revision", source.provider_revision, "--from-context", contextPath]);
  assert.equal(result.ok, false, JSON.stringify(result));
  assert.equal(result.reason_code, "invalid_continuation_request", JSON.stringify(result));
  assert.match(result.reason, /next_steps must be an array/, JSON.stringify(result));
});

test("CLI prepare with overlong field in --from-context is rejected", async t => {
  const {f, registry} = await promotedCliFixture(t);
  const contextPath = join(f.root, "bad-context.json");
  const longSummary = "x".repeat(2001);
  await writeFile(contextPath, JSON.stringify({work_summary: longSummary}));
  const source = runCli(f.root, registry, "inspect", "source");
  const result = runCli(f.root, registry, "prepare", "source", ["--operation-id", "prepare-bad-length",
    "--expected-revision", source.provider_revision, "--from-context", contextPath]);
  assert.equal(result.ok, false, JSON.stringify(result));
  assert.equal(result.reason_code, "invalid_continuation_request", JSON.stringify(result));
  assert.match(result.reason, /work_summary must be non-empty text of at most 2000 characters/, JSON.stringify(result));
});

test("CLI prepare with invalid nested key in --from-context is rejected", async t => {
  const {f, registry} = await promotedCliFixture(t);
  const contextPath = join(f.root, "bad-context.json");
  await writeFile(contextPath, JSON.stringify({work_summary: "test",
    approaches_tried: [{approach: "x", outcome: "failed", reason: "y", extra: "bad"}]}));
  const source = runCli(f.root, registry, "inspect", "source");
  const result = runCli(f.root, registry, "prepare", "source", ["--operation-id", "prepare-bad-nested",
    "--expected-revision", source.provider_revision, "--from-context", contextPath]);
  assert.equal(result.ok, false, JSON.stringify(result));
  assert.equal(result.reason_code, "invalid_continuation_request", JSON.stringify(result));
  assert.match(result.reason, /approaches_tried must be an array of at most 20 objects with approach\/outcome\/reason/, JSON.stringify(result));
});

test("P1: --from-context with operational key action=adopt is rejected with no mutation", async t => {
  const {f, registry} = await promotedCliFixture(t);
  const contextPath = join(f.root, "bad-context.json");
  // Attacker tries to turn prepare into adopt via context.
  await writeFile(contextPath, JSON.stringify({work_summary: "test", action: "adopt", agent_id: "agent-b", target_agent_id: "agent-b"}));
  const source = runCli(f.root, registry, "inspect", "source");
  const revBefore = source.provider_revision;
  const result = runCli(f.root, registry, "prepare", "source", ["--operation-id", "prepare-p1-action",
    "--expected-revision", revBefore, "--from-context", contextPath]);
  assert.equal(result.ok, false, JSON.stringify(result));
  assert.equal(result.reason_code, "invalid_continuation_request", JSON.stringify(result));
  assert.match(result.reason, /unknown context field: (action|agent_id|target_agent_id)/, JSON.stringify(result));
  // Readback: provider revision, Todo note, and owner must be unchanged.
  const after = runCli(f.root, registry, "inspect", "source");
  assert.equal(after.provider_revision, revBefore, "provider revision must not advance on rejected prepare");
  assert.equal(after.note_state, source.note_state, "note_state must be unchanged");
});

test("P1: --from-context with operational key agent_id is rejected with no mutation", async t => {
  const {f, registry} = await promotedCliFixture(t);
  const contextPath = join(f.root, "bad-context.json");
  // Attacker tries to change the actor identity via context.
  await writeFile(contextPath, JSON.stringify({work_summary: "test", agent_id: "agent-b"}));
  const source = runCli(f.root, registry, "inspect", "source");
  const revBefore = source.provider_revision;
  const result = runCli(f.root, registry, "prepare", "source", ["--operation-id", "prepare-p1-agent",
    "--expected-revision", revBefore, "--from-context", contextPath]);
  assert.equal(result.ok, false, JSON.stringify(result));
  assert.equal(result.reason_code, "invalid_continuation_request", JSON.stringify(result));
  assert.match(result.reason, /unknown context field: agent_id/, JSON.stringify(result));
  const after = runCli(f.root, registry, "inspect", "source");
  assert.equal(after.provider_revision, revBefore, "provider revision must not advance");
});

test("P1: --from-context with scalar JSON is rejected", async t => {
  const {f, registry} = await promotedCliFixture(t);
  const contextPath = join(f.root, "bad-context.json");
  await writeFile(contextPath, JSON.stringify(42));
  const source = runCli(f.root, registry, "inspect", "source");
  const result = runCli(f.root, registry, "prepare", "source", ["--operation-id", "prepare-p1-scalar",
    "--expected-revision", source.provider_revision, "--from-context", contextPath]);
  assert.equal(result.ok, false, JSON.stringify(result));
  assert.match(result.reason, /must be a JSON object/, JSON.stringify(result));
});

test("P1: --from-context with array JSON is rejected", async t => {
  const {f, registry} = await promotedCliFixture(t);
  const contextPath = join(f.root, "bad-context.json");
  await writeFile(contextPath, JSON.stringify(["work_summary", "test"]));
  const source = runCli(f.root, registry, "inspect", "source");
  const result = runCli(f.root, registry, "prepare", "source", ["--operation-id", "prepare-p1-array",
    "--expected-revision", source.provider_revision, "--from-context", contextPath]);
  assert.equal(result.ok, false, JSON.stringify(result));
  assert.match(result.reason, /must be a JSON object/, JSON.stringify(result));
});

test("P1: --from-context with goal_id is rejected", async t => {
  const {f, registry} = await promotedCliFixture(t);
  const contextPath = join(f.root, "bad-context.json");
  await writeFile(contextPath, JSON.stringify({work_summary: "test", goal_id: "malicious"}));
  const source = runCli(f.root, registry, "inspect", "source");
  const result = runCli(f.root, registry, "prepare", "source", ["--operation-id", "prepare-p1-goal",
    "--expected-revision", source.provider_revision, "--from-context", contextPath]);
  assert.equal(result.ok, false, JSON.stringify(result));
  assert.match(result.reason, /unknown context field: goal_id/, JSON.stringify(result));
});

test("CLI --format digest renders readable handoff summary", async t => {
  const f = await fixture(t);
  const registry = join(f.root, "registry.json");
  await writeFile(registry, JSON.stringify({common_runtime_root: f.root, goals: [{id: "goal-a", repo: f.root,
    state_file: "ACTIVE_GOAL_STATE.md", coordination: {agent_model: "peer_v1", registered_agents: ["agent-a", "agent-b"]}}]}));
  const cli = (action: string, session: string, args: string[] = [], format = "json", agentId = "agent-a") => {
    // The 'digest' format is a subcommand-specific flag; pass it after the action.
    const formatArgs = format === "digest" ? ["--format", "digest"] : [];
    const run = spawnSync(PYTHON, ["-m", "loopx.cli", "--registry", registry, "--runtime-root", f.root,
      "--format", "json", "handoff", action, "--goal-id", "goal-a", "--todo-id", "todo_a",
      "--agent-id", agentId, "--session-id", session, "--workspace", f.root, ...formatArgs, ...args],
      {encoding: "utf8", env: {...process.env, PYTHONPATH: process.cwd()}, timeout: 30000});
    assert.equal(run.error, undefined, run.stderr);
    return run;
  };
  // Promote the file authority.
  const statePath = join(f.root, "ACTIVE_GOAL_STATE.md");
  await writeFile(statePath, "---\ngoal_id: goal-a\n---\n");
  const fenceResult = await engageLegacyCoordinationWriterFence({state_path: statePath, schema_version: LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
    runtime_root: f.root, goal_id: "goal-a", fence: {schema_version: LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
      state: "engaged", goal_id: "goal-a", fence_id: "fixture", source_version: "fixture",
      source_projection_sha256: "sha256:fixture", expected_shadow_provider_revision: "fixture"}});
  assert.equal(fenceResult.status, "applied", JSON.stringify(fenceResult));
  // Prepare a rich context note via the TypeScript layer (CLI --from-context is
  // tested through the digest output below).
  const initial = await f.inspect();
  await executeTodoContinuation(f.store, {...f.base, action: "prepare", operation_id: "prepare-digest",
    expected_provider_revision: initial.provider_revision,
    work_summary: "Implementing cross-agent handoff with typed transfer grant.",
    approaches_tried: [{approach: "Unstructured note", outcome: "failed", reason: "Can be forged"}],
    next_steps: ["Add validation", "Add tests"],
    files_touched: [{path: "todo_claim.ts", action: "edited", summary: "Added validateContinuationNote"}],
  });
  // Inspect with digest format. Use agent-a (registered) but different session.
  const target = cli("inspect", "target-session", [], "digest");
  assert.ok(target.stdout.includes("Handoff Context"), "digest output: " + target.stdout);
  assert.ok(target.stdout.includes("Implementing cross-agent handoff"), "digest output: " + target.stdout);
  assert.ok(target.stdout.includes("Unstructured note"), "digest output: " + target.stdout);
  assert.ok(target.stdout.includes("Add validation"), "digest output: " + target.stdout);
  assert.ok(target.stdout.includes("todo_claim.ts"), "digest output: " + target.stdout);
});

test("cross-agent handoff: agent-a prepares, agent-b adopts", async t => {
  const f = await fixture(t);
  await executeTodoContinuation(f.store, f.prepare);
  const packet = await executeTodoContinuation(f.store, {...f.base, action: "inspect", session_id: "target-session", agent_id: "agent-b"});
  assert.equal(packet.can_adopt, true);
  assert.equal(packet.claimed_by, "agent-a");
  const adopt = {...f.base, action: "adopt", session_id: "target-session", agent_id: "agent-b",
    target_agent_id: "agent-b", operation_id: "adopt-cross", expected_provider_revision: packet.provider_revision};
  const result = await executeTodoContinuation(f.store, adopt);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.current_authority_verified, true);
  assert.equal(result.target_agent_id, "agent-b");
  const head = await f.store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status === "loaded") {
    const todo = (head.head.todos as Record<string, unknown>[]).find(t => t.todo_id === "todo_a");
    assert.equal(todo?.claimed_by, "agent-b");
  }
});

function sqliteSkipReason(): string | undefined {
  try { sqliteAuthorityRuntime(); return undefined; }
  catch { return "requires a WAL-fixed SQLite runtime with finalized statements"; }
}

test("cross-agent handoff works with SQLite authority", {skip: sqliteSkipReason()}, async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-continuation-sqlite-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  // Set up SQLite authority
  await selectLocalSqliteAuthority(root, "goal-a", true);
  const store = await openLocalAuthorityStore(root, "goal-a");
  assert.ok(store instanceof SqliteAuthorityStore);
  // Seed a Todo
  const todos = [{schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "todo_a", role: "agent",
    status: "open", done: false, text: "Validate SQLite handoff", archive_state: "active", claimed_by: "agent-a"}];
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, events: [], receipts: [],
    next_projection: {goal_id: "goal-a", handoff_mode: "soft_claim", todos, leases: [], todo_read_model: {
      schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA, todo_count: 1,
      records_sha256: canonicalAuthoritySha256(todos), contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]}}});
  const base = {goal_id: "goal-a", todo_id: "todo_a", agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"],
    session_id: "source-session", workspace: root, artifacts: []};
  // Prepare
  const inspect1 = await executeTodoContinuation(store, {...base, action: "inspect"});
  const prepare = {...base, action: "prepare", operation_id: "prepare-1", expected_provider_revision: inspect1.provider_revision,
    rationale: "SQLite handoff test", source_refs: ["artifact:decision.md"]};
  const prepResult = await executeTodoContinuation(store, prepare);
  assert.equal(prepResult.ok, true, JSON.stringify(prepResult));
  // Inspect as target
  const packet = await executeTodoContinuation(store, {...base, action: "inspect", session_id: "target-session", agent_id: "agent-b"});
  assert.equal(packet.can_adopt, true);
  assert.equal(packet.claimed_by, "agent-a");
  // Adopt
  const adopt = {...base, action: "adopt", session_id: "target-session", agent_id: "agent-b",
    target_agent_id: "agent-b", operation_id: "adopt-cross", expected_provider_revision: packet.provider_revision};
  const result = await executeTodoContinuation(store, adopt);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.current_authority_verified, true);
  assert.equal(result.target_agent_id, "agent-b");
  // Verify ownership transfer
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status === "loaded") {
    const projection = indexCoordinationProjection(head.head, "goal-a");
    const todo = projection.todos.get("todo_a");
    assert.equal(todo?.claimed_by, "agent-b");
  }
});

test("cross-agent handoff rejects unregistered target agent", async t => {
  const f = await fixture(t);
  await executeTodoContinuation(f.store, f.prepare);
  const packet = await executeTodoContinuation(f.store, {...f.base, action: "inspect", session_id: "target-session", agent_id: "agent-b"});
  assert.equal(packet.can_adopt, true);
  const adopt = {...f.base, action: "adopt", session_id: "target-session", agent_id: "agent-b",
    target_agent_id: "agent-unregistered", operation_id: "adopt-bad", expected_provider_revision: packet.provider_revision};
  const result = await executeTodoContinuation(f.store, adopt);
  assert.equal(result.ok, false);
  assert.equal(result.reason_code, "target_agent_not_registered");
});

test("regular claim without transfer grant is rejected for cross-agent transfer", async t => {
  const f = await fixture(t);
  // agent-b tries to directly claim agent-a's Todo without going through
  // the handoff flow (no prepare/inspect/adopt, no transfer grant).
  // This must be rejected with claim_owner_mismatch.
  const result = await executeCoordinationTodoClaim(f.store, {
    goal_id: "goal-a", todo_id: "todo_a", claimed_by: "agent-b", actor_agent_id: "agent-b",
    expected_role: "agent", registered_agents: ["agent-a", "agent-b"],
    operation_id: "direct-claim-cross", dry_run: false, now: new Date(),
  });
  assert.equal(result.status, "failed", JSON.stringify(result));
  assert.equal(result.reason_code, "claim_owner_mismatch", JSON.stringify(result));
  assert.equal(result.failure_kind, "decision_rejection", JSON.stringify(result));
  // Verify zero ownership side effects: owner must remain agent-a.
  const head = await f.store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status === "loaded") {
    const todo = (head.head.todos as Record<string, unknown>[]).find(t => t.todo_id === "todo_a");
    assert.equal(todo?.claimed_by, "agent-a");
  }
});

test("free-text note without grant returns claim_owner_mismatch, not protocol failure", async t => {
  // Regression: a Todo with a plain-text (non-JSON) note must not trigger
  // JSON.parse when no transfer grant is present. Without a grant, the claim
  // must stably return claim_owner_mismatch / decision_rejection.
  const root = await mkdtemp(join(tmpdir(), "loopx-continuation-plainnote-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const directory = join(root, "authority", "file-v0");
  const store = new FileAuthorityStore(directory, "goal-a");
  const todos = [{schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "todo_a", role: "agent",
    status: "open", done: false, text: "Validate the selected implementation",
    note: "ordinary note", archive_state: "active", claimed_by: "agent-a"}];
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, events: [], receipts: [],
    next_projection: {goal_id: "goal-a", handoff_mode: "soft_claim", todos, leases: [], todo_read_model: {
      schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA, todo_count: 1,
      records_sha256: canonicalAuthoritySha256(todos), contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]}}});
  const result = await executeCoordinationTodoClaim(store, {
    goal_id: "goal-a", todo_id: "todo_a", claimed_by: "agent-b", actor_agent_id: "agent-b",
    expected_role: "agent", registered_agents: ["agent-a", "agent-b"],
    operation_id: "direct-claim-plainnote", dry_run: false, now: new Date(),
  });
  assert.equal(result.status, "failed", JSON.stringify(result));
  assert.equal(result.reason_code, "claim_owner_mismatch", JSON.stringify(result));
  assert.equal(result.failure_kind, "decision_rejection", JSON.stringify(result));
  // Verify zero ownership side effects: owner must remain agent-a.
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status === "loaded") {
    const todo = (head.head.todos as Record<string, unknown>[]).find(t => t.todo_id === "todo_a");
    assert.equal(todo?.claimed_by, "agent-a");
  }
});

test("arbitrary JSON note with matching grant hash is rejected as claim_owner_mismatch", async t => {
  // Regression: the final claim authority must enforce the typed
  // loopx-explicit-continuation invariant, not just note-hash equality.
  // An arbitrary JSON note (wrong marker, no todo_facts/rationale/source_refs)
  // with a matching continuation_note_facts hash must be rejected with
  // claim_owner_mismatch and produce zero ownership side effects.
  const root = await mkdtemp(join(tmpdir(), "loopx-continuation-arbitrary-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const directory = join(root, "authority", "file-v0");
  const store = new FileAuthorityStore(directory, "goal-a");
  const arbitraryNote = JSON.stringify({kind: "ordinary-json-note", foo: "bar", nested: {x: 1}});
  const todos = [{schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "todo_a", role: "agent",
    status: "open", done: false, text: "Validate the selected implementation",
    note: arbitraryNote, archive_state: "active", claimed_by: "agent-a"}];
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, events: [], receipts: [],
    next_projection: {goal_id: "goal-a", handoff_mode: "soft_claim", todos, leases: [], todo_read_model: {
      schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA, todo_count: 1,
      records_sha256: canonicalAuthoritySha256(todos), contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]}}});
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("missing fixture");
  // Construct a transfer grant with all correct bindings (source, target,
  // todo, revision) and a continuation_note_facts that matches the
  // arbitrary note's canonical hash. Before the fix, this would be
  // accepted because the final decision only checked hash equality.
  const arbitraryNoteFacts = canonicalAuthoritySha256(JSON.parse(arbitraryNote));
  const result = await executeCoordinationTodoClaim(store, {
    goal_id: "goal-a", todo_id: "todo_a", claimed_by: "agent-b", actor_agent_id: "agent-b",
    expected_role: "agent", registered_agents: ["agent-a", "agent-b"],
    operation_id: "arbitrary-note-claim", dry_run: false, now: new Date(),
    expected_provider_revision: head.provider_revision,
    transfer_grant: {
      schema_version: "todo_transfer_grant_v0",
      source_agent_id: "agent-a",
      target_agent_id: "agent-b",
      todo_id: "todo_a",
      expected_revision: head.provider_revision,
      continuation_note_facts: arbitraryNoteFacts,
    },
  });
  assert.equal(result.status, "failed", JSON.stringify(result));
  assert.equal(result.reason_code, "claim_owner_mismatch", JSON.stringify(result));
  assert.equal(result.failure_kind, "decision_rejection", JSON.stringify(result));
  // Verify zero ownership side effects: owner must remain agent-a, no receipt.
  const after = await store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status === "loaded") {
    const todo = (after.head.todos as Record<string, unknown>[]).find(t => t.todo_id === "todo_a");
    assert.equal(todo?.claimed_by, "agent-a");
  }
  const receipt = await store.readReceipt("arbitrary-note-claim");
  assert.equal(receipt.status, "missing", "no receipt must be written for a rejected transfer");
});

test("continuation marker with stale todo_facts and matching hash is rejected", async t => {
  // Regression: a note that has the correct loopx-explicit-continuation
  // marker but stale todo_facts (Todo changed after note was prepared)
  // must be rejected even when the grant's continuation_note_facts
  // matches the note's canonical hash.
  const root = await mkdtemp(join(tmpdir(), "loopx-continuation-stale-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const directory = join(root, "authority", "file-v0");
  const store = new FileAuthorityStore(directory, "goal-a");
  // Note claims todo_facts = "stale-facts" which won't match current Todo.
  const staleNote = JSON.stringify({kind: "loopx-explicit-continuation", source_session: "src",
    rationale: "decision", source_refs: ["ref"], todo_facts: "stale-facts"});
  const todos = [{schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "todo_a", role: "agent",
    status: "open", done: false, text: "Validate the selected implementation",
    note: staleNote, archive_state: "active", claimed_by: "agent-a"}];
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, events: [], receipts: [],
    next_projection: {goal_id: "goal-a", handoff_mode: "soft_claim", todos, leases: [], todo_read_model: {
      schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA, todo_count: 1,
      records_sha256: canonicalAuthoritySha256(todos), contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]}}});
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("missing fixture");
  const staleNoteFacts = canonicalAuthoritySha256(JSON.parse(staleNote));
  const result = await executeCoordinationTodoClaim(store, {
    goal_id: "goal-a", todo_id: "todo_a", claimed_by: "agent-b", actor_agent_id: "agent-b",
    expected_role: "agent", registered_agents: ["agent-a", "agent-b"],
    operation_id: "stale-note-claim", dry_run: false, now: new Date(),
    expected_provider_revision: head.provider_revision,
    transfer_grant: {
      schema_version: "todo_transfer_grant_v0",
      source_agent_id: "agent-a",
      target_agent_id: "agent-b",
      todo_id: "todo_a",
      expected_revision: head.provider_revision,
      continuation_note_facts: staleNoteFacts,
    },
  });
  assert.equal(result.status, "failed", JSON.stringify(result));
  assert.equal(result.reason_code, "claim_owner_mismatch", JSON.stringify(result));
  assert.equal(result.failure_kind, "decision_rejection", JSON.stringify(result));
  const after = await store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status === "loaded") {
    const todo = (after.head.todos as Record<string, unknown>[]).find(t => t.todo_id === "todo_a");
    assert.equal(todo?.claimed_by, "agent-a");
  }
  const receipt = await store.readReceipt("stale-note-claim");
  assert.equal(receipt.status, "missing", "no receipt must be written for a rejected transfer");
});

test("same-agent adoption still works (backward compatible)", async t => {
  const f = await fixture(t);
  await executeTodoContinuation(f.store, f.prepare);
  const packet = await executeTodoContinuation(f.store, {...f.base, action: "inspect", session_id: "target-session"});
  assert.equal(packet.can_adopt, true);
  const adopt = {...f.base, action: "adopt", session_id: "target-session", operation_id: "adopt-same",
    expected_provider_revision: packet.provider_revision};
  const result = await executeTodoContinuation(f.store, adopt);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.current_authority_verified, true);
  assert.equal(result.target_agent_id, "agent-a");
});

test("unknown claim outcome is recovered, but a later owner change invalidates historical success", async t => {
  const f = await fixture(t);
  await executeTodoContinuation(f.store, f.prepare);
  const packet = await f.inspect();
  const original = f.store.commitAuthority.bind(f.store);
  f.store.commitAuthority = async input => {
    await original(input);
    return {status: "ambiguous", reason_code: "test_lost_ack", reason: "Synthetic lost acknowledgment"};
  };
  const adopt = {...f.base, action: "adopt", session_id: "target", operation_id: "adopt-lost",
    expected_provider_revision: packet.provider_revision};
  const recovered = await executeTodoContinuation(f.store, adopt);
  assert.equal(recovered.ok, true);
  assert.equal((recovered.claim as Record<string, unknown>).status, "recovered");
  f.store.commitAuthority = original;
  await change(f.store, {claimed_by: "agent-b"});
  const retry = await executeTodoContinuation(f.store, adopt);
  assert.equal(retry.ok, false);
});

test("rich context prepare: work_summary + structured fields", async t => {
  const f = await fixture(t);
  const initial = await f.inspect();
  const prepare = {...f.base, action: "prepare", operation_id: "prepare-rich",
    expected_provider_revision: initial.provider_revision,
    work_summary: "Implementing cross-agent handoff with typed transfer grant. Need to enforce note invariant.",
    approaches_tried: [
      {approach: "Unstructured note with hash only", outcome: "failed",
        reason: "Any caller can compute public facts and bypass prepare/inspect"},
      {approach: "Typed transfer_grant without note validation", outcome: "partial",
        reason: "Still accepts arbitrary JSON note with matching hash"},
    ],
    next_steps: ["Add shared validateContinuationNote predicate", "Add regression tests"],
    files_touched: [
      {path: "loopx/control_plane/coordination/todo_claim.ts", action: "edited",
        summary: "Import validateContinuationNote, enforce typed invariant"},
      {path: "loopx/control_plane/coordination/todo_continuation.ts", action: "edited",
        summary: "Adopt uses noteValidation.noteFacts"},
    ],
    key_decisions: [
      {decision: "Typed transfer_grant", rationale: "Binds source/target/todo/revision/note"},
    ],
    open_questions: ["Stage B should support agent-agnostic handoff?"],
  };
  const result = await executeTodoContinuation(f.store, prepare);
  assert.equal(result.ok, true, JSON.stringify(result));
  // Verify the note was written correctly.
  const head = await f.store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status === "loaded") {
    const todo = (head.head.todos as Record<string, unknown>[]).find(t => t.todo_id === "todo_a");
    assert.ok(todo);
    const note = JSON.parse(String(todo!.note));
    assert.equal(note.kind, "loopx-explicit-continuation");
    assert.equal(typeof note.work_summary, "string");
    assert.equal(note.approaches_tried.length, 2);
    assert.equal(note.next_steps.length, 2);
    assert.equal(note.files_touched.length, 2);
    assert.equal(note.key_decisions.length, 1);
    assert.equal(note.open_questions.length, 1);
  }
  // Target can adopt with rich context.
  const packet = await executeTodoContinuation(f.store, {...f.base, action: "inspect",
    session_id: "target-session", agent_id: "agent-b", workspace: f.root, artifacts: []});
  assert.equal(packet.can_adopt, true);
  assert.ok(packet.digest, "digest should be present for valid rich note");
  assert.equal(packet.digest.work_summary, prepare.work_summary);
  assert.equal(packet.digest.approaches_tried.length, 2);
  assert.equal(packet.digest.next_steps.length, 2);
  const adopt = {...f.base, action: "adopt", session_id: "target-session", agent_id: "agent-b",
    target_agent_id: "agent-b", operation_id: "adopt-rich",
    expected_provider_revision: packet.provider_revision};
  const adopted = await executeTodoContinuation(f.store, adopt);
  assert.equal(adopted.ok, true, JSON.stringify(adopted));
  assert.equal(adopted.current_authority_verified, true);
  assert.equal(adopted.target_agent_id, "agent-b");
});

test("rich context without work_summary or rationale is rejected", async t => {
  const f = await fixture(t);
  const initial = await f.inspect();
  const prepare = {...f.base, action: "prepare", operation_id: "prepare-invalid",
    expected_provider_revision: initial.provider_revision,
    // No work_summary and no rationale — should be rejected.
    approaches_tried: [{approach: "x", outcome: "failed", reason: "y"}],
  };
  await assert.rejects(() => executeTodoContinuation(f.store, prepare),
    /provide at least one of: work_summary/);
});

test("rich context with invalid approach outcome is rejected", async t => {
  const f = await fixture(t);
  const initial = await f.inspect();
  const prepare = {...f.base, action: "prepare", operation_id: "prepare-invalid-outcome",
    expected_provider_revision: initial.provider_revision,
    work_summary: "test",
    approaches_tried: [{approach: "x", outcome: "invalid", reason: "y"}],
  };
  await assert.rejects(() => executeTodoContinuation(f.store, prepare),
    /approaches_tried must be an array of at most 20 objects with approach\/outcome\/reason/);
});

test("rich context with invalid file action is rejected", async t => {
  const f = await fixture(t);
  const initial = await f.inspect();
  const prepare = {...f.base, action: "prepare", operation_id: "prepare-invalid-action",
    expected_provider_revision: initial.provider_revision,
    work_summary: "test",
    files_touched: [{path: "foo.ts", action: "invalid"}],
  };
  await assert.rejects(() => executeTodoContinuation(f.store, prepare),
    /files_touched must be an array of at most 50 objects with path\/action/);
});

test("legacy rationale-only prepare still works (backward compat)", async t => {
  const f = await fixture(t);
  // The original prepare from the fixture uses rationale + source_refs.
  const result = await executeTodoContinuation(f.store, f.prepare);
  assert.equal(result.ok, true, JSON.stringify(result));
  const packet = await executeTodoContinuation(f.store, {...f.base, action: "inspect",
    session_id: "target-session", agent_id: "agent-b", workspace: f.root, artifacts: []});
  assert.equal(packet.can_adopt, true);
  assert.equal(packet.decision_rationale, "The narrow path preserves the existing authority boundary");
  // Digest should include rationale for legacy notes.
  assert.ok(packet.digest);
  assert.equal(packet.digest.rationale, "The narrow path preserves the existing authority boundary");
});

test("inspect digest is absent when note is missing or stale", async t => {
  const f = await fixture(t);
  // Missing note.
  const missing = await f.inspect();
  assert.equal(missing.note_state, "missing");
  assert.equal(missing.digest, undefined);
  // Prepare, then change the Todo to make the note stale.
  await executeTodoContinuation(f.store, f.prepare);
  await change(f.store, {text: "Changed"});
  const stale = await executeTodoContinuation(f.store, {...f.base, action: "inspect",
    session_id: "target-session"});
  assert.equal(stale.note_state, "stale");
  assert.equal(stale.digest, undefined);
});

test("full rich context flow: prepare → digest inspect → cross-agent adopt", async t => {
  const f = await fixture(t);
  const initial = await f.inspect();
  // Source prepares rich context.
  const prepare = {...f.base, action: "prepare", operation_id: "prepare-full",
    expected_provider_revision: initial.provider_revision,
    work_summary: "Building cross-agent handoff. Stage A covers local lease-free Todos.",
    approaches_tried: [
      {approach: "Manual handoff with 600-char rationale", outcome: "partial",
        reason: "Too compact for complex context"},
    ],
    next_steps: ["Add rich context support", "Add digest output"],
    files_touched: [
      {path: "continuation_note.ts", action: "created", summary: "Shared validation module"},
      {path: "todo_continuation.ts", action: "edited", summary: "Rich context + digest"},
      {path: "todo_claim.ts", action: "read"},
    ],
    key_decisions: [
      {decision: "Shared validateContinuationNote", rationale: "Single source of truth"},
    ],
    open_questions: ["Stage B agent-agnostic handoff?"],
  };
  const prepareResult = await executeTodoContinuation(f.store, prepare);
  assert.equal(prepareResult.ok, true, JSON.stringify(prepareResult));
  assert.equal(prepareResult.note_readback_verified, true);
  // Target inspects and gets a digest.
  const inspect = await executeTodoContinuation(f.store, {...f.base, action: "inspect",
    session_id: "target-session", agent_id: "agent-a", workspace: f.root, artifacts: []});
  assert.equal(inspect.can_adopt, true);
  assert.ok(inspect.digest);
  assert.equal(inspect.digest.work_summary, prepare.work_summary);
  assert.equal(inspect.digest.approaches_tried.length, 1);
  assert.equal(inspect.digest.next_steps.length, 2);
  assert.equal(inspect.digest.files_touched.length, 3);
  assert.equal(inspect.digest.key_decisions.length, 1);
  assert.equal(inspect.digest.open_questions.length, 1);
  // Target adopts (same agent, different session — can_adopt requires different session).
  const adopt = {...f.base, action: "adopt", session_id: "target-session", agent_id: "agent-a",
    operation_id: "adopt-full", expected_provider_revision: inspect.provider_revision};
  const adoptResult = await executeTodoContinuation(f.store, adopt);
  assert.equal(adoptResult.ok, true, JSON.stringify(adoptResult));
  assert.equal(adoptResult.current_authority_verified, true);
});

// P1 regression: final claim authority must reject notes that the
// producer can never create. The exact schema in validateContinuationNote()
// must match buildContextFromInput() — same allowed keys, same bounds, same
// non-empty rules. These tests call evaluateCoordinationTodoClaimDecision
// directly with a matching transfer_grant to prove the final authority
// (not just the can_adopt gate) enforces the exact closed schema.
test("P1: empty source_session rejected by final claim decision", async t => {
  const f = await fixture(t);
  const head = await f.store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("missing fixture");
  const todo = (head.head.todos as Record<string, unknown>[])[0]!;
  const todoFacts = computeContinuationTodoFacts(todo as Record<string, unknown> as never);
  // Note with empty source_session — buildContextFromInput would reject this.
  const malformedNote = JSON.stringify({kind: "loopx-explicit-continuation", source_session: "",
    todo_facts: todoFacts, work_summary: "Valid summary"});
  await change(f.store, {note: malformedNote});
  const after = await f.store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status !== "loaded") throw new Error("missing fixture");
  const malformedTodo = {...(after.head.todos as Record<string, unknown>[])[0]!, note: malformedNote};
  const decision = evaluateCoordinationTodoClaimDecision(malformedTodo, {
    goal_id: "goal-a", todo_id: "todo_a", claimed_by: "agent-b", actor_agent_id: "agent-b",
    expected_role: "agent", registered_agents: ["agent-a", "agent-b"], operation_id: "op",
    expected_provider_revision: "rev", dry_run: false, now: new Date(),
    transfer_grant: {schema_version: "todo_transfer_grant_v0", source_agent_id: "agent-a",
      target_agent_id: "agent-b", todo_id: "todo_a", expected_revision: "rev",
      continuation_note_facts: "matching"},
  });
  assert.equal(decision.status, "rejected", JSON.stringify(decision));
  assert.equal(decision.reason_code, "claim_owner_mismatch", JSON.stringify(decision));
});

test("P1: overlong next_step (301 chars) rejected by final claim decision", async t => {
  const f = await fixture(t);
  const head = await f.store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("missing fixture");
  const todo = (head.head.todos as Record<string, unknown>[])[0]!;
  const todoFacts = computeContinuationTodoFacts(todo as Record<string, unknown> as never);
  // next_step with 301 chars — buildContextFromInput limits to 300.
  const malformedNote = JSON.stringify({kind: "loopx-explicit-continuation", source_session: "source",
    todo_facts: todoFacts, work_summary: "Valid", next_steps: ["x".repeat(301)]});
  await change(f.store, {note: malformedNote});
  const after = await f.store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status !== "loaded") throw new Error("missing fixture");
  const malformedTodo = {...(after.head.todos as Record<string, unknown>[])[0]!, note: malformedNote};
  const decision = evaluateCoordinationTodoClaimDecision(malformedTodo, {
    goal_id: "goal-a", todo_id: "todo_a", claimed_by: "agent-b", actor_agent_id: "agent-b",
    expected_role: "agent", registered_agents: ["agent-a", "agent-b"], operation_id: "op",
    expected_provider_revision: "rev", dry_run: false, now: new Date(),
    transfer_grant: {schema_version: "todo_transfer_grant_v0", source_agent_id: "agent-a",
      target_agent_id: "agent-b", todo_id: "todo_a", expected_revision: "rev",
      continuation_note_facts: "matching"},
  });
  assert.equal(decision.status, "rejected", JSON.stringify(decision));
  assert.equal(decision.reason_code, "claim_owner_mismatch", JSON.stringify(decision));
});

test("P1: extra root key rejected by final claim decision", async t => {
  const f = await fixture(t);
  const head = await f.store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("missing fixture");
  const todo = (head.head.todos as Record<string, unknown>[])[0]!;
  const todoFacts = computeContinuationTodoFacts(todo as Record<string, unknown> as never);
  // Extra unexpected_authority_field — buildContextFromInput never emits this.
  const malformedNote = JSON.stringify({kind: "loopx-explicit-continuation", source_session: "source",
    todo_facts: todoFacts, work_summary: "Valid", unexpected_authority_field: "evil"});
  await change(f.store, {note: malformedNote});
  const after = await f.store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status !== "loaded") throw new Error("missing fixture");
  const malformedTodo = {...(after.head.todos as Record<string, unknown>[])[0]!, note: malformedNote};
  const decision = evaluateCoordinationTodoClaimDecision(malformedTodo, {
    goal_id: "goal-a", todo_id: "todo_a", claimed_by: "agent-b", actor_agent_id: "agent-b",
    expected_role: "agent", registered_agents: ["agent-a", "agent-b"], operation_id: "op",
    expected_provider_revision: "rev", dry_run: false, now: new Date(),
    transfer_grant: {schema_version: "todo_transfer_grant_v0", source_agent_id: "agent-a",
      target_agent_id: "agent-b", todo_id: "todo_a", expected_revision: "rev",
      continuation_note_facts: "matching"},
  });
  assert.equal(decision.status, "rejected", JSON.stringify(decision));
  assert.equal(decision.reason_code, "claim_owner_mismatch", JSON.stringify(decision));
});

test("P1: extra nested key in approach_tried rejected by final claim decision", async t => {
  const f = await fixture(t);
  const head = await f.store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("missing fixture");
  const todo = (head.head.todos as Record<string, unknown>[])[0]!;
  const todoFacts = computeContinuationTodoFacts(todo as Record<string, unknown> as never);
  // Extra nested key in approach_tried — isApproachTried now rejects unknown keys.
  const malformedNote = JSON.stringify({kind: "loopx-explicit-continuation", source_session: "source",
    todo_facts: todoFacts, work_summary: "Valid",
    approaches_tried: [{approach: "Try X", outcome: "failed", reason: "Nope", extra_nested: "evil"}]});
  await change(f.store, {note: malformedNote});
  const after = await f.store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status !== "loaded") throw new Error("missing fixture");
  const malformedTodo = {...(after.head.todos as Record<string, unknown>[])[0]!, note: malformedNote};
  const decision = evaluateCoordinationTodoClaimDecision(malformedTodo, {
    goal_id: "goal-a", todo_id: "todo_a", claimed_by: "agent-b", actor_agent_id: "agent-b",
    expected_role: "agent", registered_agents: ["agent-a", "agent-b"], operation_id: "op",
    expected_provider_revision: "rev", dry_run: false, now: new Date(),
    transfer_grant: {schema_version: "todo_transfer_grant_v0", source_agent_id: "agent-a",
      target_agent_id: "agent-b", todo_id: "todo_a", expected_revision: "rev",
      continuation_note_facts: "matching"},
  });
  assert.equal(decision.status, "rejected", JSON.stringify(decision));
  assert.equal(decision.reason_code, "claim_owner_mismatch", JSON.stringify(decision));
});


test("hard-lease inspect reads context without pretending an unfenced receiver can adopt", async t => {
  const f = await fixture(t, "hard_lease");
  const packet = await f.inspect();
  assert.equal(packet.ok, true, JSON.stringify(packet));
  assert.equal(packet.can_adopt, false);
  assert.equal(packet.note_state, "missing");
  assert.equal((packet.execution_authority as Record<string, unknown>).allowed, false);
});
