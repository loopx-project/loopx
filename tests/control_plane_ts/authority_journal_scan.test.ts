import assert from "node:assert/strict";
import test from "node:test";
import {AuthorityJournalScan} from "../../loopx/control_plane/coordination/authority_journal_scan.ts";
import {decodeAuthorityTransaction} from "../../loopx/control_plane/coordination/authority_store_transactions.ts";
import {parseAuthorityCursor} from "../../loopx/control_plane/coordination/authority_store_codec.ts";

const row = (cursor: string) => ({cursor, provider_revision: `revision-${cursor}`,
  operation_id: `operation-${cursor}`, events: [{sequence: cursor}], receipts: [{accepted: true}],
  projection: {nested: {sequence: cursor}}});
const head = (cursor: string) => ({cursor, provider_revision: `revision-${cursor}`, head: row(cursor).projection});
const scan = (after: string | null, limit: number) => {
  const result = AuthorityJournalScan.prepare(after, limit);
  assert.ok(result instanceof AuthorityJournalScan);
  return result;
};

test("scan snapshot rejects gaps, repeated identities, short pages and unproved lookahead", () => {
  const candidates = [[], [row("1")], [row("1"), row("3")],
    [row("1"), {...row("2"), operation_id: "operation-1"}],
    [row("1"), {...row("2"), cursor: "01"}],
    [row("1"), row("2"), row("3")]];
  for (const rows of candidates) {
    assert.throws(() => scan(null, 1).page(rows, head("3")), /committed scan|provider cursor/);
  }
  assert.throws(() => scan("1", 10).page([row("2")], head("3")), /snapshot interval/);
});

test("a final row and a lookahead head must match the observed head exactly", () => {
  for (const limit of [1, 2]) {
    for (const corrupt of [{...head("2"), provider_revision: "other"},
      {...head("2"), head: {nested: {sequence: "forged"}}}]) {
      assert.throws(() => scan(null, limit).page([row("1"), row("2")], corrupt), /head lineage/);
    }
  }
});

test("scan checkpoints preserve bigint precision and output value isolation", () => {
  const offset = "9007199254740993";
  const next = "9007199254740994";
  const rows = [row(next)];
  const page = scan(offset, Number.MAX_SAFE_INTEGER).page(rows, head(next));
  assert.equal(page.status, "page");
  if (page.status !== "page") return;
  assert.equal(page.next_cursor, next);
  assert.equal(page.has_more, false);
  page.transactions[0]!.projection.nested = "changed";
  assert.deepEqual(rows[0]!.projection.nested, {sequence: next});
  assert.deepEqual(scan(next, 1).page([], head(next)),
    {status: "page", transactions: [], next_cursor: next, has_more: false});
});

test("scan requests reject malformed types before a provider effect", () => {
  for (const limit of [0, -1, 1.5, Infinity, NaN, "1", 1n, Number.MAX_SAFE_INTEGER + 1]) {
    const result = AuthorityJournalScan.prepare(null, limit as number);
    assert.ok(!(result instanceof AuthorityJournalScan));
    assert.equal(result.reason_code, "invalid_scan_request");
  }
  for (const cursor of [1, 1n, ["1"], {toString: () => "1"}, undefined]) {
    assert.throws(() => parseAuthorityCursor(cursor as string), /provider cursor/);
  }
});

test("scan pages copy every mutable JSON container and retain row metadata", () => {
  const rows = ["1", "2"].map(cursor => ({
    ...row(cursor),
    projection: {...row(cursor).projection, padding: "p".repeat(1024 * 1024),
      ["__proto__"]: {marker: "data"}, negative: -0, holes: Array(2)},
    events: [{sequence: cursor, detail: {marker: "event"}}],
    receipts: [{accepted: true, detail: {marker: "receipt"}}],
    provenance: {marker: "retained metadata"},
  }));
  const observed = {...head("2"), head: rows[1]!.projection};
  const page = scan(null, 2).page(rows, observed);
  assert.equal(page.status, "page");
  if (page.status !== "page") return;
  assert.deepEqual(page.transactions, rows);
  assert.deepEqual(Object.keys(page.transactions[0]!), Object.keys(rows[0]!));
  assert.deepEqual(Object.keys(page.transactions[0]!.projection), Object.keys(rows[0]!.projection));
  const first = page.transactions[0]!;
  assert.equal(Object.is(first.projection.negative, -0), true);
  assert.equal(0 in (first.projection.holes as unknown[]), false);
  (first.projection["__proto__"] as {marker: string}).marker = "edited";
  (first.events[0]!.detail as {marker: string}).marker = "edited";
  (first.receipts[0]!.detail as {marker: string}).marker = "edited";
  assert.deepEqual(rows[0]!.projection["__proto__"], {marker: "data"});
  assert.deepEqual(rows[0]!.events[0]!.detail, {marker: "event"});
  assert.deepEqual(rows[0]!.receipts[0]!.detail, {marker: "receipt"});
  assert.deepEqual(page.transactions[1], rows[1]);
  assert.equal(({} as Record<string, unknown>).marker, undefined);
});

test("transaction decoder requires a canonical positive decimal cursor", () => {
  for (const cursor of ["0", "01", "-1", "1.0", "unknown", 1, null]) {
    assert.throws(() => decodeAuthorityTransaction({...row("1"), cursor}), /cursor/);
  }
});
