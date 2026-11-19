/** Deterministic wave-based material reranking preview for Material Lifecycle.
 *
 * Pure, browser-safe TypeScript decision owner with zero host or storage IO.
 * Produces bounded rerank deltas and constraints; never authorizes source writes.
 */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, requireStringLiteral} from "../runtime_decode.ts";

export const MATERIAL_WAVE_RERANK_PLAN_SCHEMA_VERSION =
  "material_wave_rerank_plan_v0";

export const MATERIAL_HORIZON_WAVES = [
  "imminent_gate",
  "active_sprint",
  "foundational_track",
  "orthogonal_backlog",
] as const;
export type MaterialHorizonWave = (typeof MATERIAL_HORIZON_WAVES)[number];

export const MATERIAL_EFFECTIVE_WAVES = [
  "imminent_gate",
  "active_sprint",
  "foundational_track",
  "unannotated_retention",
  "orthogonal_backlog",
] as const;
export type MaterialEffectiveWave = (typeof MATERIAL_EFFECTIVE_WAVES)[number];

export const MATERIAL_EFFORT_BANDS = [
  "quick_slice",
  "focused_study",
  "deep_monograph",
] as const;
export type MaterialEffortBand = (typeof MATERIAL_EFFORT_BANDS)[number];

export const MATERIAL_EVIDENCE_ROLES = [
  "load_bearing_primary",
  "method_comparator",
  "foundational_reference",
  "secondary_or_commentary",
  "orthogonal_signal",
] as const;
export type MaterialEvidenceRole = (typeof MATERIAL_EVIDENCE_ROLES)[number];

const COMPACT_TOKEN_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const MAX_QUEUE_REFS = 10_000;
const MAX_PROTECTED_REFS = 100;
const MAX_PRIMARY_SOURCE_REFS = 20;
const MAX_EVIDENCE_REFS = 10;

const UNSAFE_FIELDS = new Set([
  "api_key",
  "content",
  "credential",
  "credentials",
  "private_locator",
  "provider_payload",
  "raw_chat",
  "raw_content",
  "raw_provider_payload",
  "token",
  "tool_output",
]);

const REQUEST_ALLOWED_FIELDS = new Set([
  "annotations",
  "default_evidence_refs",
  "max_orthogonal_in_top_window",
  "ordered_material_refs",
  "protected_material_refs",
  "required_load_bearing_refs",
  "top_window_size",
]);

const ANNOTATION_ALLOWED_FIELDS = new Set([
  "blocks_active_deliverable",
  "decision_gain_score",
  "effort_band",
  "evidence_refs",
  "evidence_role",
  "horizon_wave",
  "material_ref",
  "primary_source_refs",
  "reason_code",
  "scoped_slice_ready",
]);

const ACTIVE_WAVE_ORDER: Readonly<Record<string, number>> = {
  imminent_gate: 0,
  active_sprint: 1,
  foundational_track: 2,
};

const EVIDENCE_ROLE_PRIORITY: Readonly<Record<MaterialEvidenceRole, number>> = {
  load_bearing_primary: 0,
  method_comparator: 1,
  foundational_reference: 2,
  secondary_or_commentary: 3,
  orthogonal_signal: 4,
};

const EFFORT_BAND_ORDER: Readonly<Record<MaterialEffortBand, number>> = {
  quick_slice: 0,
  focused_study: 1,
  deep_monograph: 2,
};

// Exact integer multipliers for LCM(1, 2, 6) = 6 so cost_adjusted_voi comparisons
// never suffer IEEE-754 1/6 rounding drift.
const EFFORT_SCALED_MULTIPLIER: Readonly<Record<MaterialEffortBand, number>> = {
  quick_slice: 6,
  focused_study: 3,
  deep_monograph: 1,
};

interface NormalizedItem {
  readonly materialRef: string;
  readonly originalRank: number;
  readonly annotated: boolean;
  readonly declaredWave: MaterialHorizonWave | null;
  readonly effectiveWave: MaterialEffectiveWave;
  readonly effortBand: MaterialEffortBand | null;
  readonly scopedSliceReady: boolean;
  readonly decisionGainScore: number;
  readonly scaledVoi: number;
  readonly evidenceRole: MaterialEvidenceRole | null;
  readonly blocksActiveDeliverable: boolean;
  readonly primarySourceRefs: readonly string[];
  readonly explicitReasonCode: string | null;
  readonly evidenceRefs: readonly string[] | null;
  readonly monographStratified: boolean;
  readonly isOrthogonal: boolean;
}

function fail(message: string): never {
  throw new EffectRuntimeRequestError(message);
}

function checkAllowedFields(
  record: JsonObject,
  field: string,
  allowed: ReadonlySet<string>,
  required: readonly string[] = [],
): void {
  const keys = Object.keys(record);
  const unsafe = keys.filter((key) => UNSAFE_FIELDS.has(key)).sort();
  if (unsafe.length > 0) {
    fail(`${field} contains unsafe fields: ${unsafe.join(", ")}`);
  }
  const unexpected = keys.filter((key) => !allowed.has(key)).sort();
  if (unexpected.length > 0) {
    fail(`${field} contains unsupported fields: ${unexpected.join(", ")}`);
  }
  const missing = required.filter((key) => record[key] === undefined || record[key] === null).sort();
  if (missing.length > 0) {
    fail(`${field} is missing required fields: ${missing.join(", ")}`);
  }
}

function compactToken(value: unknown, field: string): string {
  if (typeof value !== "string") {
    fail(`${field} must be a compact token string`);
  }
  const normalized = value.trim().replace(/\s+/g, " ");
  if (!normalized) {
    fail(`${field} must be non-empty`);
  }
  if (normalized.length > 128) {
    fail(`${field} must be at most 128 characters`);
  }
  if (normalized.includes("://") || normalized.startsWith("/")) {
    fail(`${field} must use an opaque compact token, not a path or URL`);
  }
  if (!COMPACT_TOKEN_RE.test(normalized)) {
    fail(`${field} must contain only letters, digits, dot, colon, dash, or underscore`);
  }
  return normalized;
}

function positiveInt(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) {
    fail(`${field} must be a positive integer`);
  }
  return value;
}

function nonNegativeInt(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    fail(`${field} must be a non-negative integer`);
  }
  return value;
}

function optionalBoolean(value: unknown, field: string, fallback: boolean): boolean {
  if (value === undefined || value === null) return fallback;
  if (typeof value !== "boolean") {
    fail(`${field} must be a boolean`);
  }
  return value;
}

function optionalGainScore(value: unknown, field: string): number {
  if (value === undefined || value === null) return 0;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 100) {
    fail(`${field} must be a finite number between 0 and 100`);
  }
  return value;
}

function uniqueOrderedTokens(
  value: unknown,
  field: string,
  maxItems: number,
  requireNonEmpty = false,
): string[] {
  if (!Array.isArray(value)) {
    fail(`${field} must be an array of compact tokens`);
  }
  if (requireNonEmpty && value.length === 0) {
    fail(`${field} must contain at least one item`);
  }
  if (value.length > maxItems) {
    fail(`${field} must contain at most ${maxItems} items`);
  }
  const result: string[] = [];
  const seen = new Set<string>();
  for (let index = 0; index < value.length; index++) {
    const token = compactToken(value[index], `${field}[${index}]`);
    if (seen.has(token)) {
      fail(`${field} values must be unique`);
    }
    seen.add(token);
    result.push(token);
  }
  return result;
}

function deduplicatedTokenList(
  value: unknown,
  field: string,
  maxItems: number,
  sortOutput: boolean,
): string[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) {
    fail(`${field} must be an array of compact tokens`);
  }
  if (value.length > maxItems) {
    fail(`${field} must contain at most ${maxItems} items`);
  }
  const result: string[] = [];
  const seen = new Set<string>();
  for (let index = 0; index < value.length; index++) {
    const token = compactToken(value[index], `${field}[${index}]`);
    if (!seen.has(token)) {
      seen.add(token);
      result.push(token);
    }
  }
  return sortOutput ? [...result].sort() : result;
}

function compareWithinWave(left: NormalizedItem, right: NormalizedItem): number {
  if (left.blocksActiveDeliverable !== right.blocksActiveDeliverable) {
    return left.blocksActiveDeliverable ? -1 : 1;
  }
  const leftRole = left.evidenceRole === null ? 2 : EVIDENCE_ROLE_PRIORITY[left.evidenceRole];
  const rightRole = right.evidenceRole === null ? 2 : EVIDENCE_ROLE_PRIORITY[right.evidenceRole];
  if (leftRole !== rightRole) {
    return leftRole - rightRole;
  }
  if (left.scaledVoi !== right.scaledVoi) {
    return right.scaledVoi - left.scaledVoi;
  }
  const leftEffort = left.effortBand === null ? 1 : EFFORT_BAND_ORDER[left.effortBand];
  const rightEffort = right.effortBand === null ? 1 : EFFORT_BAND_ORDER[right.effortBand];
  if (leftEffort !== rightEffort) {
    return leftEffort - rightEffort;
  }
  return left.originalRank - right.originalRank;
}

function findCyclePath(
  nodes: readonly string[],
  succs: ReadonlyMap<string, ReadonlySet<string>>,
): string[] {
  const state = new Map<string, 0 | 1 | 2>();
  const stack: string[] = [];
  const dfs = (node: string): string[] | null => {
    state.set(node, 1);
    stack.push(node);
    for (const next of succs.get(node) ?? []) {
      const nextState = state.get(next) ?? 0;
      if (nextState === 1) {
        const cycleStart = stack.indexOf(next);
        return [...stack.slice(cycleStart), next];
      }
      if (nextState === 0) {
        const found = dfs(next);
        if (found) return found;
      }
    }
    stack.pop();
    state.set(node, 2);
    return null;
  };
  for (const node of nodes) {
    if ((state.get(node) ?? 0) === 0) {
      const found = dfs(node);
      if (found) return found;
    }
  }
  return [];
}

function topologicalOrderOrFail(
  nodes: readonly string[],
  preds: ReadonlyMap<string, ReadonlySet<string>>,
  succs: ReadonlyMap<string, ReadonlySet<string>>,
): string[] {
  const inDegree = new Map<string, number>();
  const queue: string[] = [];
  for (const node of nodes) {
    const degree = preds.get(node)?.size ?? 0;
    inDegree.set(node, degree);
    if (degree === 0) queue.push(node);
  }
  const topo: string[] = [];
  let head = 0;
  while (head < queue.length) {
    const current = queue[head++]!;
    topo.push(current);
    for (const next of succs.get(current) ?? []) {
      const updated = (inDegree.get(next) ?? 0) - 1;
      inDegree.set(next, updated);
      if (updated === 0) queue.push(next);
    }
  }
  if (topo.length !== nodes.length) {
    const cycle = findCyclePath(nodes, succs);
    fail(`primary_source_refs cycle detected: ${cycle.join(" -> ")}`);
  }
  return topo;
}

export function planMaterialWaveRerank(rawRequest: JsonObject): JsonObject {
  const request = requireJsonObject(rawRequest, "material wave rerank request");
  checkAllowedFields(request, "request", REQUEST_ALLOWED_FIELDS, ["ordered_material_refs"]);

  const orderedRefs = uniqueOrderedTokens(
    request.ordered_material_refs,
    "ordered_material_refs",
    MAX_QUEUE_REFS,
    true,
  );
  const totalCount = orderedRefs.length;
  const originalRankByRef = new Map<string, number>();
  orderedRefs.forEach((ref, index) => {
    originalRankByRef.set(ref, index + 1);
  });

  const topWindowSize =
    request.top_window_size === undefined || request.top_window_size === null
      ? 30
      : positiveInt(request.top_window_size, "top_window_size");
  const maxOrthogonalInTopWindow =
    request.max_orthogonal_in_top_window === undefined ||
    request.max_orthogonal_in_top_window === null
      ? 0
      : nonNegativeInt(request.max_orthogonal_in_top_window, "max_orthogonal_in_top_window");

  const protectedRefs = deduplicatedTokenList(
    request.protected_material_refs,
    "protected_material_refs",
    MAX_PROTECTED_REFS,
    true,
  );
  const protectedSet = new Set(protectedRefs);
  for (const ref of protectedRefs) {
    if (!originalRankByRef.has(ref)) {
      fail("protected_material_refs must belong to the ranked queue");
    }
  }

  const requiredLoadBearingRefs = deduplicatedTokenList(
    request.required_load_bearing_refs,
    "required_load_bearing_refs",
    MAX_QUEUE_REFS,
    false,
  );
  const missingLoadBearing = requiredLoadBearingRefs.filter(
    (ref) => !originalRankByRef.has(ref),
  );
  if (missingLoadBearing.length > 0) {
    fail(
      `missing or unranked required load-bearing primary materials (settle candidate intake first): ${missingLoadBearing.join(", ")}`,
    );
  }

  const defaultEvidenceRefs = deduplicatedTokenList(
    request.default_evidence_refs,
    "default_evidence_refs",
    MAX_EVIDENCE_REFS,
    true,
  );

  const rawAnnotations =
    request.annotations === undefined || request.annotations === null
      ? []
      : request.annotations;
  if (!Array.isArray(rawAnnotations)) {
    fail("annotations must be an array of objects");
  }
  if (rawAnnotations.length > totalCount) {
    fail("annotations cannot exceed ordered_material_refs length");
  }

  const annotationByRef = new Map<string, NormalizedItem>();
  for (let index = 0; index < rawAnnotations.length; index++) {
    const field = `annotations[${index}]`;
    const record = requireJsonObject(rawAnnotations[index], field);
    checkAllowedFields(record, field, ANNOTATION_ALLOWED_FIELDS, ["material_ref"]);
    const materialRef = compactToken(record.material_ref, `${field}.material_ref`);
    const originalRank = originalRankByRef.get(materialRef);
    if (originalRank === undefined) {
      fail(
        `ranked material identity required; use intake settlement for unranked entry: ${materialRef}`,
      );
    }
    if (annotationByRef.has(materialRef)) {
      fail(`annotations material_ref values must be unique: ${materialRef}`);
    }

    const blocksActiveDeliverable = optionalBoolean(
      record.blocks_active_deliverable,
      `${field}.blocks_active_deliverable`,
      false,
    );
    const rawRole =
      record.evidence_role === undefined || record.evidence_role === null
        ? null
        : requireStringLiteral(
            record.evidence_role,
            MATERIAL_EVIDENCE_ROLES,
            `${field}.evidence_role`,
            `unsupported evidence_role: ${String(record.evidence_role)}`,
          );
    const declaredWave: MaterialHorizonWave =
      record.horizon_wave === undefined || record.horizon_wave === null
        ? rawRole === "orthogonal_signal"
          ? "orthogonal_backlog"
          : blocksActiveDeliverable
            ? "imminent_gate"
            : "active_sprint"
        : requireStringLiteral(
            record.horizon_wave,
            MATERIAL_HORIZON_WAVES,
            `${field}.horizon_wave`,
            `unsupported horizon_wave: ${String(record.horizon_wave)}`,
          );
    const evidenceRole: MaterialEvidenceRole =
      rawRole ??
      (declaredWave === "orthogonal_backlog"
        ? "orthogonal_signal"
        : "foundational_reference");
    const effortBand: MaterialEffortBand =
      record.effort_band === undefined || record.effort_band === null
        ? "focused_study"
        : requireStringLiteral(
            record.effort_band,
            MATERIAL_EFFORT_BANDS,
            `${field}.effort_band`,
            `unsupported effort_band: ${String(record.effort_band)}`,
          );
    const scopedSliceReady = optionalBoolean(
      record.scoped_slice_ready,
      `${field}.scoped_slice_ready`,
      false,
    );
    const decisionGainScore = optionalGainScore(
      record.decision_gain_score,
      `${field}.decision_gain_score`,
    );
    const primarySourceRefs = deduplicatedTokenList(
      record.primary_source_refs,
      `${field}.primary_source_refs`,
      MAX_PRIMARY_SOURCE_REFS,
      false,
    );
    if (primarySourceRefs.includes(materialRef)) {
      fail(`primary_source_refs cycle detected: ${materialRef} -> ${materialRef}`);
    }
    const explicitReasonCode =
      record.reason_code === undefined || record.reason_code === null
        ? null
        : compactToken(record.reason_code, `${field}.reason_code`);
    const evidenceRefs =
      record.evidence_refs === undefined || record.evidence_refs === null
        ? null
        : deduplicatedTokenList(
            record.evidence_refs,
            `${field}.evidence_refs`,
            MAX_EVIDENCE_REFS,
            true,
          );

    const monographStratified =
      effortBand === "deep_monograph" &&
      !scopedSliceReady &&
      (declaredWave === "imminent_gate" || declaredWave === "active_sprint");
    const effectiveWave: MaterialEffectiveWave = monographStratified
      ? "foundational_track"
      : declaredWave;
    const isOrthogonal =
      effectiveWave === "orthogonal_backlog" || evidenceRole === "orthogonal_signal";

    annotationByRef.set(materialRef, {
      materialRef,
      originalRank,
      annotated: true,
      declaredWave,
      effectiveWave,
      effortBand,
      scopedSliceReady,
      decisionGainScore,
      scaledVoi: decisionGainScore * EFFORT_SCALED_MULTIPLIER[effortBand],
      evidenceRole,
      blocksActiveDeliverable,
      primarySourceRefs,
      explicitReasonCode,
      evidenceRefs,
      monographStratified,
      isOrthogonal,
    });
  }

  const itemByRef = new Map<string, NormalizedItem>();
  for (const ref of orderedRefs) {
    const existing = annotationByRef.get(ref);
    if (existing) {
      itemByRef.set(ref, existing);
    } else {
      itemByRef.set(ref, {
        materialRef: ref,
        originalRank: originalRankByRef.get(ref)!,
        annotated: false,
        declaredWave: null,
        effectiveWave: "unannotated_retention",
        effortBand: null,
        scopedSliceReady: false,
        decisionGainScore: 0,
        scaledVoi: 0,
        evidenceRole: null,
        blocksActiveDeliverable: false,
        primarySourceRefs: [],
        explicitReasonCode: null,
        evidenceRefs: null,
        monographStratified: false,
        isOrthogonal: false,
      });
    }
  }

  // Build primary-over-secondary lineage DAG across ranked refs.
  const preds = new Map<string, Set<string>>();
  const succs = new Map<string, Set<string>>();
  for (const ref of orderedRefs) {
    preds.set(ref, new Set());
    succs.set(ref, new Set());
  }
  for (const item of itemByRef.values()) {
    for (const primaryRef of item.primarySourceRefs) {
      if (originalRankByRef.has(primaryRef)) {
        preds.get(item.materialRef)!.add(primaryRef);
        succs.get(primaryRef)!.add(item.materialRef);
      }
    }
  }

  const topoOrder = topologicalOrderOrFail(orderedRefs, preds, succs);

  // Map locked anchor ranks and open ranks.
  const lockedRankToRef = new Map<number, string>();
  for (const ref of protectedRefs) {
    lockedRankToRef.set(originalRankByRef.get(ref)!, ref);
  }
  const openRanks: number[] = [];
  for (let rank = 1; rank <= totalCount; rank++) {
    if (!lockedRankToRef.has(rank)) openRanks.push(rank);
  }

  const prevOpenRank = (limitRank: number): number => {
    let best = 0;
    for (const openRank of openRanks) {
      if (openRank <= limitRank) best = openRank;
      else break;
    }
    return best;
  };

  // Validate rank intervals implied by protected anchors and lineage edges.
  const minRank = new Map<string, number>();
  const maxRank = new Map<string, number>();
  for (const ref of orderedRefs) {
    const locked = originalRankByRef.get(ref)!;
    minRank.set(ref, protectedSet.has(ref) ? locked : 1);
    maxRank.set(ref, protectedSet.has(ref) ? locked : totalCount);
  }
  for (const ref of topoOrder) {
    const currentMin = minRank.get(ref)!;
    for (const next of succs.get(ref)!) {
      const candidate = currentMin + 1;
      if (candidate > minRank.get(next)!) {
        minRank.set(next, candidate);
      }
    }
  }
  for (let index = topoOrder.length - 1; index >= 0; index--) {
    const ref = topoOrder[index]!;
    let currentMax = maxRank.get(ref)!;
    for (const next of succs.get(ref)!) {
      const candidate = maxRank.get(next)! - 1;
      if (candidate < currentMax) {
        currentMax = candidate;
      }
    }
    if (!protectedSet.has(ref)) {
      currentMax = prevOpenRank(currentMax);
    }
    maxRank.set(ref, currentMax);
    if (currentMax < 1 || minRank.get(ref)! > currentMax) {
      fail(
        `protected anchor inversion prevents primary-over-secondary ordering for ${ref}`,
      );
    }
  }

  // Stage A: Partition movable items by effective wave and apply within-wave cost-adjusted VOI.
  const activeWaveItems: NormalizedItem[] = [];
  const unannotatedItems: NormalizedItem[] = [];
  const orthogonalWaveItems: NormalizedItem[] = [];
  for (const ref of orderedRefs) {
    if (protectedSet.has(ref)) continue;
    const item = itemByRef.get(ref)!;
    if (
      item.effectiveWave === "imminent_gate" ||
      item.effectiveWave === "active_sprint" ||
      item.effectiveWave === "foundational_track"
    ) {
      activeWaveItems.push(item);
    } else if (item.effectiveWave === "orthogonal_backlog") {
      orthogonalWaveItems.push(item);
    } else {
      unannotatedItems.push(item);
    }
  }

  activeWaveItems.sort((left, right) => {
    const waveDiff =
      ACTIVE_WAVE_ORDER[left.effectiveWave]! -
      ACTIVE_WAVE_ORDER[right.effectiveWave]!;
    return waveDiff !== 0 ? waveDiff : compareWithinWave(left, right);
  });
  orthogonalWaveItems.sort(compareWithinWave);
  unannotatedItems.sort((left, right) => left.originalRank - right.originalRank);

  const effectiveTopWindow = Math.min(topWindowSize, totalCount);
  let protectedInTop = 0;
  let protectedOrthInTop = 0;
  for (const [rank, ref] of lockedRankToRef.entries()) {
    if (rank <= effectiveTopWindow) {
      protectedInTop++;
      if (itemByRef.get(ref)!.isOrthogonal) {
        protectedOrthInTop++;
      }
    }
  }
  const openSlotsInTop = effectiveTopWindow - protectedInTop;
  const totalNonOrthogonalCount = orderedRefs.filter(
    (ref) => !itemByRef.get(ref)!.isOrthogonal,
  ).length;
  if (
    protectedOrthInTop > maxOrthogonalInTopWindow &&
    totalNonOrthogonalCount > effectiveTopWindow - protectedOrthInTop
  ) {
    fail(
      "protected orthogonal anchors in top_window_size exceed max_orthogonal_in_top_window",
    );
  }

  const allowedMovableOrthInTop = Math.max(
    0,
    maxOrthogonalInTopWindow - protectedOrthInTop,
  );
  const candidatesBeforeBacklog = [...activeWaveItems, ...unannotatedItems];
  let nonOrthRemaining = candidatesBeforeBacklog.filter(
    (item) => !item.isOrthogonal,
  ).length;

  const topWindowMovable: NormalizedItem[] = [];
  const deferredOrthogonal: NormalizedItem[] = [];
  const remainingActiveWaves: NormalizedItem[] = [];
  const remainingPassive: NormalizedItem[] = [];
  const deferredByCapSet = new Set<string>();
  let movableOrthUsed = 0;

  for (const item of candidatesBeforeBacklog) {
    if (!item.isOrthogonal) {
      nonOrthRemaining--;
    }
    if (topWindowMovable.length < openSlotsInTop) {
      const slotsStillNeededAfterSkip = openSlotsInTop - topWindowMovable.length;
      if (item.isOrthogonal) {
        if (
          movableOrthUsed < allowedMovableOrthInTop ||
          nonOrthRemaining < slotsStillNeededAfterSkip
        ) {
          topWindowMovable.push(item);
          movableOrthUsed++;
        } else {
          deferredOrthogonal.push(item);
          deferredByCapSet.add(item.materialRef);
        }
      } else {
        topWindowMovable.push(item);
      }
    } else if (
      item.effectiveWave === "imminent_gate" ||
      item.effectiveWave === "active_sprint" ||
      item.effectiveWave === "foundational_track"
    ) {
      remainingActiveWaves.push(item);
    } else {
      remainingPassive.push(item);
    }
  }

  const initialMovable = [
    ...topWindowMovable,
    ...remainingActiveWaves,
    ...deferredOrthogonal,
    ...orthogonalWaveItems,
    ...remainingPassive,
  ].map((item) => item.materialRef);

  const basePos = new Map<string, number>();
  initialMovable.forEach((ref, index) => {
    basePos.set(ref, index);
  });

  // Compute transitive movable predecessors so lineage pull-up works even across
  // an intermediate protected anchor.
  const movableAncestors = new Map<string, string[]>();
  for (const ref of topoOrder) {
    const ancestors = new Set<string>();
    for (const pred of preds.get(ref)!) {
      if (!protectedSet.has(pred)) {
        ancestors.add(pred);
      }
      for (const transitive of movableAncestors.get(pred) ?? []) {
        ancestors.add(transitive);
      }
    }
    movableAncestors.set(
      ref,
      [...ancestors].sort((a, b) => basePos.get(a)! - basePos.get(b)!),
    );
  }

  // Pull buried movable primary sources immediately ahead of dependent secondary items.
  const emitted = new Set<string>();
  const pulledList: string[] = [];
  const lineagePromotedSet = new Set<string>();
  const emitWithAncestors = (ref: string, pulledForDescendant: boolean): void => {
    if (emitted.has(ref) || protectedSet.has(ref)) return;
    for (const ancestor of movableAncestors.get(ref) ?? []) {
      if (!emitted.has(ancestor)) {
        emitWithAncestors(ancestor, true);
      }
    }
    if (pulledForDescendant) {
      lineagePromotedSet.add(ref);
    }
    emitted.add(ref);
    pulledList.push(ref);
  };
  for (const ref of initialMovable) {
    emitWithAncestors(ref, false);
  }

  const pulledPos = new Map<string, number>();
  pulledList.forEach((ref, index) => {
    pulledPos.set(ref, index);
  });

  // Schedule ranks 1..N honoring protected anchors, predecessor readiness, and deadlines.
  const unplacedPredCount = new Map<string, number>();
  for (const ref of orderedRefs) {
    unplacedPredCount.set(ref, preds.get(ref)!.size);
  }
  const unplacedMovable = new Set<string>(pulledList);
  let finalOrder: string[] = [];

  const countOpenSlotsInRange = (startRank: number, endRank: number): number => {
    let count = 0;
    for (const openRank of openRanks) {
      if (openRank >= startRank && openRank <= endRank) count++;
      else if (openRank > endRank) break;
    }
    return count;
  };

  for (let rank = 1; rank <= totalCount; rank++) {
    const lockedRef = lockedRankToRef.get(rank);
    if (lockedRef !== undefined) {
      if ((unplacedPredCount.get(lockedRef) ?? 0) !== 0) {
        const missingPreds = [...preds.get(lockedRef)!].filter(
          (pred) => !finalOrder.includes(pred),
        );
        fail(
          `protected anchor inversion: primary source ${missingPreds.join(", ")} cannot precede protected material ${lockedRef}`,
        );
      }
      finalOrder.push(lockedRef);
      for (const next of succs.get(lockedRef)!) {
        unplacedPredCount.set(next, unplacedPredCount.get(next)! - 1);
      }
      continue;
    }

    // Check protected-descendant deadlines among remaining movable items.
    let tightDeadline: number | null = null;
    const distinctDeadlines = new Set<number>();
    for (const candidate of unplacedMovable) {
      const deadline = maxRank.get(candidate)!;
      if (deadline < rank) {
        fail(
          `protected anchor inversion: primary source ${candidate} missed deadline before protected material`,
        );
      }
      if (deadline < totalCount) {
        distinctDeadlines.add(deadline);
      }
    }
    for (const deadline of [...distinctDeadlines].sort((a, b) => a - b)) {
      let dueCount = 0;
      for (const candidate of unplacedMovable) {
        if (maxRank.get(candidate)! <= deadline) dueCount++;
      }
      const availableOpen = countOpenSlotsInRange(rank, deadline);
      if (dueCount > availableOpen) {
        fail(
          `protected anchor inversion: insufficient open ranks before rank ${deadline + 1}`,
        );
      }
      if (dueCount === availableOpen && tightDeadline === null) {
        tightDeadline = deadline;
      }
    }

    let chosen: string | null = null;
    let bestPulledIndex = Number.POSITIVE_INFINITY;
    for (const candidate of unplacedMovable) {
      if (unplacedPredCount.get(candidate) !== 0) continue;
      if (tightDeadline !== null && maxRank.get(candidate)! > tightDeadline) {
        continue;
      }
      const candidatePos = pulledPos.get(candidate)!;
      if (candidatePos < bestPulledIndex) {
        bestPulledIndex = candidatePos;
        chosen = candidate;
      }
    }
    if (chosen === null) {
      fail(
        `protected anchor inversion prevents scheduling open rank ${rank}`,
      );
    }
    unplacedMovable.delete(chosen);
    finalOrder.push(chosen);
    for (const next of succs.get(chosen)!) {
      unplacedPredCount.set(next, unplacedPredCount.get(next)! - 1);
    }
  }

  // Stage B: Re-verify Top-Window orthogonal cap after lineage pull-up / anchor delays.
  const currentRankOf = (order: readonly string[]): Map<string, number> => {
    const map = new Map<string, number>();
    order.forEach((ref, index) => map.set(ref, index + 1));
    return map;
  };

  let rankMap = currentRankOf(finalOrder);
  const countOrthInTop = (order: readonly string[]): number => {
    let count = 0;
    for (let index = 0; index < effectiveTopWindow; index++) {
      if (itemByRef.get(order[index]!)!.isOrthogonal) count++;
    }
    return count;
  };
  const hasNonOrthBelowTop = (order: readonly string[]): boolean => {
    for (let index = effectiveTopWindow; index < order.length; index++) {
      if (!itemByRef.get(order[index]!)!.isOrthogonal) return true;
    }
    return false;
  };
  const rebuildFinalOrder = (movableOrder: readonly string[]): string[] => {
    const rebuilt = [...finalOrder];
    let cursor = 0;
    for (let index = 0; index < totalCount; index++) {
      if (!lockedRankToRef.has(index + 1)) {
        rebuilt[index] = movableOrder[cursor++]!;
      }
    }
    return rebuilt;
  };
  const preservesPrimaryPrecedence = (order: readonly string[]): boolean => {
    const ranks = currentRankOf(order);
    for (const [ref, predSet] of preds.entries()) {
      for (const pred of predSet) {
        if (ranks.get(pred)! >= ranks.get(ref)!) return false;
      }
    }
    return true;
  };

  while (
    countOrthInTop(finalOrder) > maxOrthogonalInTopWindow &&
    hasNonOrthBelowTop(finalOrder)
  ) {
    // Find rightmost movable orthogonal item in Top Window with no successors in Top Window.
    let orthMovableIndex = -1;
    const movableSeq = finalOrder.filter((ref) => !protectedSet.has(ref));
    for (let mIdx = openSlotsInTop - 1; mIdx >= 0; mIdx--) {
      const candidate = movableSeq[mIdx]!;
      if (!itemByRef.get(candidate)!.isOrthogonal) continue;
      const hasTopWindowSuccessor = [...succs.get(candidate)!].some(
        (succ) => rankMap.get(succ)! <= effectiveTopWindow,
      );
      if (!hasTopWindowSuccessor) {
        orthMovableIndex = mIdx;
        break;
      }
    }
    // Find leftmost movable non-orthogonal item below Top Window whose predecessors are all in Top Window.
    let replacementMovableIndex = -1;
    let replacementFinalOrder: string[] | null = null;
    for (let mIdx = openSlotsInTop; mIdx < movableSeq.length; mIdx++) {
      const candidate = movableSeq[mIdx]!;
      if (itemByRef.get(candidate)!.isOrthogonal) continue;
      const allPredsInTop = [...preds.get(candidate)!].every(
        (pred) => rankMap.get(pred)! <= effectiveTopWindow,
      );
      if (!allPredsInTop) continue;
      const candidateMovable = [...movableSeq];
      candidateMovable.splice(mIdx, 1);
      candidateMovable.splice(orthMovableIndex, 1);
      candidateMovable.splice(openSlotsInTop - 1, 0, candidate);
      candidateMovable.splice(openSlotsInTop, 0, movableSeq[orthMovableIndex]!);
      const candidateOrder = rebuildFinalOrder(candidateMovable);
      if (preservesPrimaryPrecedence(candidateOrder)) {
        replacementMovableIndex = mIdx;
        replacementFinalOrder = candidateOrder;
        break;
      }
    }
    if (
      orthMovableIndex === -1 ||
      replacementMovableIndex === -1 ||
      replacementFinalOrder === null
    ) {
      fail(
        "cannot satisfy max_orthogonal_in_top_window without violating primary_source_refs or protected anchors",
      );
    }
    const shiftedOrth = movableSeq[orthMovableIndex]!;
    const promotedNonOrth = movableSeq[replacementMovableIndex]!;
    deferredByCapSet.add(shiftedOrth);
    movableSeq.splice(replacementMovableIndex, 1);
    movableSeq.splice(orthMovableIndex, 1);
    movableSeq.splice(openSlotsInTop - 1, 0, promotedNonOrth);
    movableSeq.splice(openSlotsInTop, 0, shiftedOrth);
    finalOrder = replacementFinalOrder;
    rankMap = currentRankOf(finalOrder);
  }

  // Final closed-loop postcondition verification across all 4 structural invariants.
  if (finalOrder.length !== totalCount || new Set(finalOrder).size !== totalCount) {
    fail("wave rerank failed lossless membership verification");
  }
  for (const ref of protectedRefs) {
    if (rankMap.get(ref) !== originalRankByRef.get(ref)) {
      fail(`wave rerank violated protected rank for ${ref}`);
    }
  }
  for (const [ref, predSet] of preds.entries()) {
    const secondaryRank = rankMap.get(ref)!;
    for (const pred of predSet) {
      if (rankMap.get(pred)! >= secondaryRank) {
        fail(
          `wave rerank violated primary-over-secondary precedence: ${pred} must precede ${ref}`,
        );
      }
    }
  }
  for (const item of itemByRef.values()) {
    if (
      item.blocksActiveDeliverable &&
      item.evidenceRole === "load_bearing_primary"
    ) {
      const landedRank = rankMap.get(item.materialRef)!;
      if (landedRank > topWindowSize) {
        fail(
          `load-bearing deliverable blocker ${item.materialRef} landed at rank ${landedRank} outside top_window_size=${topWindowSize} (open_top_window_slots=${openSlotsInTop})`,
        );
      }
    }
  }

  let prevUntouchedOriginalRank = 0;
  for (const ref of finalOrder) {
    const item = itemByRef.get(ref)!;
    if (
      !item.annotated &&
      !protectedSet.has(ref) &&
      (succs.get(ref)?.size ?? 0) === 0
    ) {
      if (item.originalRank <= prevUntouchedOriginalRank) {
        fail("wave rerank violated relative order of unannotated materials");
      }
      prevUntouchedOriginalRank = item.originalRank;
    }
  }

  // Assemble moves, bounds, wave counts, and audit lists.
  const waveCounts: Record<MaterialEffectiveWave, number> = {
    imminent_gate: 0,
    active_sprint: 0,
    foundational_track: 0,
    unannotated_retention: 0,
    orthogonal_backlog: 0,
  };
  const demotedMonographRefs: string[] = [];
  const shiftedOrthogonalRefs: string[] = [];
  const lineagePromotedRefs: string[] = [];
  const moves: JsonObject[] = [];
  let maxDisplacement = 1;

  for (let index = 0; index < finalOrder.length; index++) {
    const ref = finalOrder[index]!;
    const toRank = index + 1;
    const item = itemByRef.get(ref)!;
    waveCounts[item.effectiveWave]++;
    if (item.monographStratified) {
      demotedMonographRefs.push(ref);
    }
    if (
      item.isOrthogonal &&
      toRank > effectiveTopWindow &&
      (deferredByCapSet.has(ref) || item.originalRank <= effectiveTopWindow)
    ) {
      shiftedOrthogonalRefs.push(ref);
    }
    if (lineagePromotedSet.has(ref) && toRank < item.originalRank) {
      lineagePromotedRefs.push(ref);
    }
    if (item.originalRank !== toRank) {
      const displacement = Math.abs(item.originalRank - toRank);
      if (displacement > maxDisplacement) {
        maxDisplacement = displacement;
      }
      let reasonCode = item.explicitReasonCode;
      if (reasonCode === null) {
        if (item.monographStratified) {
          reasonCode = "unsliced_monograph_stratified";
        } else if (
          item.isOrthogonal &&
          toRank > effectiveTopWindow &&
          (deferredByCapSet.has(ref) || item.evidenceRole === "orthogonal_signal")
        ) {
          reasonCode = "orthogonal_top_window_cap";
        } else if (lineagePromotedSet.has(ref) && toRank < item.originalRank) {
          reasonCode = "primary_source_lineage_precedence";
        } else if (item.effectiveWave === "imminent_gate") {
          reasonCode = "imminent_gate_priority";
        } else if (item.effectiveWave === "active_sprint") {
          reasonCode = "active_sprint_priority";
        } else if (item.effectiveWave === "foundational_track") {
          reasonCode = "foundational_track_priority";
        } else if (item.effectiveWave === "orthogonal_backlog") {
          reasonCode = "orthogonal_backlog_demotion";
        } else {
          reasonCode = "wave_rebalance_shift";
        }
      }
      moves.push({
        material_ref: ref,
        from_rank: item.originalRank,
        to_rank: toRank,
        reason_code: reasonCode,
        evidence_refs: item.evidenceRefs ?? defaultEvidenceRefs,
      });
    }
  }

  return {
    schema_version: MATERIAL_WAVE_RERANK_PLAN_SCHEMA_VERSION,
    reordered_material_refs: finalOrder,
    top_window_size: topWindowSize,
    max_orthogonal_in_top_window: maxOrthogonalInTopWindow,
    no_change: moves.length === 0,
    constraints: {
      target_window_size: totalCount,
      max_moved_items: Math.max(1, moves.length),
      max_rank_displacement: maxDisplacement,
      protected_material_refs: protectedRefs,
    },
    moves,
    wave_counts: waveCounts,
    top_window_orthogonal_count: countOrthInTop(finalOrder),
    demoted_monograph_refs: demotedMonographRefs,
    shifted_orthogonal_refs: shiftedOrthogonalRefs,
    lineage_promoted_refs: lineagePromotedRefs,
  };
}
