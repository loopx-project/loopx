import assert from "node:assert/strict";
import test from "node:test";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} from "../../loopx/control_plane/effect_runtime_handlers.ts";
import {verifyStateRecoveryArchive, verifyStateRecoveryManifest} from "../../loopx/control_plane/state_recovery.ts";
import {decodeStateRecoveryAudit, STATE_RECOVERY_AUDIT_SCHEMA} from "../../loopx/control_plane/state_recovery_contract.ts";

const member = {archive_path: "runtime-root/retained", size_bytes: 1, sha256: "c".repeat(64)};
function request() {
  const external = {schema_version: "loopx_state_backup_v0", ok: true, dry_run: false,
    execute_requested: true, backup_id: "fixture", private_hint: "private-marker",
    execution: {archive_sha256: "a".repeat(64), archive_size_bytes: 500,
      file_members: [member], archive_to_logical_ratio: 0.5}};
  const embedded = structuredClone(external) as Record<string, any>;
  embedded.execution.archive_sha256 = embedded.execution.archive_size_bytes = null;
  delete embedded.execution.archive_to_logical_ratio;
  return {external_manifest: external, embedded_manifest: embedded, files: [member],
    archive_sha256: "a".repeat(64), archive_size_bytes: 500, external_manifest_sha256: "b".repeat(64),
    sqlite_member_count: 0};
}

test("archive and manifest checks bind observed bytes without projecting private capture metadata", () => {
  const input = request(), before = structuredClone(input);
  const first = verifyStateRecoveryArchive(input);
  assert.equal(first.ok, true);
  const verified = verifyStateRecoveryManifest(input);
  assert.equal(verified.ok, true);
  assert.equal(verified.file_witnesses_verified, true);
  assert.equal(verified.manifest_verified, true);
  assert.equal(verified.execution_authority_granted, false);
  assert.equal(JSON.stringify(verified).includes("private-marker"), false);
  assert.deepEqual(input, before);
  assert.equal(verifyStateRecoveryArchive({...input, archive_sha256: "d".repeat(64)}).error_code, "archive_digest_mismatch");
  assert.equal(verifyStateRecoveryManifest({...input, files: [{...member, size_bytes: 2}]}).error_code, "member_witness_mismatch");
  assert.equal(verifyStateRecoveryManifest({...input, embedded_manifest: {...input.embedded_manifest, backup_id: "other"}}).error_code, "manifest_disagreement");
  assert.equal(verifyStateRecoveryManifest({...input, sqlite_member_count: 129}).error_code, "sqlite_inventory_capacity");
});

test("legacy absent member witnesses stay unqualified rather than becoming verified by an empty list", () => {
  const input = request();
  delete (input.external_manifest.execution as Record<string, unknown>).file_members;
  delete input.embedded_manifest.execution.file_members;
  const result = verifyStateRecoveryManifest(input);
  assert.equal(result.ok, true);
  assert.equal(result.file_witnesses_verified, false);
  assert.equal(result.capture_profile, "legacy_best_effort");
});

function audit() {
  const body = {ok: true, schema_version: STATE_RECOVERY_AUDIT_SCHEMA,
    recovery_id: "e".repeat(64), status: "held", written: true, manifest_verified: true,
    observed_at: "2026-10-12T00:00:00.000Z", execution_authority_granted: false, activation_eligible: false,
    backup: {schema_version: "loopx_state_backup_v0", archive_sha256: "a".repeat(64), external_manifest_sha256: "b".repeat(64)},
    workspace: {isolation: "inert", registered: false},
    components: [{component_id: "configuration", owner_kind: "configuration", state: "readable",
      reason_code: "configuration_digest_verified", next_action: "retain_isolated_configuration", facts: {}, dependency_ids: []}],
    summary: {readable: 1, historical_only: 0, unknown: 0}, findings: []};
  return {...body, audit_sha256: canonicalAuthoritySha256(body)};
}

test("the packaged client and owner reject permission, unknown-state and summary contradictions", () => {
  const input = audit();
  assert.doesNotThrow(() => decodeStateRecoveryAudit(input));
  for (const delta of [{execution_authority_granted: true}, {activation_eligible: true},
    {workspace: {isolation: "inert", registered: true}}, {summary: {...input.summary, readable: 2}}]) {
    assert.throws(() => decodeStateRecoveryAudit({...input, ...delta}));
  }
  assert.throws(() => decodeStateRecoveryAudit({...input, components: [{...input.components[0], state: "active"}]}));
  assert.throws(() => decodeStateRecoveryAudit({...input, components: [{...input.components[0], dependency_ids: ["missing"]}]}));
});

test("untrusted owner inspection has no shared-daemon dispatch", async () => {
  const handlers = createEffectRuntimeHandlers({fingerprint: "recovery-isolation", requestShutdown() {}});
  await assert.rejects(() => dispatchEffectRuntimeMethod(handlers, "state.recovery.owners.audit", {}), /unsupported Effect runtime method/);
});

test("recorded measurements reject invalid values and absent historical measurements remain absent", () => {
  const input = audit();
  const measurements = {archive_bytes: 500, regular_file_bytes: 256, archive_processing_ms: 3, owner_read_ms: 2};
  assert.equal(decodeStateRecoveryAudit(input).measurements, undefined);
  assert.deepEqual(decodeStateRecoveryAudit({...input, measurements}).measurements, measurements);
  for (const field of Object.keys(measurements)) {
    for (const value of [-1, 1.5, "3", null, Number.NaN, Number.MAX_SAFE_INTEGER + 1]) {
      assert.throws(() => decodeStateRecoveryAudit({...input, measurements: {...measurements, [field]: value}}));
    }
  }
});
