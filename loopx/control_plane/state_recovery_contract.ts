/** Shared readback contract for the offline owner and packaged client. */
import {BARE_SHA256_PATTERN} from "./content_digest.ts";
import {EffectRuntimeRequestError} from "./effect_runtime_errors.ts";
import {requireJsonObject, type JsonObject} from "./runtime_decode.ts";

export const STATE_RECOVERY_AUDIT_SCHEMA = "loopx_state_recovery_audit_v0";
export const RECOVERY_COMPONENT_STATES = ["readable", "historical_only", "unknown"] as const;
export type RecoveryComponentState = typeof RECOVERY_COMPONENT_STATES[number];
export interface RecoveryMeasurements extends JsonObject {
  archive_bytes: number;
  regular_file_bytes: number;
  archive_processing_ms: number;
  owner_read_ms: number;
}
export interface RecoveryComponent extends JsonObject {
  component_id: string;
  owner_kind: string;
  state: RecoveryComponentState;
  reason_code: string;
  next_action: string;
  facts: JsonObject;
  dependency_ids: string[];
}
export interface StateRecoveryAudit extends JsonObject {
  schema_version: typeof STATE_RECOVERY_AUDIT_SCHEMA;
  recovery_id: string;
  audit_sha256: string;
  observed_at: string;
  status: "held";
  execution_authority_granted: false;
  activation_eligible: false;
  components: RecoveryComponent[];
  findings: Array<{reason_code: string; owner_kind: string; next_action: string}>;
  summary: Record<RecoveryComponentState, number>;
  measurements?: RecoveryMeasurements;
}
function requireValue(condition: unknown): asserts condition {
  if (!condition) throw new EffectRuntimeRequestError("State recovery audit contract is invalid", "recovery_audit_invalid");
}
function token(value: unknown): boolean {
  return typeof value === "string" && /^[a-z0-9_:.~-]{1,128}$/.test(value);
}
export function isRecoveryDigest(value: unknown): value is string {
  return typeof value === "string" && BARE_SHA256_PATTERN.test(value);
}
/** Display only; unsafe labels retain their opaque component identity. */
export function recoveryGoalLabel(value: string): string | null {
  return /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value) ? value : null;
}
export function decodeRecoveryMeasurements(value: unknown): RecoveryMeasurements {
  const measurements = requireJsonObject(value, "recovery measurements");
  for (const field of ["archive_bytes", "regular_file_bytes", "archive_processing_ms", "owner_read_ms"]) {
    requireValue(Number.isSafeInteger(measurements[field]) && Number(measurements[field]) >= 0);
  }
  return measurements as RecoveryMeasurements;
}
export function decodeStateRecoveryAudit(value: unknown): StateRecoveryAudit {
  const audit = requireJsonObject(value, "state recovery audit");
  requireValue(audit.ok === true && audit.schema_version === STATE_RECOVERY_AUDIT_SCHEMA
    && audit.status === "held" && audit.written === true && audit.manifest_verified === true
    && audit.execution_authority_granted === false && audit.activation_eligible === false
    && isRecoveryDigest(audit.recovery_id) && isRecoveryDigest(audit.audit_sha256));
  requireValue(typeof audit.observed_at === "string" && audit.observed_at.length <= 40
    && Number.isFinite(Date.parse(audit.observed_at)));
  const workspace = requireJsonObject(audit.workspace, "inert recovery workspace");
  requireValue(workspace.isolation === "inert" && workspace.registered === false);
  // Earlier reports remain historical observations; absent measurements are
  // unknown, never synthesized from their timestamp or today's runtime.
  if (audit.measurements !== undefined) decodeRecoveryMeasurements(audit.measurements);
  const backup = requireJsonObject(audit.backup, "recovery backup basis");
  requireValue(backup.schema_version === "loopx_state_backup_v0"
    && isRecoveryDigest(backup.archive_sha256) && isRecoveryDigest(backup.external_manifest_sha256));
  requireValue(Array.isArray(audit.components) && audit.components.length <= 512);
  const ids = new Set<string>();
  const counts = {readable: 0, historical_only: 0, unknown: 0};
  for (const value of audit.components) {
    const row = requireJsonObject(value, "recovery component");
    requireValue(typeof row.component_id === "string" && token(row.component_id) && !ids.has(row.component_id)
      && token(row.owner_kind) && token(row.reason_code) && token(row.next_action)
      && RECOVERY_COMPONENT_STATES.includes(row.state as RecoveryComponentState)
      && Array.isArray(row.dependency_ids) && row.dependency_ids.every(token));
    const facts = requireJsonObject(row.facts, "recovery component facts");
    requireValue(facts.goal_id === undefined || (typeof facts.goal_id === "string" && recoveryGoalLabel(facts.goal_id) === facts.goal_id));
    // Older audits did not retain the inspected position. Never synthesize it
    // from an observation time or a commit count on historical readback.
    if (facts.provider_revision !== undefined || facts.cursor !== undefined) {
      requireValue(row.owner_kind === "coordination_authority" && row.state === "readable"
        && token(facts.provider_revision) && typeof facts.cursor === "string"
        && Number.isSafeInteger(facts.history_commits) && Number(facts.history_commits) > 0
        && facts.cursor === String(facts.history_commits));
    }
    ids.add(row.component_id);
    counts[row.state as RecoveryComponentState]++;
  }
  for (const value of audit.components) {
    const row = value as RecoveryComponent;
    requireValue(row.dependency_ids.every(id => ids.has(id) && id !== row.component_id));
  }
  const summary = requireJsonObject(audit.summary, "recovery summary");
  requireValue(RECOVERY_COMPONENT_STATES.every(state => summary[state] === counts[state]));
  requireValue(Array.isArray(audit.findings));
  for (const value of audit.findings) {
    const finding = requireJsonObject(value, "recovery finding");
    requireValue(token(finding.reason_code) && token(finding.owner_kind) && token(finding.next_action));
  }
  return audit as StateRecoveryAudit;
}
