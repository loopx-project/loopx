/** Content-ops owns style metadata; the caller's catalog remains authoritative.
 * No provider reads, store writes, lifecycle transitions or publication authority.
 */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

type ObjectValue = Record<string, unknown>;
export const CONTENT_REFERENCE_SCHEMA = "content_ops_reference_v0";
export const CONTENT_REFERENCE_RESULT_SCHEMA = "content_ops_reference_result_v0";

function object(value: unknown, field: string): ObjectValue {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new EffectRuntimeRequestError(`${field} must be an object`);
  return value as ObjectValue;
}
function text(value: unknown, field: string, optional = false): string | null {
  if (optional && (value === undefined || value === null)) return null;
  if (typeof value !== "string" || !value.trim() || value.length > 2000 || value.includes("\0")) throw new EffectRuntimeRequestError(`${field} must be nonempty text (at most 2000 characters)`);
  return value.trim();
}
function token(value: unknown, field: string): string {
  const result = text(value, field)!;
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(result)) throw new EffectRuntimeRequestError(`${field} must be a compact stable reference`);
  return result;
}
function timestamp(value: unknown, field: string, optional = false): string | null {
  const result = text(value, field, optional);
  if (result !== null && (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(result) || !Number.isFinite(Date.parse(result)))) throw new EffectRuntimeRequestError(`${field} must be an ISO timestamp with timezone`);
  if (result !== null) {
    const [year, month, day] = result.slice(0, 10).split("-").map(Number);
    if (!year || !month || !day || month > 12 || day > new Date(Date.UTC(year, month, 0)).getUTCDate()) throw new EffectRuntimeRequestError(`${field} contains an invalid calendar date`);
  }
  return result;
}
function strings(value: unknown, field: string): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 40) throw new EffectRuntimeRequestError(`${field} must be an array of at most 40 strings`);
  return value.map((entry, index) => text(entry, `${field}[${index}]`)!);
}
function publicUrl(value: unknown): string {
  const raw = text(value, "source_url")!;
  let url: URL;
  try {url = new URL(raw);} catch {throw new EffectRuntimeRequestError("source_url must be an HTTPS source URL");}
  // A locator is metadata only. Nothing here fetches or establishes access.
  if (url.protocol !== "https:" || url.username || url.password) throw new EffectRuntimeRequestError("source_url must be HTTPS without credentials");
  return url.href;
}
export type ContentReference = {
  schema_version: typeof CONTENT_REFERENCE_SCHEMA;
  id: string; reference_ref: string; source_url: string; source_revision: string | null;
  title: string; author: string; captured_at: string; published_at: string | null;
  opening: string | null; tone: string | null; structure: string[];
  tags: string[]; uses: string[]; caveats: string[]; reuse_boundary: string | null; reading_boundary: string | null;
  lifecycle_state: string | null;
  engagement: {observed_at: string; counts: Record<string, number>} | null;
};
export function readContentReference(input: unknown): ContentReference {
  const value = object(input, "reference");
  const id = token(value.id, "id");
  const revision = value.source_revision == null ? null : token(value.source_revision, "source_revision");
  const style = value.style == null ? {} : object(value.style, "style");
  let engagement: ContentReference["engagement"] = null;
  if (value.metrics != null) {
    const metrics = object(value.metrics, "metrics");
    const counts = object(metrics.counts, "metrics.counts");
    const checked: Record<string, number> = {};
    for (const [key, count] of Object.entries(counts)) {
      token(key, "metric name");
      if (typeof count !== "number" || !Number.isSafeInteger(count) || count < 0) throw new EffectRuntimeRequestError("engagement counts must be nonnegative safe integers");
      checked[key] = count;
    }
    engagement = {observed_at: timestamp(metrics.observed_at, "metrics.observed_at")!, counts: checked};
  }
  return {schema_version: CONTENT_REFERENCE_SCHEMA, id,
    reference_ref: `content-reference:${id}:${revision ?? "unversioned"}`,
    source_url: publicUrl(value.source_url), source_revision: revision,
    title: text(value.title, "title")!, author: text(value.author, "author")!,
    captured_at: timestamp(value.captured_at, "captured_at")!,
    published_at: timestamp(value.published_at, "published_at", true),
    opening: text(style.opening, "style.opening", true), tone: text(style.tone, "style.tone", true),
    structure: strings(value.structure, "structure"), tags: strings(value.tags, "tags"),
    uses: strings(value.uses, "uses"), caveats: strings(value.caveats, "caveats"),
    reuse_boundary: text(value.reuse_boundary, "reuse_boundary", true),
    reading_boundary: text(value.reading_boundary, "reading_boundary", true),
    lifecycle_state: text(value.lifecycle_state, "lifecycle_state", true), engagement};
}
export function readContentReferenceLibrary(input: unknown): {library: ObjectValue; references: ContentReference[]} {
  const library = object(input, "library");
  if (!Array.isArray(library.entries) || library.entries.length > 10000) throw new EffectRuntimeRequestError("library.entries must be an array of at most 10000 references");
  const references = library.entries.map(readContentReference);
  const ids = new Set<string>(), sources = new Set<string>();
  for (const reference of references) {
    if (ids.has(reference.id) || sources.has(reference.source_url)) throw new EffectRuntimeRequestError("library contains duplicate stable IDs or source URLs; reconcile in the original store");
    ids.add(reference.id); sources.add(reference.source_url);
  }
  return {library, references};
}
function result() {
  return {ok: true, schema_version: CONTENT_REFERENCE_RESULT_SCHEMA, capability_id: "content-ops",
    visibility: "local_private", source_read_performed: false, store_write_performed: false,
    lifecycle_write_performed: false, publish_authorized: false};
}

/** Search observations, never rank by virality or silently enable a source. */
export function searchContentReferences(input: unknown) {
  const request = object(input, "request");
  const {references} = readContentReferenceLibrary(request.library);
  const filter = (value: unknown, field: string) => value == null ||
    (typeof value === "string" && value.length <= 2000 && !value.trim()) ? "" : text(value, field)!.toLocaleLowerCase();
  const query = filter(request.query, "query");
  const structure = filter(request.structure, "structure");
  const selected = references.filter(reference => reference.lifecycle_state !== "archived" &&
    [reference.title, ...reference.tags, ...reference.uses, ...reference.structure].join(" ").toLocaleLowerCase().includes(query) &&
    reference.structure.join(" ").toLocaleLowerCase().includes(structure));
  return {...result(), references: selected, total_count: references.length,
    unknown_lifecycle_count: references.filter(reference => reference.lifecycle_state === null).length,
    limitations: ["Legacy unversioned entries remain unversioned; absence of lifecycle state does not mean active.",
      "Engagement is a dated observation, not proof of virality, adoption or writing effectiveness.",
      "Permission to read, reuse or publish is not inferred from a URL or library membership."]};
}

/** Prepare an append/correction; preserve unknown legacy fields without promoting them. */
export function captureContentReference(input: unknown) {
  const request = object(input, "request");
  const {library, references} = readContentReferenceLibrary(request.library);
  const candidate = object(request.reference, "reference");
  // Capability-local input shape: raw provider bodies belong in their existing private backing.
  const allowed = new Set(["id", "title", "source_url", "author", "source_revision", "captured_at", "published_at", "style", "tags", "structure", "uses", "caveats", "reuse_boundary", "reading_boundary", "metrics"]);
  for (const key of Object.keys(candidate)) if (!allowed.has(key)) throw new EffectRuntimeRequestError(`unsupported capture field: ${key}; keep raw/source-specific material in its original backing`);
  const style = candidate.style == null ? {} : object(candidate.style, "style");
  if (Object.keys(style).some(key => !["opening", "tone"].includes(key))) throw new EffectRuntimeRequestError("style accepts opening and tone only");
  const next = readContentReference(candidate);
  if (!next.source_revision || !next.reuse_boundary || !next.reading_boundary || !next.opening || !next.tone || !next.structure.length || !next.caveats.length) throw new EffectRuntimeRequestError("capture requires source_revision, reuse_boundary, reading_boundary, opening, tone, structure and caveats (record unknown explicitly)");
  const previous = references.find(reference => reference.id === next.id);
  const sameSource = references.find(reference => reference.source_url === next.source_url);
  if (sameSource && sameSource.id !== next.id) throw new EffectRuntimeRequestError(`source already belongs to ${sameSource.id}; reuse that identity`);
  if (previous && previous.source_url !== next.source_url) throw new EffectRuntimeRequestError("a stable ID cannot be reassigned to another source");
  if (previous && request.expected_source_revision !== previous.source_revision) throw new EffectRuntimeRequestError("source revision changed or unknown; read it and provide expected_source_revision, including null for legacy data");
  const entries = library.entries as ObjectValue[];
  const prepared: ObjectValue = {...candidate, source_url: next.source_url};
  const nextEntries = previous ? entries.map(entry => entry.id === next.id ?
    {...entry, ...prepared, style: {...object(entry.style ?? {}, "style"), ...style},
      ...(candidate.metrics != null ? {metrics: {...object(entry.metrics ?? {}, "metrics"), ...object(candidate.metrics, "metrics")}} : {})} : entry) : [...entries, prepared];
  const preparedReference = readContentReference(nextEntries.find(entry => entry.id === next.id));
  return {...result(), reference: preparedReference, library: {...library, entries: nextEntries},
    material_ref: next.id, previous_source_revision: previous?.source_revision ?? null,
    note: "Prepared local artifact only. Original catalog remains authoritative; use a separately qualified material provider for apply/rollback."};
}

/** An attributed outline uses structure, never copies the original post body. */
export function planReferenceDraft(input: unknown) {
  const request = object(input, "request");
  const {references} = readContentReferenceLibrary(request.library);
  const id = token(request.reference_id, "reference_id");
  const reference = references.find(value => value.id === id);
  if (!reference) throw new EffectRuntimeRequestError("reference is unavailable in this catalog revision");
  if (request.expected_source_revision !== reference.source_revision) throw new EffectRuntimeRequestError("source revision changed; retrieve and reassess before drafting");
  if (reference.lifecycle_state === "archived") throw new EffectRuntimeRequestError("reference is archived; ask its material owner to reactivate it");
  if (!reference.reuse_boundary) throw new EffectRuntimeRequestError("reuse boundary is unknown; record it before preparing a draft");
  const subject = text(request.subject, "subject")!;
  const facts = strings(request.facts, "facts");
  if (!facts.length) throw new EffectRuntimeRequestError("draft requires caller-supplied facts; source claims are not facts about your product");
  const steps = reference.structure.map((role, index) => ({role, instruction: `Apply ${role} to ${subject}; use only independently supported facts.`, fact: facts[index] ?? null}));
  return {...result(), subject, reference_ref: reference.reference_ref,
    source_map: [{source_item_id: id, source_url: reference.source_url, source_revision: reference.source_revision,
      attribution: reference.author, use: "structure_reference", reuse_boundary: reference.reuse_boundary}],
    steps, unused_facts: facts.slice(steps.length), caveats: reference.caveats,
    attribution_text: `Structure reference: ${reference.author} — ${reference.source_url}`,
    note: "An outline, not a verified final post. Review every fact and the source's reuse conditions before publication."};
}
