import assert from "node:assert/strict";
import test from "node:test";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} from "../../loopx/control_plane/effect_runtime_handlers.ts";
import {verifyStateRecoveryArchive, verifyStateRecoveryManifest} from "../../loopx/control_plane/state_recovery.ts";
import {decodeStateRecoveryAudit, STATE_RECOVERY_AUDIT_SCHEMA} from "../../loopx/control_plane/state_recovery_contract.ts";
import {authorityBackupDirectory} from "../../loopx/control_plane/coordination/authority_backup.ts";

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

test("captured runtime bindings are optional data and must name included inert roots", () => {
  for (const root of ["runtime-root", null]) {
    const input = request();
    for (const manifest of [input.external_manifest, input.embedded_manifest]) {
      Object.assign(manifest, {registry_discovery: {goal_runtime_roots: {"goal-a": root}},
        included: [{archive_path: "runtime-root"}]});
    }
    const result = verifyStateRecoveryManifest(input);
    assert.equal(result.ok, true);
    assert.equal(result.execution_authority_granted, false);
    assert.equal(JSON.stringify(result).includes("goal_runtime_roots"), false);
  }
  for (const root of ["/outside", "../outside", "runtime-root/../other", "runtime-root//other", "unlisted", 1, true]) {
    const input = request();
    for (const manifest of [input.external_manifest, input.embedded_manifest]) {
      Object.assign(manifest, {registry_discovery: {goal_runtime_roots: {"goal-a": root}},
        included: [{archive_path: "runtime-root"}]});
    }
    assert.equal(verifyStateRecoveryManifest(input).ok, false, String(root));
  }
});

test("native capture metadata cannot change its Goal/configuration dependency or exceed the historical capacity", () => {
  const original = request();
  const path = `${authorityBackupDirectory("goal-a")}/authority.jsonl`;
  const capture = {goal_id: "goal-a", state: "readable", runtime_archive_path: "runtime-root",
    configuration_sha256: "d".repeat(64), started_at: "2026-10-15T00:00:00.000Z", completed_at: "2026-10-15T00:00:01.000Z",
    archive_path: path, archive: {goal_id: "goal-a", commits: "1", archive_sha256: "e".repeat(64)},
    dependencies: {selector_sha256: null, writer_fence_sha256: null}};
  const artifact = {...member, archive_path: path};
  original.files = [artifact];
  for (const manifest of [original.external_manifest, original.embedded_manifest]) {
    Object.assign(manifest, {registry_discovery: {goal_runtime_roots: {"goal-a": "runtime-root"}},
      included: [{archive_path: "runtime-root"}]});
    Object.assign(manifest.execution, {authority_captures: [capture], file_members: [artifact],
      configuration_backup: {sha256: "d".repeat(64)}});
  }
  // The logical archive seal is deliberately different from the member byte
  // digest: each has its own verifier and must never be substituted for the other.
  assert.equal(verifyStateRecoveryManifest(original).ok, true);
  for (const delta of [{runtime_archive_path: null}, {runtime_archive_path: "other"},
    {configuration_sha256: "f".repeat(64)}, {completed_at: "2026-10-14T00:00:00.000Z"},
    {archive_path: "../outside"}, {archive: {...capture.archive, commits: "20001"}}]) {
    const input = structuredClone(original);
    for (const manifest of [input.external_manifest, input.embedded_manifest]) {
      Object.assign((manifest.execution as any).authority_captures[0], delta);
    }
    assert.equal(verifyStateRecoveryManifest(input).ok, false, JSON.stringify(delta));
  }
  const duplicate = structuredClone(original);
  for (const manifest of [duplicate.external_manifest, duplicate.embedded_manifest]) {
    (manifest.execution as any).authority_captures.push(capture);
  }
  assert.equal(verifyStateRecoveryManifest(duplicate).ok, false);
  assert.equal(verifyStateRecoveryManifest({...original, files: []}).error_code, "authority_capture_member_mismatch");
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

test("historical authority positions stay optional but supplied positions must agree with the inspected history", () => {
  const input = audit();
  input.components[0] = {...input.components[0], component_id: "goal:fixture",
    owner_kind: "coordination_authority", facts: {history_commits: 2}};
  assert.doesNotThrow(() => decodeStateRecoveryAudit(input));
  const position = {provider_revision: `file:2:${"a".repeat(24)}`, cursor: "2"};
  const positioned = (facts: Record<string, unknown>) => ({...input,
    components: [{...input.components[0], facts: {history_commits: 2, ...facts}}]});
  assert.doesNotThrow(() => decodeStateRecoveryAudit(positioned(position)));
  for (const facts of [{provider_revision: position.provider_revision}, {cursor: "2"},
    {...position, cursor: "3"}, {...position, cursor: "02"}, {...position, cursor: 2},
    {...position, provider_revision: "/private/source"}, {...position, provider_revision: "a".repeat(129)}]) {
    assert.throws(() => decodeStateRecoveryAudit(positioned(facts)));
  }
  const wrongOwner = positioned(position);
  wrongOwner.components[0].owner_kind = "configuration";
  assert.throws(() => decodeStateRecoveryAudit(wrongOwner));
});
