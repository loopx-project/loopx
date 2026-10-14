import assert from "node:assert/strict";
import test from "node:test";
import {chmod, mkdir, mkdtemp, readFile, readdir, realpath, rm, symlink, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {checkpointArtifacts} from "../../loopx/control_plane/runtime/checkpoint_artifacts.ts";
import {sqliteAuthorityRuntime} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";

async function fixture(t: test.TestContext) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "checkpoint-artifacts-")));
  t.after(() => rm(root, {recursive: true, force: true}));
  await mkdir(join(root, "code"));
  await mkdir(join(root, "code/empty"));
  await writeFile(join(root, "code/solution.sh"), "#!/bin/sh\nexit 0\n");
  await chmod(join(root, "code/solution.sh"), 0o755);
  await writeFile(join(root, "conversation"), "native cursor fixture\n");
  const components = [{id: "code", source_path: join(root, "code")}, {id: "conversation", source_path: join(root, "conversation")}];
  const probe = await checkpointArtifacts({action: "probe", components});
  const request = {action: "capture", components, expected_source_sha256: probe.source_sha256,
    destination: join(root, "captured"), point_metadata: {cursor: "completed-native-turn", effects: "historical only"}};
  return {root, request};
}

test("capture, verify and inert materialization preserve executable code and empty directories", async t => {
  const {root, request} = await fixture(t);
  const captured = await checkpointArtifacts(request);
  assert.equal(captured.integrity_verified, true);
  assert.equal(captured.source_stability_verified, true);
  assert.equal(captured.research_point_qualified, false);
  assert.equal(captured.execution_authority_granted, false);
  assert.equal(captured.privacy_certified, false);
  const restored = await checkpointArtifacts({action: "materialize", root: request.destination,
    expected_sha256: captured.sha256, destination: join(root, "restored")});
  assert.equal(restored.activation_performed, false);
  assert.equal(await readFile(join(root, "restored/components/code/solution.sh"), "utf8"), "#!/bin/sh\nexit 0\n");
  assert.deepEqual(await readdir(join(root, "restored/components/code/empty")), []);
  assert.deepEqual(await readFile(join(root, "restored/checkpoint.json")), await readFile(join(root, "captured/checkpoint.json")));
  await assert.rejects(checkpointArtifacts({action: "materialize", root: request.destination,
    expected_sha256: captured.sha256, destination: join(root, "restored")}), /already exists/);
});

test("changed source, duplicate identities and overlapping sources publish no unit", async t => {
  const {root, request} = await fixture(t);
  await writeFile(join(root, "conversation"), "new native cursor");
  await assert.rejects(checkpointArtifacts(request), /source moved/);
  await assert.rejects(checkpointArtifacts({...request, components: [...request.components, request.components[0]]}), /duplicated/);
  await assert.rejects(checkpointArtifacts({action: "probe", components: [request.components[0], {id: "nested", source_path: join(root, "code/empty")}]}), /overlap/);
  assert.deepEqual((await readdir(root)).sort(), ["code", "conversation"]);
});

test("missing, changed and extra artifact members or foreign expected digest reject", async t => {
  const {root, request} = await fixture(t), captured = await checkpointArtifacts(request);
  const verify = {action: "verify", root: request.destination, expected_sha256: captured.sha256};
  await assert.rejects(checkpointArtifacts({...verify, expected_sha256: "foreign"}), /digest/);
  await writeFile(join(root, "captured/components/conversation"), "tampered");
  await assert.rejects(checkpointArtifacts(verify), /artifact members/);
  await writeFile(join(root, "captured/components/conversation"), "native cursor fixture\n");
  await writeFile(join(root, "captured/foreign"), "unreviewed member");
  await assert.rejects(checkpointArtifacts(verify), /unexpected members/);
  await rm(join(root, "captured/foreign"));
  await rm(join(root, "captured/components/conversation"));
  await assert.rejects(checkpointArtifacts(verify), /ENOENT/);
  await assert.rejects(checkpointArtifacts({action: "materialize", root: request.destination,
    expected_sha256: captured.sha256, destination: join(root, "must-not-exist")}));
  assert.ok(!(await readdir(root)).includes("must-not-exist"));
});

test("expired or invalid capture windows publish no unit and leave the source intact", async t => {
  const {root, request} = await fixture(t);
  const bytes = await readFile(join(root, "conversation"));
  await assert.rejects(checkpointArtifacts({...request, deadline_epoch_ms: Date.now() - 1}), /window has closed/);
  await assert.rejects(checkpointArtifacts({...request, deadline_epoch_ms: Number.NaN}), /finite/);
  await assert.rejects(checkpointArtifacts({...request, execution_authority_granted: true}), /fields are invalid/);
  assert.deepEqual(await readFile(join(root, "conversation")), bytes);
  assert.deepEqual((await readdir(root)).sort(), ["code", "conversation"]);
});

test("concurrent capture reserves one destination and cannot replace an existing unit", async t => {
  const {root, request} = await fixture(t);
  const results = await Promise.allSettled([checkpointArtifacts(request), checkpointArtifacts(request)]);
  assert.equal(results.filter(r => r.status === "fulfilled").length, 1);
  assert.equal(results.filter(r => r.status === "rejected").length, 1);
  assert.deepEqual((await readdir(root)).sort(), ["captured", "code", "conversation"]);
  const bytes = await readFile(join(root, "captured/checkpoint.json"));
  await assert.rejects(checkpointArtifacts(request), /already exists/);
  assert.deepEqual(await readFile(join(root, "captured/checkpoint.json")), bytes);
});

test("symlink sources, nested links and redirected destinations reject without publication", async t => {
  const {root, request} = await fixture(t);
  await symlink(join(root, "code"), join(root, "alias"), process.platform === "win32" ? "junction" : "dir");
  await assert.rejects(checkpointArtifacts({action: "probe", components: [{id: "code", source_path: join(root, "alias")}]}), /symlink/);
  await symlink(join(root, "conversation"), join(root, "code/link"));
  await assert.rejects(checkpointArtifacts(request), /symlink/);
  await assert.rejects(checkpointArtifacts({...request, destination: join(root, "alias/capture")}), /symlink/);
});

test("SQLite uses the existing committed WAL snapshot owner and materializes verbatim", async t => {
  const {root, request} = await fixture(t);
  await mkdir(join(root, "state"));
  const {driver} = sqliteAuthorityRuntime();
  const writer = new driver.DatabaseSync(join(root, "state/authority.sqlite"));
  try {
    writer.exec("PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0; CREATE TABLE evidence(value TEXT); INSERT INTO evidence VALUES('retained negative result')");
    await writeFile(join(root, "state/notes-wal"), "ordinary retained note");
    const components = [...request.components, {id: "state", source_path: join(root, "state")}];
    const probe = await checkpointArtifacts({action: "probe", components});
    const captured = await checkpointArtifacts({...request, components, expected_source_sha256: probe.source_sha256});
    assert.deepEqual((await readdir(join(root, "captured/components/state"))).sort(), ["authority.sqlite", "notes-wal"]);
    await writeFile(join(root, "captured/components/state/authority.sqlite-shm"), "foreign index");
    await assert.rejects(checkpointArtifacts({action: "verify", root: request.destination,
      expected_sha256: captured.sha256}), /artifact members/);
    await rm(join(root, "captured/components/state/authority.sqlite-shm"));
    await checkpointArtifacts({action: "materialize", root: request.destination,
      expected_sha256: captured.sha256, destination: join(root, "restored")});
    assert.deepEqual(await readFile(join(root, "restored/components/state/authority.sqlite")), await readFile(join(root, "captured/components/state/authority.sqlite")));
    const copy = new driver.DatabaseSync(join(root, "restored/components/state/authority.sqlite"), {readOnly: true});
    try {assert.equal(copy.prepare("SELECT value FROM evidence").get()?.value, "retained negative result");}
    finally {copy.close();}
  } finally {writer.close();}
});

test("a committed WAL change after the probe invalidates capture, including a standalone database component", async t => {
  const {root, request} = await fixture(t);
  const path = join(root, "authority.sqlite"), {driver} = sqliteAuthorityRuntime();
  const writer = new driver.DatabaseSync(path);
  try {
    writer.exec("PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0; CREATE TABLE evidence(value TEXT)");
    const components = [{id: "state", source_path: path}];
    const probe = await checkpointArtifacts({action: "probe", components});
    writer.exec("INSERT INTO evidence VALUES('new committed evidence')");
    await assert.rejects(checkpointArtifacts({...request, components, expected_source_sha256: probe.source_sha256}), /source moved/);
    assert.ok(!(await readdir(root)).includes("captured"));
    const stable = await checkpointArtifacts({action: "probe", components});
    await checkpointArtifacts({...request, components, expected_source_sha256: stable.source_sha256});
    const copy = new driver.DatabaseSync(join(root, "captured/components/state"), {readOnly: true});
    try {assert.equal(copy.prepare("SELECT value FROM evidence").get()?.value, "new committed evidence");}
    finally {copy.close();}
  } finally {writer.close();}
});

test("read-only snapshot housekeeping on a closed WAL database is not source movement", async t => {
  const {root, request} = await fixture(t), {driver} = sqliteAuthorityRuntime();
  await mkdir(join(root, "state"));
  const path = join(root, "state/authority.sqlite"), writer = new driver.DatabaseSync(path);
  writer.exec("PRAGMA journal_mode=WAL; CREATE TABLE evidence(value TEXT); INSERT INTO evidence VALUES('retained')");
  writer.close();
  assert.deepEqual(await readdir(join(root, "state")), ["authority.sqlite"]);
  const components = [{id: "state", source_path: join(root, "state")}];
  const probe = await checkpointArtifacts({action: "probe", components});
  const captured = await checkpointArtifacts({...request, components, expected_source_sha256: probe.source_sha256});
  assert.equal(captured.source_stability_verified, true);
  const after = await checkpointArtifacts({action: "probe", components});
  assert.equal(after.source_sha256, probe.source_sha256);
  assert.deepEqual(await readdir(join(root, "captured/components/state")), ["authority.sqlite"]);
  const changed = new driver.DatabaseSync(path);
  try {
    changed.exec("INSERT INTO evidence VALUES('new committed data')");
    await assert.rejects(checkpointArtifacts({...request, components, destination: join(root, "must-not-exist"),
      expected_source_sha256: probe.source_sha256}), /source moved/);
  } finally {changed.close();}
  const copy = new driver.DatabaseSync(join(root, "captured/components/state/authority.sqlite"), {readOnly: true});
  try {assert.equal(copy.prepare("SELECT COUNT(*) AS n FROM evidence").get()?.n, 1);}
  finally {copy.close();}
});

test("an explicitly selected sidecar remains a byte artifact rather than disappearing from its witness", async t => {
  const {root, request} = await fixture(t), {driver} = sqliteAuthorityRuntime();
  const path = join(root, "authority.sqlite"), writer = new driver.DatabaseSync(path);
  try {
    writer.exec("PRAGMA journal_mode=WAL; CREATE TABLE evidence(value TEXT)");
    const components = [{id: "explicit_index", source_path: path + "-shm"}];
    const probe = await checkpointArtifacts({action: "probe", components});
    assert.equal((probe.source_witness as Record<string, unknown[]>).explicit_index.length, 1);
    const captured = await checkpointArtifacts({...request, components, expected_source_sha256: probe.source_sha256});
    assert.equal(captured.integrity_verified, true);
    assert.deepEqual(await readFile(join(root, "captured/components/explicit_index")), await readFile(path + "-shm"));
  } finally {writer.close();}
});

test("replan capture selection requires the original admitted identity and grants no execution", async () => {
  const id = "replan-0123456789abcdef", turn = "fixture-turn";
  const identity = {goal_id: "fixture-goal", agent_id: "fixture-agent", turn_instance_id: turn,
    replan_obligation_id: id, binding_kind: "autonomous_replan", binding_id: id,
    effect_id: `fixture-goal:fixture-agent:autonomous_replan:${id}:${turn}`};
  const quota = {ok: true, goal_id: "fixture-goal", effective_action: "autonomous_replan_required",
    replan_action_packet: {schema_version: "replan_action_packet_v0", decision: "replan_required", obligation_id: id},
    heartbeat_receipt: {status: "committed", turn_instance_id: turn, settlement_identity: identity}};
  const request = {action: "select_replan", quota, goal_id: "fixture-goal", agent_id: "fixture-agent", turn_instance_id: turn};
  const selected = await checkpointArtifacts(request);
  assert.equal(selected.capture, true);
  assert.equal(selected.obligation_id, id);
  assert.equal(selected.execution_authority_granted, false);
  const todoBound = await checkpointArtifacts({...request, quota: {...quota, heartbeat_receipt: {
    ...quota.heartbeat_receipt, semantic_replan_obligation_id: id, settlement_identity: {
      goal_id: "fixture-goal", agent_id: "fixture-agent", turn_instance_id: turn, todo_id: "todo_existing",
      effect_id: `fixture-goal:fixture-agent:todo_existing:${turn}`}}}});
  assert.equal(todoBound.capture, true);
  assert.equal(todoBound.settlement_effect_id, `fixture-goal:fixture-agent:todo_existing:${turn}`);
  const ordinary = await checkpointArtifacts({...request, quota: {...quota, effective_action: "normal_run"}});
  assert.equal(ordinary.capture, false);
  await assert.rejects(checkpointArtifacts({...request, turn_instance_id: "another-turn"}), /admitted identity/);
  await assert.rejects(checkpointArtifacts({...request, agent_id: "another-agent"}), /admitted identity/);
  await assert.rejects(checkpointArtifacts({...request, quota: {...quota, replan_action_packet:
    {...quota.replan_action_packet, obligation_id: "replan-fedcba9876543210"}}}), /admitted identity/);
  await assert.rejects(checkpointArtifacts({...request, quota: {...quota, heartbeat_receipt:
    {...quota.heartbeat_receipt, settlement_identity: {...identity, todo_id: "todo_conflict"}}}}), /admitted identity/);
  await assert.rejects(checkpointArtifacts({...request, quota: {...quota, ok: false}}), /matching current/);
});

test("committed semantic replan can capture before work selection without inventing settlement authority", async () => {
  const id = "replan-0123456789abcdef", turn = "fixture-turn";
  const quota = {ok: true, goal_id: "fixture-goal", effective_action: "autonomous_replan_required",
    agent_identity: {agent_id: "fixture-agent", registered: true},
    replan_action_packet: {schema_version: "replan_action_packet_v0", decision: "replan_required", obligation_id: id},
    heartbeat_receipt: {status: "committed", turn_instance_id: turn, event_id: "fixture-event",
      semantic_replan_obligation_id: id, settlement_binding_owed: true}};
  const request = {action: "select_replan", quota, goal_id: "fixture-goal", agent_id: "fixture-agent", turn_instance_id: turn};
  const before = structuredClone(quota);
  assert.deepEqual(await checkpointArtifacts(request), {
    capture: true, obligation_id: id, execution_authority_granted: false,
  });
  assert.deepEqual(quota, before);
  for (const receipt of [
    {...quota.heartbeat_receipt, status: "pending"},
    {...quota.heartbeat_receipt, settlement_binding_owed: false},
    {...quota.heartbeat_receipt, semantic_replan_obligation_id: "replan-fedcba9876543210"},
    {...quota.heartbeat_receipt, turn_instance_id: "another-turn"},
    {...quota.heartbeat_receipt, settlement_identity: null},
  ]) await assert.rejects(checkpointArtifacts({...request, quota: {...quota, heartbeat_receipt: receipt}}));
  await assert.rejects(checkpointArtifacts({...request, agent_id: "another-agent"}));
  await assert.rejects(checkpointArtifacts({...request, quota: {...quota,
    agent_identity: {...quota.agent_identity, registered: false}}}));
});
