import {HANDOFF_MODES} from "../coordination/handoff_mode_vocabulary.ts";
import {leaseWorkspace, sameLeaseWorkspace, sameLeaseCheckout, type LeaseWorkspace} from "./task_lease_workspace.ts";
/** Shared acquire/reclaim admission. IO and durable receipts belong to callers. */
import {retiredHandoffModeRejection} from "../coordination/handoff_mode_policy.ts";
import {leaseOwnerRejection as ownerRejection} from "./task_lease_eligibility.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, requireStringLiteral} from "../runtime_decode.ts";
import type {JsonObject} from "../effect_program.ts";
import type {TodoFact, LeaseRecord} from "./task_lease_acquire.ts";
import {normalizeTodoRepository} from "../todos/work_requirements.ts";
import {leaseWriteRepository, leaseRepositoryRejection, repositoryScopesMayOverlap} from "./task_lease_repository.ts";

export const TODO_WRITE_SCOPE_MAX_CHARS = 160;

export interface AcquireDecisionLease {
  present: boolean;
  active: boolean;
  status: string | null;
  owner: string | null;
  idempotency_key: string | null;
  version: number;
  lease_epoch: number;
  write_scopes: readonly string[];
  write_repository?: string | null;
  write_workspace?: LeaseWorkspace | null;
  acquire_ttl_seconds: number | null;
}

export interface AcquireDecisionOtherLease {
  todo_id: string;
  owner?: string;
  active: boolean;
  effective: boolean;
  write_scopes: readonly string[];
  write_repository?: string | null;
  write_workspace?: LeaseWorkspace | null;
}

export interface AcquireDecisionInput {
  handoff_mode: string;
  registered_agents: readonly string[];
  todo: TodoFact | null;
  lease: AcquireDecisionLease | null;
  other_leases: readonly AcquireDecisionOtherLease[];
  command: {
    owner: string;
    idempotency_key: string;
    ttl_seconds: number;
    write_scopes: readonly string[];
    expected_version: number | null;
    write_workspace?: LeaseWorkspace | null;
  };
}

export interface AcquireDecision extends JsonObject {
  outcome: "apply" | "no_change" | "conflict" | "rejected";
  code: string;
  idempotent: boolean;
  next_lease: JsonObject | null;
  conflict_indexes: number[];
  overlap_advisory_indexes?: number[];
}

function stringValue(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string`);
  }
  return value;
}

function characterClassEnd(pattern: string, start: number): number {
  let end = start + 1;
  if (pattern[end] === "!") end += 1;
  if (pattern[end] === "]") end += 1;
  return pattern.indexOf("]", end);
}

/**
 * Canonicalize one repository-relative scope without resolving it against a
 * host filesystem. Glob character classes retain their own slash semantics.
 */
export function canonicalWriteScope(value: string): string | null {
  const scope = value.trim();
  if (
    scope.length === 0 || scope.length > TODO_WRITE_SCOPE_MAX_CHARS ||
    scope.startsWith("/") || scope.startsWith("~") || scope.includes("\\") ||
    /[\s<>]/u.test(scope)
  ) {
    return null;
  }
  const parts: string[] = [];
  let start = 0;
  for (let index = 0; index < scope.length; index += 1) {
    if (scope[index] === "[") {
      const closing = characterClassEnd(scope, index);
      if (closing >= 0) {
        index = closing;
        continue;
      }
    }
    if (scope[index] !== "/") continue;
    parts.push(scope.slice(start, index));
    start = index + 1;
  }
  parts.push(scope.slice(start));
  const trailingSlash = scope.endsWith("/");
  const canonical: string[] = [];
  for (const part of parts) {
    if (part.length === 0 || part === ".") continue;
    if (part === "..") return null;
    canonical.push(part);
  }
  if (canonical.length === 0) return trailingSlash ? "./" : null;
  return `${canonical.join("/")}${trailingSlash ? "/" : ""}`;
}

/**
 * Build a conflict-comparison view for persisted scopes without rewriting the
 * retained record. An unreadable legacy scope blocks new work conservatively.
 */
export function comparisonWriteScopes(scopes: readonly string[]): string[] {
  const canonical: string[] = [];
  const seen = new Set<string>();
  for (const raw of scopes) {
    const scope = canonicalWriteScope(raw);
    if (scope === null) return ["**"];
    if (!seen.has(scope)) {
      seen.add(scope);
      canonical.push(scope);
    }
  }
  return canonical;
}

function optionalInteger(value: unknown, label: string): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new EffectRuntimeRequestError(`${label} must be an integer or null`);
  }
  return value;
}

/**
 * Canonical fold for one code point, iterated until it is stable. Host case
 * aliases are symmetric (Σ/ς, K/KELVIN SIGN) and some change length (ß -> ss),
 * so one upper/lower pass is not an equivalence relation on its own.
 */
function foldCharacter(character: string): string {
  let folded = character;
  // Two passes settle every code point in current Unicode data; the bound only
  // guards against a future mapping that never reaches a fixed point.
  for (let pass = 0; pass < 4; pass += 1) {
    const next = folded.toUpperCase().toLowerCase();
    if (next === folded) return folded;
    folded = next;
  }
  return folded;
}

/**
 * Folding one code point at a time keeps the fold composable across a path, so a
 * comparison-only view can fold substrings without changing the retained
 * spelling or the immutable request identity.
 */
function caseFold(value: string): string {
  let folded = "";
  for (const character of value) folded += foldCharacter(character);
  return folded;
}

/** The code point of single-character text, or null for longer text. */
function singleCodePoint(value: string): string | null {
  const code = value.codePointAt(0);
  if (code === undefined) return null;
  const character = String.fromCodePoint(code);
  return character.length === value.length ? character : null;
}

type GlobToken =
  | {kind: "literal"; folded: string}
  | {kind: "star"}
  | {kind: "question"}
  | {kind: "class"; body: string; negated: boolean};

function globTokens(pattern: string): GlobToken[] {
  const tokens: GlobToken[] = [];
  let literal = "";
  const flushLiteral = () => {
    if (literal.length === 0) return;
    tokens.push({kind: "literal", folded: caseFold(literal)});
    literal = "";
  };
  for (let index = 0; index < pattern.length; index += 1) {
    const character = pattern[index];
    if (character === "*") {
      flushLiteral();
      tokens.push({kind: "star"});
      continue;
    }
    if (character === "?") {
      flushLiteral();
      tokens.push({kind: "question"});
      continue;
    }
    if (character === "[") {
      const end = characterClassEnd(pattern, index);
      if (end >= 0) {
        flushLiteral();
        let body = pattern.slice(index + 1, end);
        const negated = body.startsWith("!");
        if (negated) body = body.slice(1);
        tokens.push({kind: "class", body, negated});
        index = end;
        continue;
      }
    }
    literal += character;
  }
  flushLiteral();
  return tokens;
}

type CharacterClass = {
  literals: readonly string[];
  ranges: readonly (readonly [number, number])[];
};

/** Class members are code points, and a range keeps its original interval bounds. */
function characterClass(body: string): CharacterClass {
  const members = [...body];
  const literals: string[] = [];
  const ranges: Array<readonly [number, number]> = [];
  for (let index = 0; index < members.length; index += 1) {
    const high = index + 2 < members.length && members[index + 1] === "-"
      ? members[index + 2].codePointAt(0)
      : undefined;
    const low = high === undefined ? undefined : members[index].codePointAt(0);
    if (low !== undefined && high !== undefined) {
      ranges.push([low, high]);
      index += 2;
    } else {
      literals.push(members[index]);
    }
  }
  return {literals, ranges};
}

function classContains(members: CharacterClass, character: string): boolean {
  const code = character.codePointAt(0);
  if (code === undefined) return false;
  return members.literals.includes(character) ||
    members.ranges.some(([low, high]) => low <= code && code <= high);
}

/**
 * Code points whose canonical fold is not themselves, grouped by fold value.
 * Class membership needs this inverse: walking an interval is unbounded, while a
 * fold class holds at most a handful of code points, so an interval of any width
 * is decided by a few comparisons. The inverse is only discoverable by folding
 * the code space, so it is built once on first use.
 */
let foldPreimages: ReadonlyMap<string, readonly string[]> | null = null;

function foldPreimagesByFold(): ReadonlyMap<string, readonly string[]> {
  const cached = foldPreimages;
  if (cached !== null) return cached;
  const preimages = new Map<string, string[]>();
  for (let start = 0; start <= 0x10ffff; start += 0x400) {
    const end = Math.min(start + 0x3ff, 0x10ffff);
    let block = "";
    for (let code = start; code <= end; code += 1) {
      if (code < 0xd800 || code > 0xdfff) block += String.fromCodePoint(code);
    }
    // A block without a case mapping cannot hold a non-identity fold.
    if (block.toUpperCase() === block && block.toLowerCase() === block) continue;
    for (let code = start; code <= end; code += 1) {
      if (code >= 0xd800 && code <= 0xdfff) continue;
      const character = String.fromCodePoint(code);
      const folded = foldCharacter(character);
      if (folded === character) continue;
      const bucket = preimages.get(folded);
      if (bucket === undefined) preimages.set(folded, [character]);
      else bucket.push(character);
    }
  }
  foldPreimages = preimages;
  return preimages;
}

/** Longest fold one code point expands to; derived rather than assumed. */
let foldConsumptionLimit = 0;

function maximumFoldConsumption(): number {
  if (foldConsumptionLimit === 0) {
    let limit = 1;
    for (const folded of foldPreimagesByFold().keys()) {
      limit = Math.max(limit, [...folded].length);
    }
    foldConsumptionLimit = limit;
  }
  return foldConsumptionLimit;
}

/**
 * A class member covers the compared text when the two share a fold value, so
 * `[Σ]` covers ς, `[ß]` covers SS, and `[ß-…]` covers both through its own
 * bounds. Fold classes are compared instead of walking interval members, so a
 * wide range costs the same as a narrow one.
 */
function classMemberMatches(body: string, candidate: string): boolean {
  const members = characterClass(body);
  const folded = caseFold(candidate);
  const preimages = foldPreimagesByFold().get(folded) ?? [];
  if (preimages.some((character) => classContains(members, character))) return true;
  // A single code point whose fold is itself is its own preimage.
  const own = singleCodePoint(folded);
  return own !== null && classContains(members, own);
}

/**
 * A class consumes one code point by default, and several when the compared text
 * folds to the same value as a member (ß/SS, ﬃ/FFI).
 */
function classMatchLengths(body: string, symbols: readonly string[], start: number): number[] {
  const lengths: number[] = [];
  for (let length = 1; length <= maximumFoldConsumption() && start + length <= symbols.length; length += 1) {
    if (classMemberMatches(body, symbols.slice(start, start + length).join(""))) {
      lengths.push(length);
    }
  }
  return lengths;
}

/** A folded span can stand for one code point when it is a one-code-point fold or a mapped character. */
function singleCharacterMatchLengths(symbols: readonly string[], start: number): number[] {
  const lengths: number[] = [];
  const preimages = foldPreimagesByFold();
  for (let length = 1; length <= maximumFoldConsumption() && start + length <= symbols.length; length += 1) {
    const candidate = symbols.slice(start, start + length).join("");
    if ((length === 1 && caseFold(candidate) === candidate) || preimages.has(candidate)) {
      lengths.push(length);
    }
  }
  return lengths;
}

/**
 * Match against the compared path's folded code points. One original code point
 * may fold to several symbols (ß/SS), so token boundaries intentionally operate
 * on the folded stream rather than assuming one symbol per host character.
 */
function fnmatchcase(value: string, pattern: string): boolean {
  const symbols = [...caseFold(value)];
  const tokens = globTokens(pattern);
  const memo = new Map<string, boolean>();
  const match = (valueIndex: number, tokenIndex: number): boolean => {
    const key = `${valueIndex}:${tokenIndex}`;
    const cached = memo.get(key);
    if (cached !== undefined) return cached;
    let result: boolean;
    if (tokenIndex === tokens.length) {
      result = valueIndex === symbols.length;
    } else {
      const token = tokens[tokenIndex];
      if (token.kind === "star") {
        result = match(valueIndex, tokenIndex + 1) ||
          (valueIndex < symbols.length && match(valueIndex + 1, tokenIndex));
      } else if (valueIndex === symbols.length) {
        result = false;
      } else if (token.kind === "question") {
        result = singleCharacterMatchLengths(symbols, valueIndex)
          .some((length) => match(valueIndex + length, tokenIndex + 1));
      } else if (token.kind === "class") {
        const lengths = classMatchLengths(token.body, symbols, valueIndex);
        result = token.negated
          ? singleCharacterMatchLengths(symbols, valueIndex).some((length) =>
            !lengths.includes(length) && match(valueIndex + length, tokenIndex + 1))
          : lengths.some((length) => match(valueIndex + length, tokenIndex + 1));
      } else {
        const literal = [...token.folded];
        const end = valueIndex + literal.length;
        result = end <= symbols.length &&
          literal.every((symbol, index) => symbols[valueIndex + index] === symbol) &&
          match(end, tokenIndex + 1);
      }
    }
    memo.set(key, result);
    return result;
  };
  return match(0, 0);
}

function scopeLiteralPrefix(scope: string): string {
  const indexes = ["*", "?", "["]
    .map((token) => scope.indexOf(token))
    .filter((index) => index >= 0);
  return indexes.length > 0 ? scope.slice(0, Math.min(...indexes)) : scope;
}

function scopePairOverlaps(left: string, right: string): boolean {
  const equal = (first: string, second: string) => first === second ||
    caseFold(first) === caseFold(second);
  if (equal(left, right)) return true;
  if (["*", "**", "./"].includes(left) || ["*", "**", "./"].includes(right)) {
    return true;
  }
  const leftGlob = ["*", "?", "["].some((token) => left.includes(token));
  const rightGlob = ["*", "?", "["].some((token) => right.includes(token));
  if (leftGlob && !rightGlob) {
    const prefix = scopeLiteralPrefix(left);
    return fnmatchcase(right, left) ||
      (prefix.endsWith("/") && equal(right.replace(/\/$/u, ""), prefix.replace(/\/$/u, "")));
  }
  if (rightGlob && !leftGlob) {
    const prefix = scopeLiteralPrefix(right);
    return fnmatchcase(left, right) ||
      (prefix.endsWith("/") && equal(left.replace(/\/$/u, ""), prefix.replace(/\/$/u, "")));
  }
  if (leftGlob && rightGlob) {
    const leftPrefix = caseFold(scopeLiteralPrefix(left));
    const rightPrefix = caseFold(scopeLiteralPrefix(right));
    return !leftPrefix || !rightPrefix || leftPrefix.startsWith(rightPrefix) ||
      rightPrefix.startsWith(leftPrefix);
  }
  const leftRoot = caseFold(left.replace(/\/$/u, ""));
  const rightRoot = caseFold(right.replace(/\/$/u, ""));
  return (left.endsWith("/") && caseFold(right).startsWith(`${leftRoot}/`)) ||
    (right.endsWith("/") && caseFold(left).startsWith(`${rightRoot}/`));
}

function writeScopesOverlap(left: readonly string[], right: readonly string[]): boolean {
  if (left.length === 0 || right.length === 0) return false;
  const a = comparisonWriteScopes(left), b = comparisonWriteScopes(right);
  return a.some((first) => b.some((second) => scopePairOverlaps(first, second)));
}

function decisionStringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new EffectRuntimeRequestError(`${label} must be an array of strings`);
  }
  return [...value] as string[];
}

export function evaluateTaskLeaseWriteScopesOverlap(value: unknown): JsonObject {
  const input = requireJsonObject(value, "task lease write-scope overlap");
  return {
    overlap: writeScopesOverlap(
      decisionStringArray(input.left, "left"),
      decisionStringArray(input.right, "right"),
    ),
  };
}

/** Literal segments before the first wildcard, folded for comparison. */
function schedulingScopeRoot(scope: string): string {
  const parts: string[] = [];
  for (const part of scope.replace(/^\/+|\/+$/gu, "").split("/")) {
    if (part.includes("*") || part.includes("?") || part.includes("[")) break;
    parts.push(part);
  }
  return caseFold(parts.join("/"));
}

function schedulingScopePairOverlaps(left: string, right: string): boolean {
  if (scopePairOverlaps(left, right)) return true;
  const leftRoot = schedulingScopeRoot(left);
  const rightRoot = schedulingScopeRoot(right);
  return Boolean(leftRoot && rightRoot && (
    leftRoot === rightRoot ||
    leftRoot.startsWith(`${rightRoot}/`) ||
    rightRoot.startsWith(`${leftRoot}/`)
  ));
}

/**
 * Quota scheduling keeps its own subtree rule: a literal scope owns the paths
 * below it even without a trailing slash. That rule is wider than the canonical
 * lease rule, so this entry point composes the two instead of restating the
 * case fold or the glob matcher.
 */
export function evaluateQuotaSchedulingWriteScopesOverlap(value: unknown): JsonObject {
  const input = requireJsonObject(value, "quota scheduling write-scope overlap");
  const left = comparisonWriteScopes(decisionStringArray(input.left, "left"));
  const right = comparisonWriteScopes(decisionStringArray(input.right, "right"));
  const overlap = left.length > 0 && right.length > 0 &&
    left.some((first) => right.some((second) => schedulingScopePairOverlaps(first, second)));
  return {overlap};
}

function decisionBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new EffectRuntimeRequestError(`${label} must be a boolean`);
  }
  return value;
}

function decisionInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new EffectRuntimeRequestError(`${label} must be a non-negative safe integer`);
  }
  return value;
}

function decisionNullableString(value: unknown, label: string): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string or null`);
  }
  return value;
}

function decodeDecisionTodo(value: unknown): TodoFact | null {
  if (value === null || value === undefined) return null;
  const todo = requireJsonObject(value, "task lease acquire decision todo");
  return {
    todo_id: stringValue(todo.todo_id, "todo.todo_id"),
    status: stringValue(todo.status, "todo.status"),
    task_class: decisionNullableString(todo.task_class, "todo.task_class"),
    claimed_by: decisionNullableString(todo.claimed_by, "todo.claimed_by"),
    excluded_agents: decisionStringArray(todo.excluded_agents, "todo.excluded_agents"),
    task_repository: normalizeTodoRepository(todo.task_repository),
  };
}

function decodeDecisionLease(value: unknown): AcquireDecisionLease | null {
  if (value === null || value === undefined) return null;
  const lease = requireJsonObject(value, "task lease acquire decision lease");
  // Accept old callers without trusting their derived eligibility hint.
  if (lease.effective !== undefined) decisionBoolean(lease.effective, "lease.effective");
  return {
    present: decisionBoolean(lease.present, "lease.present"),
    active: decisionBoolean(lease.active, "lease.active"),
    status: decisionNullableString(lease.status, "lease.status"),
    owner: decisionNullableString(lease.owner, "lease.owner"),
    idempotency_key: decisionNullableString(
      lease.idempotency_key,
      "lease.idempotency_key",
    ),
    version: decisionInteger(lease.version, "lease.version"),
    lease_epoch: decisionInteger(lease.lease_epoch, "lease.lease_epoch"),
    write_scopes: comparisonWriteScopes(
      decisionStringArray(lease.write_scopes, "lease.write_scopes"),
    ),
    write_repository: leaseWriteRepository(lease.write_repository),
    write_workspace: leaseWorkspace(lease.write_workspace),
    acquire_ttl_seconds: optionalInteger(
      lease.acquire_ttl_seconds,
      "lease.acquire_ttl_seconds",
    ),
  };
}

function decodeAcquireDecisionInput(value: unknown): AcquireDecisionInput {
  const input = requireJsonObject(value, "task lease acquire decision");
  const command = requireJsonObject(input.command, "task lease acquire decision command");
  const rawOtherLeases = input.other_leases;
  if (!Array.isArray(rawOtherLeases)) {
    throw new EffectRuntimeRequestError("other_leases must be an array");
  }
  const otherLeases = rawOtherLeases.map((raw, index) => {
    const lease = requireJsonObject(raw, `other_leases[${index}]`);
    return {
      todo_id: stringValue(lease.todo_id, `other_leases[${index}].todo_id`),
      active: decisionBoolean(lease.active, `other_leases[${index}].active`),
      effective: decisionBoolean(lease.effective, `other_leases[${index}].effective`),
      write_repository: leaseWriteRepository(lease.write_repository),
      write_workspace: leaseWorkspace(lease.write_workspace),
      write_scopes: comparisonWriteScopes(decisionStringArray(
        lease.write_scopes,
        `other_leases[${index}].write_scopes`,
      )),
    };
  });
  return {
    handoff_mode: requireStringLiteral(input.handoff_mode, HANDOFF_MODES, "handoff_mode"),
    registered_agents: decisionStringArray(
      input.registered_agents,
      "registered_agents",
    ),
    todo: decodeDecisionTodo(input.todo),
    lease: decodeDecisionLease(input.lease),
    other_leases: otherLeases,
    command: {
      owner: stringValue(command.owner, "command.owner"),
      idempotency_key: stringValue(
        command.idempotency_key,
        "command.idempotency_key",
      ),
      ttl_seconds: decisionInteger(command.ttl_seconds, "command.ttl_seconds"),
      write_scopes: comparisonWriteScopes(decisionStringArray(
        command.write_scopes,
        "command.write_scopes",
      )),
      write_workspace: leaseWorkspace(command.write_workspace),
      expected_version: optionalInteger(
        command.expected_version,
        "command.expected_version",
      ),
    },
  };
}

function acquireDecisionResult(
  outcome: AcquireDecision["outcome"],
  code: string,
  options: {
    idempotent?: boolean;
    nextLease?: JsonObject | null;
    conflictIndexes?: number[];
    advisoryIndexes?: number[];
  } = {},
): AcquireDecision {
  return {
    outcome,
    code,
    idempotent: options.idempotent ?? false,
    next_lease: options.nextLease ?? null,
    conflict_indexes: options.conflictIndexes ?? [],
    ...(options.advisoryIndexes?.length ? {overlap_advisory_indexes: options.advisoryIndexes} : {}),
  };
}

/**
 * Canonical pure decision for both local file acquire and shared coordination.
 * Locking, source revalidation, persistence, provider CAS, and receipts stay in
 * their respective execution layers.
 */
export function evaluateTaskLeaseAcquireDecision(value: unknown): AcquireDecision {
  return decideTaskLeaseAcquire(decodeAcquireDecisionInput(value));
}

export function decideTaskLeaseAcquire(input: AcquireDecisionInput): AcquireDecision {
  const { command, lease } = input;
  if (lease !== null && lease.active && (!lease.present || lease.status === "released")) {
    return acquireDecisionResult("rejected", "invalid_lease_snapshot");
  }
  if (input.handoff_mode === "soft_claim") {
    return acquireDecisionResult("rejected", "handoff_mode_forbids_lease");
  }
  const rejection = ownerRejection(
    input.todo ?? undefined,
    command.owner || null,
    input.registered_agents,
  );
  if (rejection !== null) {
    return acquireDecisionResult("rejected", rejection);
  }
  const retired = retiredHandoffModeRejection(input.handoff_mode);
  if (retired !== null) return acquireDecisionResult("rejected", retired);
  const actualVersion = lease !== null && lease.present ? lease.version : 0;
  if (
    command.expected_version !== null && command.expected_version !== actualVersion
  ) {
    return acquireDecisionResult("conflict", "version_mismatch");
  }
  // The old wire effective hint is not authority over the supplied owner facts.
  if (lease !== null && lease.present && lease.active &&
      ownerRejection(input.todo, lease.owner, input.registered_agents) === null) {
    const repositoryRejection = leaseRepositoryRejection(input.todo, lease);
    if (repositoryRejection !== null) return acquireDecisionResult("rejected", repositoryRejection);
    if (
      lease.owner === command.owner &&
      lease.idempotency_key === command.idempotency_key
    ) {
      const scopesMatch = equalScopeSets(lease.write_scopes, command.write_scopes);
      const ttlMatches = lease.acquire_ttl_seconds === null ||
        lease.acquire_ttl_seconds === command.ttl_seconds;
      if (!scopesMatch || !ttlMatches || !sameLeaseWorkspace(lease.write_workspace, command.write_workspace)) {
        return acquireDecisionResult("rejected", "idempotency_key_reuse");
      }
      return acquireDecisionResult("no_change", "lease_acquire_replay", {
        idempotent: true,
      });
    }
    return acquireDecisionResult("conflict", "todo_lease_conflict");
  }
  if (
    lease !== null && lease.present &&
    lease.idempotency_key === command.idempotency_key
  ) {
    return acquireDecisionResult("rejected", "idempotency_key_reuse");
  }
  const repository = normalizeTodoRepository(input.todo?.task_repository);
  const workspace = leaseWorkspace(command.write_workspace);
  if (workspace && workspace.repository.toLowerCase() !== repository?.toLowerCase()) {
    return acquireDecisionResult("rejected", "lease_workspace_repository_mismatch");
  }
  const advisoryIndexes: number[] = [], conflictIndexes: number[] = [];
  for (const [index, other] of input.other_leases.entries()) {
    if (!other.active || !other.effective) continue;
    const sameCheckout = workspace !== null && sameLeaseCheckout(workspace, other.write_workspace);
    if ((!sameCheckout && !repositoryScopesMayOverlap(repository, other.write_repository)) ||
        !writeScopesOverlap(command.write_scopes, other.write_scopes)) continue;
    // Verified code-edit mode coordinates integration, rather than requesting
    // cross-checkout file exclusivity. Unknown legacy workspaces stay unknown;
    // neither their records nor their execution ownership are changed.
    if (workspace && !sameCheckout) advisoryIndexes.push(index);
    else conflictIndexes.push(index);
  }
  if (conflictIndexes.length > 0) {
    return acquireDecisionResult("conflict", "write_scope_conflict", {
      conflictIndexes,
    });
  }
  if (actualVersion >= Number.MAX_SAFE_INTEGER || (lease?.lease_epoch ?? 0) >= Number.MAX_SAFE_INTEGER) {
    return acquireDecisionResult("rejected", "lease_generation_exhausted");
  }
  return acquireDecisionResult("apply", "lease_acquire", {
    advisoryIndexes,
    nextLease: {
      present: true,
      active: true,
      status: "active",
      owner: command.owner,
      idempotency_key: command.idempotency_key,
      version: actualVersion + 1,
      lease_epoch: (lease?.lease_epoch ?? 0) + 1,
      write_scopes: comparisonWriteScopes(command.write_scopes),
      ...(workspace ? {write_workspace: workspace} : {}),
      ...(repository === null ? {} : {write_repository: repository}),
      acquire_ttl_seconds: command.ttl_seconds,
    },
  });
}

function equalScopeSets(left: readonly string[], right: readonly string[]): boolean {
  const a = comparisonWriteScopes(left).sort((first, second) => first.localeCompare(second));
  const b = comparisonWriteScopes(right).sort((first, second) => first.localeCompare(second));
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

/** Materialize the one admitted generation for standalone and atomic Todo claim. */
export function materializeTaskLeaseAcquire(identity: {goal_id: string; todo_id: string},
  command: AcquireDecisionInput["command"], decision: AcquireDecision, now: Date): LeaseRecord {
  if (decision.outcome !== "apply" || decision.next_lease === null) {
    throw new EffectRuntimeRequestError("lease materialization requires an apply decision");
  }
  const at = now.toISOString().replace(/\.\d{3}Z$/u, "Z");
  return {schema_version: "task_lease_v0", goal_id: identity.goal_id, todo_id: identity.todo_id,
    owner: command.owner, idempotency_key: command.idempotency_key,
    ...(decision.next_lease.write_workspace ? {write_workspace: leaseWorkspace(decision.next_lease.write_workspace)} : {}),
    write_scopes: comparisonWriteScopes(command.write_scopes), acquire_ttl_seconds: command.ttl_seconds,
    ...(decision.next_lease.write_repository == null ? {} :
      {write_repository: leaseWriteRepository(decision.next_lease.write_repository)}),
    version: decisionInteger(decision.next_lease.version, "next_lease.version"),
    lease_epoch: decisionInteger(decision.next_lease.lease_epoch, "next_lease.lease_epoch"),
    acquired_at: at, updated_at: at,
    expires_at: new Date(now.valueOf() + command.ttl_seconds * 1000).toISOString().replace(/\.\d{3}Z$/u, "Z"),
    status: "active"};
}
