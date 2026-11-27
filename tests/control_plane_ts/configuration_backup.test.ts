import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtemp, mkdir, readFile, readdir, realpath, rm, symlink} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {captureConfigurationBackup, compareConfigurationBackups, restoreConfigurationBackup, verifyConfigurationBackup, MAX_CONFIGURATION_COMPARISON_BYTES, requireConfigurationComparisonResultSize} from "../../loopx/control_plane/configuration_backup.ts";
import {EffectRuntimeConflictError, EffectRuntimeRequestError} from "../../loopx/control_plane/effect_runtime_errors.ts";

function snapshot() {
  return captureConfigurationBackup({data: {machine_configuration: {schema_version: "loopx_machine_configuration_v0", namespaces: {
    goal_storage: {schema_version: "loopx_goal_storage_defaults_v0", new_goal_provider: "sqlite"},
    optional_provider: {schema_version: "provider_v1", nullable: null, enabled: false},
  }}, goals: [{goal_id: "fixture", goal_configuration: {id: "fixture", control_plane: {
    optional_provider: {context: "完整配置".repeat(10000)},
  }, coordination: {storage_target: {provider: "sqlite"}}}}]}});
}

test("checkpoint preserves complete optional configuration and never certifies privacy or activation", async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "configuration-backup-")));
  try {
    const backup = snapshot(), destination = join(root, "restored");
    const request = {backup, destination, expected_sha256: backup.sha256, execute: false};
    assert.equal((await restoreConfigurationBackup(request)).written, false);
    const receipt = await restoreConfigurationBackup({...request, execute: true});
    assert.equal(receipt.readback_verified, true);
    assert.equal(receipt.live_configuration_changed, false);
    assert.deepEqual(JSON.parse(await readFile(join(destination, "configuration-backup.json"), "utf8")), backup);
    assert.deepEqual(JSON.parse(await readFile(join(destination, "machine/configuration.json"), "utf8")),
      (backup.data as Record<string, unknown>).machine_configuration);
    await assert.rejects(restoreConfigurationBackup({...request, execute: true}),
      error => error instanceof EffectRuntimeConflictError && /already exists/.test(error.message));
    assert.deepEqual(JSON.parse(await readFile(join(destination, "configuration-backup.json"), "utf8")), backup);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("competing restores publish one complete checkpoint without replacing it", async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "configuration-backup-")));
  try {
    const backup = snapshot(), destination = join(root, "restored");
    const request = {backup, destination, expected_sha256: backup.sha256, execute: true};
    const attempts = await Promise.allSettled([
      restoreConfigurationBackup(request), restoreConfigurationBackup(request),
    ]);
    assert.equal(attempts.filter((attempt) => attempt.status === "fulfilled").length, 1);
    assert.equal(attempts.filter((attempt) => attempt.status === "rejected").length, 1);
    assert.deepEqual(JSON.parse(await readFile(join(destination, "configuration-backup.json"), "utf8")), backup);
    assert.deepEqual((await readdir(root)).sort(), ["restored"]);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("tampering, extra envelope fields, duplicate identities and foreign reviewed digest reject before restore", async () => {
  const backup = snapshot();
  assert.throws(() => verifyConfigurationBackup({...backup, data: {machine_configuration: null, goals: []}}),
    error => error instanceof EffectRuntimeRequestError && /digest mismatch/.test(error.message));
  assert.throws(() => verifyConfigurationBackup({...backup, privacy_certified: true}), /envelope/);
  assert.throws(() => verifyConfigurationBackup({...backup, extra: true}), /envelope/);
  const data = backup.data as Record<string, unknown>;
  const goals = data.goals as unknown[];
  assert.throws(() => captureConfigurationBackup({data: {...data, goals: [...goals, ...goals]}}), /identity/);
  await assert.rejects(restoreConfigurationBackup({backup, expected_sha256: "changed"}), /digest changed/);
});

test("dangling targets and symlink ancestors cannot redirect recovery", async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "configuration-backup-")));
  try {
    const backup = snapshot();
    await mkdir(join(root, "physical"));
    await symlink(join(root, "physical"), join(root, "alias"), process.platform === "win32" ? "junction" : "dir");
    await symlink(join(root, "missing"), join(root, "dangling"), process.platform === "win32" ? "junction" : "file");
    await assert.rejects(restoreConfigurationBackup({backup, destination: join(root, "alias/new"), expected_sha256: backup.sha256, execute: true}),
      error => error instanceof EffectRuntimeRequestError);
    await assert.rejects(restoreConfigurationBackup({backup, destination: join(root, "dangling"), expected_sha256: backup.sha256, execute: true}),
      error => error instanceof EffectRuntimeConflictError);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("absence remains absence rather than introducing a machine default", () => {
  const backup = captureConfigurationBackup({data: {machine_configuration: null, goals: []}});
  assert.equal(verifyConfigurationBackup(backup).machine_configuration_present, false);
});

test("comparison preserves missing, null, false and array order while aligning Goal identities", () => {
  const capture = (machine: unknown, goals: unknown[]) => captureConfigurationBackup({data: {machine_configuration: machine, goals}});
  const goal = (id: string, extra = {}) => ({goal_id: id, goal_configuration: {id, ...extra}});
  const before = capture({settings: {missing: null, enabled: false, zero: 0, ordered: [1, 2]}},
    [goal("shared", {"a/b~c": null}), goal("only-before")]);
  const after = capture({settings: {enabled: null, zero: false, ordered: [2, 1], added: false}},
    [goal("only-after"), goal("shared", {"a/b~c": false})]);
  const original = JSON.stringify([before, after]);
  const result = compareConfigurationBackups({before, after});
  assert.equal(result.before_sha256, before.sha256);
  assert.equal(result.after_sha256, after.sha256);
  assert.deepEqual(result.only_in_before, ["only-before"]);
  assert.deepEqual(result.only_in_after, ["only-after"]);
  assert.equal(result.privacy_certified, false);
  assert.equal(result.activation_performed, false);
  assert.equal(result.written, false);
  const changes = result.changes as Array<Record<string, unknown>>;
  assert.deepEqual(changes.find(row => row.path === "/machine_configuration/settings/missing"),
    {path: "/machine_configuration/settings/missing", before_present: true, after_present: false, before: null});
  assert.deepEqual(changes.find(row => row.path === "/machine_configuration/settings/added"),
    {path: "/machine_configuration/settings/added", before_present: false, after_present: true, after: false});
  assert.deepEqual(changes.find(row => row.path === "/machine_configuration/settings/ordered")?.after, [2, 1]);
  assert.deepEqual(changes.find(row => row.path === "/machine_configuration/settings/zero"),
    {path: "/machine_configuration/settings/zero", before_present: true, after_present: true, before: 0, after: false});
  assert.deepEqual(changes.find(row => row.path === "/machine_configuration/settings/enabled"),
    {path: "/machine_configuration/settings/enabled", before_present: true, after_present: true, before: false, after: null});
  assert.deepEqual(changes.find(row => row.path === "/goals/shared/a~1b~0c")?.before, null);
  assert.deepEqual(changes.find(row => row.path === "/goals/shared/a~1b~0c")?.after, false);
  assert.equal(JSON.stringify([before, after]), original);
});

test("reordered Goal declarations and object keys do not invent configuration changes", () => {
  const a = {goal_id: "a", goal_configuration: {id: "a", values: {left: 1, right: 2}, array: [{left: 1, right: 2}]}};
  const b = {goal_id: "b", goal_configuration: {id: "b"}};
  const before = captureConfigurationBackup({data: {machine_configuration: null, goals: [a, b]}});
  const after = captureConfigurationBackup({data: {machine_configuration: null,
    goals: [b, {...a, goal_configuration: {values: {right: 2, left: 1}, array: [{right: 2, left: 1}], id: "a"}}]}});
  assert.notEqual(before.sha256, after.sha256);
  assert.equal(compareConfigurationBackups({before, after}).change_count, 0);
});

test("both snapshots must verify and machine absence is not an empty object", () => {
  const before = captureConfigurationBackup({data: {machine_configuration: null, goals: []}});
  const after = captureConfigurationBackup({data: {machine_configuration: {}, goals: []}});
  const result = compareConfigurationBackups({before, after});
  assert.equal(result.before_machine_configuration_present, false);
  assert.equal(result.after_machine_configuration_present, true);
  assert.deepEqual(result.changes, [{path: "/machine_configuration", before_present: true, after_present: true, before: null, after: {}}]);
  for (const request of [{before: {...before, sha256: "invalid"}, after}, {before, after: {...after, sha256: "invalid"}},
    {before: null, after}]) assert.throws(() => compareConfigurationBackups(request));
});

test("complete comparison size is bounded before a successful result is returned", (context) => {
  assert.equal(MAX_CONFIGURATION_COMPARISON_BYTES, 56 * 1024 * 1024);
  assert.doesNotThrow(() => requireConfigurationComparisonResultSize(MAX_CONFIGURATION_COMPARISON_BYTES));
  assert.throws(() => requireConfigurationComparisonResultSize(MAX_CONFIGURATION_COMPARISON_BYTES + 1),
    error => error instanceof EffectRuntimeRequestError && error.code === "request_too_large");
  const original = Buffer.byteLength;
  // Exercise the actual owner preflight at its byte-count boundary without
  // allocating a large fixture. Envelope verification still uses real bytes.
  context.mock.method(Buffer, "byteLength", (value: string, encoding?: BufferEncoding) =>
    typeof value === "string" && value.startsWith('{"ok":true,"status":"compared"')
      ? MAX_CONFIGURATION_COMPARISON_BYTES + 1 : original(value, encoding));
  const backup = snapshot();
  assert.throws(() => compareConfigurationBackups({before: backup, after: backup}),
    error => error instanceof EffectRuntimeRequestError && error.code === "request_too_large"
      && error.message.includes("56 MiB complete-result limit"));
});
