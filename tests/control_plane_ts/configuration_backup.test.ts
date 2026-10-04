import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtemp, mkdir, readFile, realpath, rm, symlink} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {captureConfigurationBackup, restoreConfigurationBackup, verifyConfigurationBackup} from "../../loopx/control_plane/configuration_backup.ts";

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
    await assert.rejects(restoreConfigurationBackup({...request, execute: true}), /already exists/);
  } finally {await rm(root, {recursive: true, force: true});}
});

test("tampering, extra envelope fields, duplicate identities and foreign reviewed digest reject before restore", async () => {
  const backup = snapshot();
  assert.throws(() => verifyConfigurationBackup({...backup, data: {machine_configuration: null, goals: []}}), /digest mismatch/);
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
    await symlink(join(root, "physical"), join(root, "alias"), "dir");
    await symlink(join(root, "missing"), join(root, "dangling"));
    for (const destination of [join(root, "alias/new"), join(root, "dangling")]) {
      await assert.rejects(restoreConfigurationBackup({backup, destination, expected_sha256: backup.sha256, execute: true}));
    }
  } finally {await rm(root, {recursive: true, force: true});}
});

test("absence remains absence rather than introducing a machine default", () => {
  const backup = captureConfigurationBackup({data: {machine_configuration: null, goals: []}});
  assert.equal(verifyConfigurationBackup(backup).machine_configuration_present, false);
});
