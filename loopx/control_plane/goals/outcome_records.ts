/** Versioned record pages inside the existing atomic authority snapshot.
 * Pages bound individual serialization units, not the lifetime of a Goal.
 * The provider still loads/commits the whole snapshot; this is not an indexed
 * database or a claim of constant-cost reads. No external files can go missing
 * between a declaration, its origins and the existing operation receipt. */
import type {JsonObject} from "../effect_program.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, hasExactAuthorityKeys, canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";

export const OUTCOME_RECORDS_SCHEMA = "loopx_goal_outcome_records_v1";
const PAGE_BYTES = 131072;
const PAGE_ITEMS = 32;
const bytes = (value: unknown) => Buffer.byteLength(JSON.stringify(value), "utf8");
function check(value: unknown, reason: string): asserts value {
  if (!value) throw new AuthorityStoreProtocolError(reason);
}
function pages(records: unknown[], envelope: (items: unknown[]) => unknown = items => items): unknown[][] {
  const result: unknown[][] = [];
  let page: unknown[] = [];
  for (const record of records) {
    check(bytes(envelope([record])) <= PAGE_BYTES, "outcome record exceeds page capacity");
    if (page.length && (page.length === PAGE_ITEMS || bytes(envelope([...page, record])) > PAGE_BYTES)) {
      result.push(page); page = [];
    }
    page.push(record);
  }
  if (page.length) result.push(page);
  return result;
}
function records(value: unknown): unknown[] {
  check(Array.isArray(value), "outcome record pages must be an array");
  return value.flatMap(page => {
    check(Array.isArray(page) && page.length > 0 && page.length <= PAGE_ITEMS && bytes(page) <= PAGE_BYTES,
      "outcome record page is empty or exceeds capacity");
    return page;
  });
}

export function encodeOutcomeRecords(state: JsonObject): JsonObject {
  const {outcomes, ...metadata} = state;
  const headers: JsonObject[] = [], bodies: JsonObject[] = [];
  // One header per outcome plus typed collection pages avoids an ever-growing
  // outcome object becoming a supposedly bounded page record itself.
  for (const raw of outcomes as JsonObject[]) {
    const {requirements, sources, todo_ids, review, ...header} = raw;
    headers.push({...header, collection_digest: canonicalAuthoritySha256({requirements, sources, todo_ids, ...(review ? {review} : {})})});
    for (const [kind, values] of [["requirements", requirements], ["sources", sources], ["todo_ids", todo_ids]] as const)
      for (const items of pages(values as unknown[], items => ({outcome_id: raw.id, kind, items})))
        bodies.push({outcome_id: raw.id, kind, items});
    if (review) {
      const row = review as JsonObject;
      headers[headers.length - 1].review_basis_digest = row.basis_digest;
      for (const items of pages(row.items as unknown[], items => ({outcome_id: raw.id, kind: "review", items})))
        bodies.push({outcome_id: raw.id, kind: "review", items});
    }
  }
  return {...metadata, schema_version: OUTCOME_RECORDS_SCHEMA, headers: pages(headers), header_digest: canonicalAuthoritySha256(headers), collections: bodies};
}

export function decodeOutcomeRecords(state: JsonObject): JsonObject {
  check(hasExactAuthorityKeys(state, ["schema_version", "goal_id", "goal_ref", "revision", "headers", "header_digest", "collections"]),
    "outcome record state has unsupported fields");
  const headers = records(state.headers);
  check(canonicalAuthoritySha256(headers) === state.header_digest, "outcome header inventory digest mismatch");
  const outcomes = headers.map(value => {
    const row = canonicalAuthorityObject(value, "outcome header");
    check(hasExactAuthorityKeys(row, ["id", "owner_agent_id", "objective", "revision", "collection_digest",
      ...(Object.hasOwn(row, "review_basis_digest") ? ["review_basis_digest"] : [])]), "outcome header has unsupported fields");
    const {review_basis_digest, collection_digest, ...header} = row;
    check(typeof collection_digest === "string", "outcome record inventory digest is missing");
    return {...header, requirements: [], sources: [], todo_ids: [],
      ...(review_basis_digest === undefined ? {} : {review: {basis_digest: review_basis_digest, items: []}})} as JsonObject;
  });
  const byId = new Map(outcomes.map(row => [row.id, row]));
  check(byId.size === outcomes.length, "outcome record headers contain duplicate ids");
  check(Array.isArray(state.collections), "outcome collections must be an array");
  for (const value of state.collections) {
    const row = canonicalAuthorityObject(value, "outcome collection");
    check(hasExactAuthorityKeys(row, ["outcome_id", "kind", "items"]) && bytes(row) <= PAGE_BYTES, "outcome collection has unsupported fields");
    const outcome = byId.get(row.outcome_id);
    const kind = row.kind;
    check(outcome && typeof kind === "string" && ["requirements", "sources", "todo_ids", "review"].includes(kind),
      "outcome collection has no matching owner");
    const target = kind === "review" ? (outcome.review as JsonObject | undefined)?.items : outcome[kind];
    check(Array.isArray(target), "outcome collection has no matching declaration");
    target.push(...records([row.items]));
  }
  for (const [index, outcome] of outcomes.entries()) {
    const {requirements, sources, todo_ids, review} = outcome;
    check(canonicalAuthoritySha256({requirements, sources, todo_ids, ...(review ? {review} : {})}) ===
      (headers[index] as JsonObject).collection_digest, "outcome record inventory digest mismatch");
  }
  return {schema_version: OUTCOME_RECORDS_SCHEMA, goal_id: state.goal_id, goal_ref: state.goal_ref,
    revision: state.revision, outcomes};
}
