/** Settlement-addressed File journal readback; observations never grant execution. */
import { constants, type Dirent } from "node:fs";
import {createHash} from "node:crypto";
import { open, readdir, realpath } from "node:fs/promises";
import { isAbsolute, join } from "node:path";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {fileURLToPath} from "node:url";

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
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";

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

async function journalSnapshot(path: string): Promise<{value: JsonObject; sha256: string}> {
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    if (!(await handle.stat()).isFile()) journalConflict("Turn journal snapshot is not a regular file");
    const bytes = await handle.readFile();
    let value: JsonObject;
    try { value = requireJsonObject(JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes)), "Turn journal"); }
    catch { journalConflict("Turn journal history is unreadable; reconcile it before retrying"); }
    return {value, sha256: createHash("sha256").update(bytes).digest("hex")};
  } finally { await handle.close(); }
}

/** Call while source preparation owns Goal maintenance. The existing journal
 * interpreter decides terminal/uncertain effects. Actual process readback is a
 * separate requirement: a terminal journal cannot certify its Host exited.
 * This does not cover Hosts outside Turn execution or external effect owners.
 */
export async function readGoalTurnHistoryForTransfer(root: string, goalId: string): Promise<JsonObject> {
  const directory = join(root, "goals", goalId, "turns");
  let entries: Dirent[];
  try {
    if (await realpath(directory) !== directory) journalConflict("Transfer Turn history contains a linked ancestor");
    entries = await readdir(directory, {withFileTypes: true});
  }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") entries = []; else throw error; }
  const turns: JsonObject[] = [];
  for (const entry of entries.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)) {
    if (!entry.name.endsWith(".json")) continue;
    if (!/^[a-f0-9]{64}\.json$/u.test(entry.name) || !entry.isFile()) journalConflict("Transfer source has an unattributable Turn journal; reconcile its owning operation");
    const {value: journal, sha256} = await journalSnapshot(join(directory, entry.name));
    const key = `sha256:${entry.name.slice(0, -5)}`;
    const envelope = jsonObject(jsonObject(journal.plan)?.turn_envelope);
    const agent = envelope?.agent_id;
    if (typeof agent !== "string" || !agent) journalConflict("Transfer source Turn has no original owner");
    const observed = interpretTurnJournal({schema_version: "loopx_turn_journal_interpretation_request_v0",
      journal, goal_id: goalId, agent_id: agent, turn_key: key});
    turns.push({turn_key: key, journal_sha256: sha256, host_attempt_count: journal.host_attempt_count ?? null,
      closed: observed.journal_consistent && ["committed", "stopped"].includes(observed.journal_status) &&
        observed.recovery_decision.action === "return_existing",
      recorded_effects: observed.recorded_effects, violations: observed.violations});
  }
  let hosts: JsonObject[] = [];
  if (turns.length || entries.some(entry => entry.name.endsWith(".hosts"))) {
    const python = process.env.LOOPX_EFFECT_RUNTIME_PYTHON;
    if (!python || !isAbsolute(python) || python.includes("\0")) journalConflict("Host drain requires the source-selected process owner adapter");
    try {
      const result = await promisify(execFile)(python, ["-m", "loopx.control_plane.turn_driver.turn_host_process", root, goalId], {
        env: {...process.env, PYTHONPATH: fileURLToPath(new URL("../../../", import.meta.url))},
        timeout: 30_000, maxBuffer: 16 * 1024 * 1024,
      });
      const rows: unknown = JSON.parse(result.stdout);
      if (!Array.isArray(rows)) journalConflict("Invalid Host process readback");
      hosts = rows.map(row => requireJsonObject(row, "original Host process observation"));
    } catch { journalConflict("Host process readback is unavailable; recover the original Host before transfer"); }
  }
  const addresses = new Set<string>();
  for (const host of hosts) {
    const turn = turns.find(row => row.turn_key === host.turn_key);
    const key = `${String(host.turn_key)}:${String(host.attempt)}`;
    if (!turn || !Number.isSafeInteger(host.attempt) || Number(host.attempt) < 1 || addresses.has(key) ||
        !Number.isSafeInteger(turn.host_attempt_count) || Number(host.attempt) > Number(turn.host_attempt_count) ||
        typeof host.drain !== "string" || [host.record_sha256, host.supervisor_sha256].some(
          value => value !== null && (typeof value !== "string" || !BARE_SHA256_PATTERN.test(value)))) {
      journalConflict("Host process evidence has no matching original Turn attempt; reconcile its owner");
    }
    addresses.add(key);
  }
  const turnHostsClosed = turns.every(turn => {
    const count = turn.host_attempt_count;
    const recorded = turn.recorded_effects as JsonObject;
    const actual = hosts.filter(host => host.turn_key === turn.turn_key);
    // The journal owner may prove no attempt, but a missing attempt counter
    // beside a recorded invocation is historical uncertainty, never absence.
    if ((count === null || count === 0) && recorded.host_invoked === false) return actual.length === 0;
    return Number.isSafeInteger(count) && Number(count) > 0 && actual.length === count &&
      actual.every(host => host.record_sha256 !== null && ["drained", "not_launched"].includes(String(host.drain)));
  });
  const facts = {turns, hosts, turn_hosts_closed: turnHostsClosed};
  return {...facts, closed: turns.every(row => row.closed === true), sha256: canonicalAuthoritySha256(facts)};
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
    // Journals are atomic-replace files. Read one complete version without
    // creating reader lock files; do not follow a replaced symlink. This is
    // not a snapshot of the directory or proof of provider-side absence.
    const {value} = await journalSnapshot(join(directory, entry.name));
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
