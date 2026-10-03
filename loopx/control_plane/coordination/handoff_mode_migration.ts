/** Reviewed ownership upgrade. Portable backup and domain rules share their
 * existing owners; the policy change is one provider CAS, never a replayed store. */
import {open, readFile} from "node:fs/promises";
import {dirname, isAbsolute} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireStringArray, requireStringLiteral} from "../runtime_decode.ts";
import {parseIsoTimestamp} from "../runtime_timestamp.ts";
import type {AuthorityStore} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthoritySha256 as sha256,
  hasExactAuthorityKeys, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {exportAuthorityArchive, verifyAuthorityArchive, type AuthorityArchiveSummary} from "./authority_archive.ts";
import {syncAuthorityDirectory} from "./file_authority_store.ts";
import {CoordinationCommandReceipt} from "./command_receipt.ts";
import {HANDOFF_MODES, EXECUTION_HANDOFF_MODES} from "./handoff_mode_policy.ts";
import {planHandoffPolicyMigration} from "./handoff_policy_migration.ts";
import {normalizeRegisteredTodoAgents} from "./todo_agents.ts";

const PLAN_SCHEMA = "loopx_handoff_mode_migration_plan_v0";
const RESULT_SCHEMA = "loopx_handoff_mode_migration_result_v0";
const RECEIPT_SCHEMA = "loopx_handoff_mode_migration_receipt_v0";
export interface HandoffModeMigrationInput {
  goal_id: string;
  runtime_root: string;
  plan: string;
  registered_agents: readonly string[];
  observed_at: string;
}
interface ReviewedPlan extends JsonObject {
  schema_version: typeof PLAN_SCHEMA;
  goal_id: string;
  runtime_root: string;
  requested_mode: typeof EXECUTION_HANDOFF_MODES[number];
  registered_agents: string[];
  source: JsonObject;
}
function fail(code: string, reason: string): JsonObject & {schema_version: typeof RESULT_SCHEMA} {
  return {schema_version: RESULT_SCHEMA, status: "failed", changed: false,
    reason_code: code, reason, execution_authority_granted: false};
}
function validate(input: HandoffModeMigrationInput) {
  requireAuthorityStoreId(input.goal_id, "goal id");
  if (!isAbsolute(input.runtime_root) || !isAbsolute(input.plan)) throw new Error("migration paths must be absolute");
  const observed = parseIsoTimestamp(input.observed_at);
  if (!observed) throw new Error("observed_at must be an ISO timestamp");
  return {observed, agents: normalizeRegisteredTodoAgents(input.registered_agents)};
}
function decodePlan(value: unknown): ReviewedPlan {
  const plan = requireJsonObject(value, "handoff migration plan");
  if (!hasExactAuthorityKeys(plan, ["schema_version", "goal_id", "runtime_root", "requested_mode",
    "registered_agents", "source"]) || plan.schema_version !== PLAN_SCHEMA) throw new Error("invalid migration plan");
  requireAuthorityStoreId(plan.goal_id, "goal id");
  if (typeof plan.runtime_root !== "string" || !isAbsolute(plan.runtime_root)) throw new Error("invalid plan runtime");
  requireStringLiteral(plan.requested_mode, EXECUTION_HANDOFF_MODES, "requested_mode");
  normalizeRegisteredTodoAgents(requireStringArray(plan.registered_agents, "registered_agents"));
  const source = requireJsonObject(plan.source, "migration source");
  if (!hasExactAuthorityKeys(source, ["store_identity", "provider_revision", "cursor", "projection_sha256"]) ||
      Object.values(source).some(v => typeof v !== "string" || !v)) throw new Error("invalid migration source");
  return plan as ReviewedPlan;
}
async function observe(store: AuthorityStore) {
  const identity = await store.storeIdentity();
  const loaded = await store.loadAuthority();
  if (identity.status !== "available" || loaded.status !== "loaded") throw new Error("canonical authority unavailable");
  return {loaded, source: {store_identity: identity.store_identity, provider_revision: loaded.provider_revision,
    cursor: loaded.cursor, projection_sha256: sha256(loaded.head)}};
}
function checkBackup(archive: AuthorityArchiveSummary, plan: ReviewedPlan): void {
  if (archive.goal_id !== plan.goal_id || archive.source_store_identity !== plan.source.store_identity ||
      archive.source_provider_revision !== plan.source.provider_revision || archive.commits !== plan.source.cursor ||
      archive.projection_sha256 !== plan.source.projection_sha256) throw new Error("backup does not match reviewed source");
}

export async function planHandoffModeMigration(store: AuthorityStore,
  input: HandoffModeMigrationInput & {requested_mode: string}): Promise<JsonObject> {
  try {
    const {observed, agents} = validate(input);
    const mode = requireStringLiteral(input.requested_mode, EXECUTION_HANDOFF_MODES, "requested_mode");
    const {loaded, source} = await observe(store);
    const policy = planHandoffPolicyMigration(loaded.head, input.goal_id, mode, agents, observed);
    if (!policy.ready) return {...fail(policy.reason_code!, policy.reason!), conflicts: [...policy.conflicts]};
    const plan: ReviewedPlan = {schema_version: PLAN_SCHEMA, goal_id: input.goal_id,
      runtime_root: input.runtime_root, requested_mode: mode, registered_agents: [...agents], source};
    // The reviewed carrier is immutable. A different preview needs a new path.
    const file = await open(input.plan, "wx", 0o600);
    try { await file.writeFile(JSON.stringify(plan) + "\n"); await file.sync(); }
    finally { await file.close(); }
    await syncAuthorityDirectory(dirname(input.plan));
    return {schema_version: RESULT_SCHEMA, status: "planned", changed: false, plan, plan_sha256: sha256(plan),
      previous_mode: policy.previous_mode, handoff_mode: mode, preserved_claim_count: policy.preserved_claims.length,
      lease_dispositions: [...policy.lease_dispositions], execution_authority_granted: false, requires_execute: true};
  } catch (error) { return fail("handoff_mode_migration_rejected", String(error)); }
}

export async function executeHandoffModeMigration(store: AuthorityStore,
  input: HandoffModeMigrationInput & {plan_sha256: string; execute: boolean}): Promise<JsonObject> {
  try {
    const {observed, agents} = validate(input);
    if (typeof input.execute !== "boolean") throw new Error("execute must be boolean");
    const plan = decodePlan(JSON.parse(await readFile(input.plan, "utf8")));
    const digest = sha256(plan);
    if (input.plan_sha256 !== digest || plan.goal_id !== input.goal_id || plan.runtime_root !== input.runtime_root) {
      throw new Error("reviewed plan digest, Goal or runtime mismatch");
    }
    const operationId = `handoff-mode-migrate:${digest}`;
    const backupPath = `${input.plan}.backup.jsonl`;
    const receipt = new CoordinationCommandReceipt({result_schema: RESULT_SCHEMA,
      identity: {schema_version: RECEIPT_SCHEMA, goal_id: input.goal_id, operation_id: operationId, request_sha256: digest},
      failure: fail,
      decode(original) {
        const decision = canonicalAuthorityObject(original.decision, "migration decision");
        if (decision.goal_id !== plan.goal_id || decision.operation_id !== operationId ||
            decision.handoff_mode !== plan.requested_mode || typeof decision.changed !== "boolean" ||
            !HANDOFF_MODES.includes(decision.previous_mode as typeof HANDOFF_MODES[number]) ||
            decision.changed !== (decision.previous_mode !== decision.handoff_mode) ||
            typeof decision.backup_archive_sha256 !== "string" || decision.plan_sha256 !== digest ||
            typeof decision.preserved_claim_count !== "number" || !Number.isSafeInteger(decision.preserved_claim_count) ||
            decision.preserved_claim_count < 0) throw new AuthorityStoreProtocolError("invalid migration receipt");
        return {fields: decision, changed: decision.changed};
      }});
    let backup: AuthorityArchiveSummary | undefined;
    try { backup = await verifyAuthorityArchive(backupPath); checkBackup(backup, plan); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    const replay = await receipt.read(store);
    if (replay) {
      if (!backup || replay.backup_archive_sha256 !== backup.archive_sha256) throw new Error("committed migration backup is missing or changed");
      return {...replay, backup_path: backupPath, execution_authority_granted: false};
    }
    if (sha256(agents) !== sha256(plan.registered_agents)) throw new Error("registered agents changed; review a fresh plan");
    const current = await observe(store);
    if (sha256(current.source) !== sha256(plan.source)) throw new Error("authority changed; review a fresh plan");
    const policy = planHandoffPolicyMigration(current.loaded.head, input.goal_id, plan.requested_mode, agents, observed);
    if (!policy.ready) return {...fail(policy.reason_code!, policy.reason!), conflicts: [...policy.conflicts]};
    if (!input.execute) return {schema_version: RESULT_SCHEMA, status: "planned", changed: false,
      plan_sha256: digest, requires_execute: true, execution_authority_granted: false};
    // Backup is published only after independently verifying the entire journal,
    // including original receipts, Todo metadata and retained projections.
    backup ??= await exportAuthorityArchive(store, input.goal_id, backupPath);
    checkBackup(backup, plan);
    const decision = {goal_id: input.goal_id, operation_id: operationId, previous_mode: policy.previous_mode,
      handoff_mode: plan.requested_mode, changed: policy.changed, plan_sha256: digest,
      backup_archive_sha256: backup.archive_sha256, preserved_claim_count: policy.preserved_claims.length};
    // Cross-host writes during backup remain subject to the exact reviewed CAS.
    const result = await receipt.commit(store, {operation_id: operationId,
      expected_provider_revision: current.loaded.provider_revision, next_projection: policy.target_projection,
      events: policy.changed ? [{schema_version: "loopx_handoff_mode_changed_v0", ...decision}] : [],
      receipts: [{schema_version: RECEIPT_SCHEMA, goal_id: input.goal_id, operation_id: operationId,
        request_sha256: digest, decision}]});
    return {...result, backup_path: backupPath, execution_authority_granted: false};
  } catch (error) { return fail("handoff_mode_migration_rejected", String(error)); }
}
