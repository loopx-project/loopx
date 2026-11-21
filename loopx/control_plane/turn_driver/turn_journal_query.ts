/** Settlement-addressed File journal readback; observations never grant execution. */
import { constants } from "node:fs";
import { open, readdir } from "node:fs/promises";
import { isAbsolute, join } from "node:path";

import {
  settlementIdentityFromPlan,
  settlementIdentityPayload,
  SETTLEMENT_IDENTITY_SCHEMA_VERSION,
  SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION,
  type BoundSettlementIdentity,
  type SettlementIdentity,
  type JsonObject,
} from "../effect_program.ts";
import { EffectRuntimeConflictError, EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { stripPythonWhitespace } from "../coordination/todo_agents.ts";
import { turnPlanWorkSource } from "../quota/turn_envelope.ts";
import {
  parseExactGoalRef,
  type ExactGoalRef,
} from "../goals/goal_instance_identity.ts";
import { jsonObject, parseJsonSource, parsedSourceNumberTokens, requireJsonObject, sourceNumberTokens,
  requireNonEmptyString, type SourceNumbers } from "../runtime_decode.ts";
import {
  interpretTurnJournal,
  parseTurnJournalGoalBinding,
  transactionPhases,
  type TurnJournalInspection,
} from "./turn_journal.ts";
import type {QuotaAccountingOwner} from "../quota/source_admission.ts";
import type {ProviderEffect} from "./settlement_provider.ts";

interface JournalEvidence {
  turn_key: string;
  observed_capabilities: string[] | null;
  journal: JsonObject;
  inspection: TurnJournalInspection;
}

function decodeIdentity(value: unknown): BoundSettlementIdentity {
  const identity = requireJsonObject(value, "settlement_identity");
  if (![SETTLEMENT_IDENTITY_SCHEMA_VERSION, SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION].includes(String(identity.schema_version))) {
    throw new EffectRuntimeRequestError("Turn journal query requires a versioned settlement identity");
  }
  const parsed = settlementIdentityFromPlan({ settlement_plan: { identity } });
  if (parsed.failure !== null) throw new EffectRuntimeRequestError(parsed.failure.reason);
  return parsed.value;
}

function decodeGoalRef(value: unknown): ExactGoalRef {
  const parsed = parseExactGoalRef(value);
  if (parsed.kind === "invalid") {
    throw new EffectRuntimeRequestError(
      `Turn journal query requires an exact GoalRef: ${parsed.issue}`,
    );
  }
  return parsed.value;
}

function journalConflict(message: string): never {
  throw new EffectRuntimeConflictError(message, "journal_readback_conflict");
}

function observedCapabilities(envelope: JsonObject): string[] {
  const observed = new Set<string>();
  function append(value: unknown): void {
    if (!Array.isArray(value)) return;
    for (const item of value) {
      // Historical declarations are string lists. Never coerce an object or
      // boolean into capability evidence. Match Python str.strip for strings.
      if (typeof item !== "string") continue;
      const capability = stripPythonWhitespace(item);
      if (capability) observed.add(capability);
    }
  }
  append(jsonObject(envelope.boundary)?.available_capabilities);
  const gate = jsonObject(envelope.capability_gate);
  if (gate && (gate.missing_capabilities == null ||
      (Array.isArray(gate.missing_capabilities) && gate.missing_capabilities.length === 0))) {
    append(gate.required_capabilities);
  }
  return [...observed];
}

// A lost Host acknowledgement may retain the wake address before the caller
// has seen the selected Todo or replan. Its only permitted result is a locator
// for the original journal. Settlement and capability reads remain fully bound.
type JournalAddress = Pick<BoundSettlementIdentity, "goal_id" | "agent_id" | "turn_instance_id">;

function evidenceForJournal(
  value: unknown,
  turnKey: string,
  expected: JournalAddress | BoundSettlementIdentity,
  expectedGoalRef: ExactGoalRef | null,
  numbers: SourceNumbers,
): JournalEvidence | null {
  const journal = jsonObject(value);
  const plan = jsonObject(journal?.plan);
  const transaction = jsonObject(plan?.transaction);
  const rawIdentity = jsonObject(jsonObject(transaction?.settlement_plan)?.identity);
  // An opaque file cannot establish absence. A known other Turn, including
  // an old unbound Turn, can be ignored without inspecting its execution state.
  const addresses = [transaction?.turn_instance_id, rawIdentity?.turn_instance_id];
  if (!addresses.some(value => typeof value === "string" && value.trim())) {
    journalConflict("Turn journal history has no recovery address; reconcile it before retrying");
  }
  const settlement = "effect_id" in expected ? expected : null;
  if (!addresses.includes(expected.turn_instance_id) &&
      (settlement === null || rawIdentity?.effect_id !== settlement.effect_id)) return null;
  if (journal?.schema_version !== "loopx_turn_journal_v0" || !transaction) {
    journalConflict("Turn journal history has an unsupported snapshot; reconcile it before retrying");
  }
  const parsed = settlementIdentityFromPlan(transaction);
  if (parsed.failure !== null) {
    journalConflict(`Turn journal settlement identity is invalid: ${parsed.failure.reason}`);
  }
  const actual = parsed.value;
  // effect_id alone is insufficient: legacy delimiter-based identities can
  // have the same rendered id while binding different structured fields.
  if (actual.goal_id !== expected.goal_id || actual.agent_id !== expected.agent_id ||
      actual.turn_instance_id !== expected.turn_instance_id) return null;
  if (settlement !== null && (actual.binding_kind !== settlement.binding_kind ||
      actual.binding_id !== settlement.binding_id || actual.effect_id !== settlement.effect_id)) return null;
  const goalBinding = parseTurnJournalGoalBinding(journal);
  if (goalBinding.kind === "invalid") {
    journalConflict(`Turn journal GoalRef binding is invalid: ${goalBinding.violation}`);
  }
  if (expectedGoalRef !== null) {
    if (goalBinding.kind === "legacy") return null;
    if (
      goalBinding.goal_ref.goal_id !== expectedGoalRef.goalId.value
      || goalBinding.goal_ref.goal_instance_id !==
        expectedGoalRef.goalInstanceId.value
    ) return null;
  }
  const inspection = interpretTurnJournal({
    schema_version: "loopx_turn_journal_interpretation_request_v0",
    journal, goal_id: expected.goal_id, agent_id: expected.agent_id, turn_key: turnKey,
    source_number_tokens: parsedSourceNumberTokens(journal, numbers),
  });
  if (!inspection.journal_consistent) {
    journalConflict(`Turn journal history is inconsistent: ${inspection.violations.join(", ")}`);
  }
  return {
    turn_key: turnKey,
    journal,
    inspection,
    observed_capabilities: inspection.replay_legal
      ? observedCapabilities(turnPlanWorkSource(plan ?? {}, sourceNumberTokens(journal,
        parsedSourceNumberTokens(journal, numbers)).filter(({path}) => path[0] === "plan" && path[1] === "work_contract")
        .map(({path, token}) => [path.slice(2), token])).work) : null,
  };
}

/** Resolve only the original journal's next terminal provider. The enclosing
 * quota reader already owns source admission; this helper opens no owner and
 * returns the existing settlement reducer's effect rather than a new grant. */
export async function readTurnTerminalCloseoutProvider(
  runtimeRoot: string,
  identity: SettlementIdentity,
  owner: QuotaAccountingOwner,
  context: {turn_key: string; effect_ref: string | null},
  todo: JsonObject,
): Promise<ProviderEffect | null> {
  if (identity.binding_kind !== "todo") return null;
  const evidence = await queryTurnJournal(runtimeRoot, identity,
    owner.kind === "exact_source" ? owner.goalRef : null);
  if (evidence === null || evidence.turn_key !== context.turn_key) {
    throw new EffectRuntimeRequestError("Terminal provider journal does not match this Turn",
      "refresh_task_lease_unavailable");
  }
  const {journal, inspection} = evidence;
  const goalBinding = parseTurnJournalGoalBinding(journal);
  if (owner.kind === "alias" && goalBinding.kind !== "legacy") {
    throw new EffectRuntimeRequestError("Terminal provider cannot cross Goal ownership",
      "refresh_task_lease_unavailable");
  }
  if (inspection.recovery_decision.action !== "continue"
      || inspection.recovery_decision.reinvoke_host
      || !inspection.completed_phases.includes("quota_spend")
      || jsonObject(journal.host_result)?.result_kind !== "validated_completion") return null;
  const [{projectCompletionEvidence}, {reduceTurnSettlementTransaction, TURN_SETTLEMENT_TRANSACTION_SCHEMA_VERSION}] = await Promise.all([
    import("../todos/read_projection.ts"), import("./settlement.ts"),
  ]);
  const intent = projectCompletionEvidence({operation: "intent", todo, expected_todo_id: identity.todo_id});
  if (intent.continuation !== "no_followup") return null;
  const transaction = jsonObject(jsonObject(journal.plan)?.transaction);
  const reduction = reduceTurnSettlementTransaction({schema_version: TURN_SETTLEMENT_TRANSACTION_SCHEMA_VERSION,
    transaction_plan: transaction, transaction_phases: transactionPhases,
    completed_phases: inspection.completed_phases, committed_effect_id: identity.effect_id,
    writeback_payload: journal.writeback ?? null, quota_spend_payload: journal.quota_spend ?? null,
    terminal_closeout_required: true, terminal_closeout_payload: journal.terminal_closeout ?? null,
    effect_attempts: journal.effect_attempts ?? {}, turn_result_kind: "validated_completion"});
  if (reduction.decision !== "execute" || reduction.provider_effects.length !== 1) return null;
  const effect = reduction.provider_effects[0]!;
  if (effect.step_kind !== "terminal_closeout"
      || (effect.action !== "prepare_and_execute" && effect.action !== "resolve_prepared"
        && effect.action !== "execute_prepared")) return null;
  if (context.effect_ref !== null && context.effect_ref !== effect.effect_ref) {
    throw new EffectRuntimeRequestError("Terminal provider reference differs from this Turn's next effect",
      "refresh_task_lease_unavailable");
  }
  return effect;
}

async function queryTurnJournal(
  runtimeRootValue: unknown,
  identity: JournalAddress | BoundSettlementIdentity,
  goalRef: ExactGoalRef | null,
  legacyOnly = false,
): Promise<JournalEvidence | null> {
  const runtimeRoot = requireNonEmptyString(runtimeRootValue, "runtime_root");
  if (!isAbsolute(runtimeRoot) || runtimeRoot.includes("\0")) {
    throw new EffectRuntimeRequestError("Turn journal runtime_root must be an absolute path");
  }
  if ([".", ".."].includes(identity.goal_id) || /[/\\\0]/u.test(identity.goal_id)) {
    throw new EffectRuntimeRequestError("Turn journal goal_id must be a single path segment");
  }
  const directory = join(runtimeRoot, "goals", identity.goal_id, "turns");
  let entries;
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
  let matched: JournalEvidence | null = null;
  for (const entry of entries.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)) {
    if (!/^[a-f0-9]{64}\.json$/u.test(entry.name)) continue;
    if (!entry.isFile()) journalConflict("Turn journal history contains a non-regular snapshot; reconcile it before retrying");
    let parsed: ReturnType<typeof parseJsonSource>;
    // Journals are atomic-replace files. Read one complete version without
    // creating reader lock files; do not follow a replaced symlink. This is
    // not a snapshot of the directory or proof of provider-side absence.
    const handle = await open(join(directory, entry.name), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    try {
      if (!(await handle.stat()).isFile()) journalConflict("Turn journal snapshot is not a regular file");
      try {
        parsed = parseJsonSource(new TextDecoder("utf-8", { fatal: true }).decode(await handle.readFile()));
      } catch (error) {
        if (error instanceof SyntaxError || error instanceof TypeError) journalConflict("Turn journal history is unreadable; reconcile it before retrying");
        throw error;
      }
    } finally {
      await handle.close();
    }
    const evidence = evidenceForJournal(
      parsed.value,
      `sha256:${entry.name.slice(0, -5)}`,
      identity,
      goalRef,
      parsed.numbers,
    );
    if (evidence === null || (legacyOnly && parseTurnJournalGoalBinding(evidence.journal).kind !== "legacy")) continue;
    if (matched !== null) journalConflict("LoopX Turn settlement identity matched multiple journals");
    matched = evidence;
  }
  return matched;
}

export async function findTurnJournalBySettlement(params: JsonObject): Promise<{ turn_key: string | null }> {
  const identity = decodeIdentity(settlementIdentityPayload({
    goal_id: requireNonEmptyString(params.goal_id, "goal_id"),
    agent_id: requireNonEmptyString(params.agent_id, "agent_id"),
    todo_id: params.todo_id == null ? null : requireNonEmptyString(params.todo_id, "todo_id"),
    request_subject: params.request_subject as import("../effect_program.ts").ExecutionRequestSubject | undefined,
    turn_instance_id: requireNonEmptyString(params.turn_instance_id, "turn_instance_id"),
  }));
  const goalRef = Object.hasOwn(params, "goal_ref") ? decodeGoalRef(params.goal_ref) : null;
  if (goalRef !== null && goalRef.goalId.value !== identity.goal_id) {
    throw new EffectRuntimeRequestError("Turn journal query GoalRef conflicts with its settlement identity");
  }
  const evidence = await queryTurnJournal(params.runtime_root, identity, goalRef);
  return { turn_key: evidence?.turn_key ?? null };
}

/** A recovery inspection uses its original parsed owner. Historical lookup
 * keeps its wildcard default; this locator never lends another Goal lifetime. */
export async function readTurnJournalRecoveryLocator(
  runtimeRoot: string,
  identity: SettlementIdentity,
  owner: QuotaAccountingOwner,
): Promise<string | null> {
  const evidence = await queryTurnJournal(runtimeRoot, identity,
    owner.kind === "exact_source" ? owner.goalRef : null, owner.kind === "alias");
  return evidence?.turn_key ?? null;
}

export async function findTurnJournalByInstance(params: JsonObject): Promise<{ turn_key: string | null }> {
  const address = {
    goal_id: requireNonEmptyString(params.goal_id, "goal_id"),
    agent_id: requireNonEmptyString(params.agent_id, "agent_id"),
    turn_instance_id: requireNonEmptyString(params.turn_instance_id, "turn_instance_id"),
  };
  const evidence = await queryTurnJournal(params.runtime_root, address, null);
  return { turn_key: evidence?.turn_key ?? null };
}

export async function readTurnJournalCapabilities(params: JsonObject): Promise<{ observed_capabilities: string[] | null }> {
  const identity = decodeIdentity(params.settlement_identity);
  const goalRef = Object.hasOwn(params, "goal_ref")
    ? decodeGoalRef(params.goal_ref)
    : null;
  if (goalRef !== null && goalRef.goalId.value !== identity.goal_id) {
    throw new EffectRuntimeRequestError(
      "Turn journal query GoalRef conflicts with its settlement identity",
    );
  }
  const evidence = await queryTurnJournal(
    params.runtime_root,
    identity,
    goalRef,
  );
  return { observed_capabilities: evidence?.observed_capabilities ?? null };
}
