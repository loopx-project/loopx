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
  type JsonObject,
} from "../effect_program.ts";
import { EffectRuntimeConflictError, EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { stripPythonWhitespace } from "../coordination/todo_agents.ts";
import {
  parseExactGoalRef,
  type ExactGoalRef,
} from "../goals/goal_instance_identity.ts";
import { jsonObject, requireJsonObject, requireNonEmptyString } from "../runtime_decode.ts";
import {
  interpretTurnJournal,
  parseTurnJournalGoalBinding,
} from "./turn_journal.ts";

interface JournalEvidence {
  turn_key: string;
  observed_capabilities: string[] | null;
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

function evidenceForJournal(
  value: unknown,
  turnKey: string,
  expected: BoundSettlementIdentity,
  expectedGoalRef: ExactGoalRef | null,
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
  if (!addresses.includes(expected.turn_instance_id) && rawIdentity?.effect_id !== expected.effect_id) return null;
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
      actual.binding_kind !== expected.binding_kind || actual.binding_id !== expected.binding_id ||
      actual.turn_instance_id !== expected.turn_instance_id || actual.effect_id !== expected.effect_id) return null;
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
  });
  if (!inspection.journal_consistent) {
    journalConflict(`Turn journal history is inconsistent: ${inspection.violations.join(", ")}`);
  }
  return {
    turn_key: turnKey,
    observed_capabilities: inspection.replay_legal
      ? observedCapabilities(jsonObject(plan?.turn_envelope) ?? {}) : null,
  };
}

async function querySettlementJournal(
  runtimeRootValue: unknown,
  identity: BoundSettlementIdentity,
  goalRef: ExactGoalRef | null,
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
    let value: unknown;
    // Journals are atomic-replace files. Read one complete version without
    // creating reader lock files; do not follow a replaced symlink. This is
    // not a snapshot of the directory or proof of provider-side absence.
    const handle = await open(join(directory, entry.name), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    try {
      if (!(await handle.stat()).isFile()) journalConflict("Turn journal snapshot is not a regular file");
      try {
        value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(await handle.readFile()));
      } catch (error) {
        if (error instanceof SyntaxError || error instanceof TypeError) journalConflict("Turn journal history is unreadable; reconcile it before retrying");
        throw error;
      }
    } finally {
      await handle.close();
    }
    const evidence = evidenceForJournal(
      value,
      `sha256:${entry.name.slice(0, -5)}`,
      identity,
      goalRef,
    );
    if (evidence === null) continue;
    if (matched !== null) journalConflict("LoopX Turn settlement identity matched multiple journals");
    matched = evidence;
  }
  return matched;
}

export async function findTurnJournalBySettlement(params: JsonObject): Promise<{ turn_key: string | null }> {
  const identity = decodeIdentity(settlementIdentityPayload({
    goal_id: requireNonEmptyString(params.goal_id, "goal_id"),
    agent_id: requireNonEmptyString(params.agent_id, "agent_id"),
    todo_id: requireNonEmptyString(params.todo_id, "todo_id"),
    turn_instance_id: requireNonEmptyString(params.turn_instance_id, "turn_instance_id"),
  }));
  const evidence = await querySettlementJournal(params.runtime_root, identity, null);
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
  const evidence = await querySettlementJournal(
    params.runtime_root,
    identity,
    goalRef,
  );
  return { observed_capabilities: evidence?.observed_capabilities ?? null };
}
