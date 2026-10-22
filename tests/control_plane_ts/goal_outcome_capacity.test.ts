import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {Pool} from "pg";
import {randomUUID} from "node:crypto";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {PostgreSqlAuthorityStore, installPostgreSqlAuthorityStoreSchema} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {refineGoalOutcome, GoalOutcomeValidationError} from "../../loopx/control_plane/goals/outcome_requirements.ts";
import {inspectGoalAcceptance, refineGoalAcceptanceOutcome} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";

const goal = "outcome-capacity";
const bytes = (value: unknown) => Buffer.byteLength(JSON.stringify(value), "utf8");
function crowded() {
  const state = {schema_version: "loopx_goal_outcomes_v0", goal_id: goal, goal_ref: null, revision: 1,
    outcomes: Array.from({length: 20}, (_, i) => ({id: `outcome-${i}`, owner_agent_id: "author", objective: `Deliver result ${i}`,
      revision: 1, requirements: Array.from({length: 5}, (_, r) => ({id: `r${r}`, description: `Retain requirement ${r}`})),
      todo_ids: [], sources: [{ref: `origin-${i}`, summary: `Owner requested result ${i}`}]}))};
  for (let i = 0; bytes(state) < 130400; i++) {
    state.outcomes[i % 20].sources.push({ref: `source-${i}`, summary: `Retained origin ${i}: ${"x".repeat(480)}`});
  }
  // Leave less room than even one complete review, while keeping the legacy
  // declaration valid and every source's independently bounded original text.
  const source = {ref: "boundary", summary: "x"};
  state.outcomes[0].sources.push(source);
  const available = 131000 - bytes(state);
  assert.ok(available >= 0 && available < 512);
  source.summary += "x".repeat(available);
  assert.equal(bytes(state), 131000);
  return authorityProjectionFixture(goal, [], [], "native", {goal_outcomes: state});
}
const review = {outcome_id: "outcome-19", review: Array.from({length: 5}, (_, i) => ({id: `r${i}`,
  conclusion: "uncertain", evidence_refs: ["artifact:current-check"], note: "Independent acceptance is still pending."}))};

test("a full retained declaration can receive a complete review without losing origins", () => {
  const head = crowded(), original = structuredClone(head);
  const state = refineGoalOutcome(head, goal, null, "author", review);
  assert.ok(bytes(state) > 131072, "reproduces the previous combined-cap rejection");
  assert.ok(bytes(state) <= 262144, "declarations and reviews remain bounded together");
  const before = (original.goal_outcomes as JsonObject).outcomes as JsonObject[];
  for (const item of state.outcomes) {
    const prior = before.find(row => row.id === item.id)!;
    for (const field of ["objective", "owner_agent_id", "requirements", "todo_ids", "sources"]) assert.deepEqual(item[field], prior[field]);
  }
  assert.deepEqual(state.outcomes.find(o => o.id === review.outcome_id)!.review!.items, review.review);
  assert.deepEqual(head, original, "pure refinement never mutates its input");
  assert.throws(() => refineGoalOutcome({...head, goal_outcomes: state}, goal, null, "author", {
    outcome_id: "outcome-0", source: {ref: "overflow", summary: "x".repeat(512)},
  }), /declaration capacity exceeded/, "review allowance cannot expand declaration capacity");
});

test("review capacity measures UTF-8 bytes across all retained reviews", () => {
  let head = authorityProjectionFixture(goal, [], [], "native");
  const fullReview = Array.from({length: 32}, (_, i) => ({id: `r${i}`, conclusion: "uncertain", evidence_refs: [], note: "界".repeat(512)}));
  for (let i = 0; i < 3; i++) {
    head = {...head, goal_outcomes: refineGoalOutcome(head, goal, null, "author", {
      outcome_id: `outcome-${i}`, owner_agent_id: "author", objective: `Deliver result ${i}`,
      requirements: fullReview.map(({id}) => ({id, description: `Keep ${id}`})), source: {ref: `origin-${i}`, summary: "Original request"},
    })};
  }
  for (let i = 0; i < 2; i++) head = {...head, goal_outcomes: refineGoalOutcome(head, goal, null, "author", {outcome_id: `outcome-${i}`, review: fullReview})};
  const before = structuredClone(head);
  assert.throws(() => refineGoalOutcome(head, goal, null, "author", {outcome_id: "outcome-2", review: fullReview}), /review capacity exceeded/);
  assert.deepEqual(head, before);
  // Replacing current evidence retains the original declarations and journal
  // semantics; the current-review budget is not an append-only history budget.
  const small = fullReview.map(row => ({...row, note: "Uncertain"}));
  head = {...head, goal_outcomes: refineGoalOutcome(head, goal, null, "author", {outcome_id: "outcome-0", review: small})};
  assert.equal(refineGoalOutcome(head, goal, null, "author", {outcome_id: "outcome-2", review: fullReview}).outcomes.length, 3);
});

for (const provider of ["file", "sqlite", "postgresql"] as const) test(`${provider}: crowded review survives restart and replay; overflow is atomic`,
  {skip: provider === "postgresql" && !process.env.LOOPX_TEST_POSTGRES_URL}, async t => {
  const root = await mkdtemp(join(tmpdir(), "outcome-capacity-")); t.after(() => rm(root, {recursive: true, force: true}));
  const pool = provider === "postgresql" ? new Pool({connectionString: process.env.LOOPX_TEST_POSTGRES_URL, max: 2}) : null;
  if (pool) t.after(() => pool.end());
  const database = pool ? {connect: async () => {
    const client = await pool.connect();
    return {query: async (sql: string, values?: readonly unknown[]) => client.query(sql, values ? [...values] : undefined),
      release: () => client.release()};
  }} : null;
  if (database) await installPostgreSqlAuthorityStoreSchema(database, `postgresql:${"b".repeat(32)}`);
  const scope = {tenant_id: `outcome-capacity-${randomUUID()}`, goal_id: goal};
  const open = () => database ? new PostgreSqlAuthorityStore(database, scope)
    : provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  const store = open(), head = crowded();
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: head, events: [], receipts: []});
  const prior = await inspectGoalAcceptance(store, goal);
  const request = {goal_id: goal, actor_agent_id: "author", operation_id: "review", expected_provider_revision: prior.provider_revision, refinement: review};
  const preview = await refineGoalAcceptanceOutcome(store, {...request, dry_run: true});
  assert.equal(preview.status, "planned");
  assert.deepEqual(await inspectGoalAcceptance(store, goal), prior);
  assert.equal((await refineGoalAcceptanceOutcome(store, request)).status, "applied");
  const after = await inspectGoalAcceptance(open(), goal);
  const outcomes = (after.goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject;
  const rows = outcomes.outcomes as JsonObject[];
  assert.equal(rows.length, 20);
  const current = rows.find(row => row.id === review.outcome_id)!;
  assert.ok((current.requirements as JsonObject[]).every(r => r.status === "unverified" && r.planning_satisfied === false));
  assert.equal((await refineGoalAcceptanceOutcome(open(), request)).status, "replayed");
  assert.deepEqual(await inspectGoalAcceptance(open(), goal), after);
  assert.equal((await refineGoalAcceptanceOutcome(store, {...request, operation_id: "stale"})).status, "conflict");
  await assert.rejects(() => refineGoalAcceptanceOutcome(store, {...request, operation_id: "overflow", expected_provider_revision: after.provider_revision,
    refinement: {outcome_id: "outcome-0", source: {ref: "overflow", summary: "x".repeat(512)}}}), /declaration capacity exceeded/);
  assert.deepEqual(await inspectGoalAcceptance(open(), goal), after);
  const loaded = await open().loadAuthority(); assert.equal(loaded.status, "loaded");
  if (loaded.status === "loaded") {
    const original = (head.goal_outcomes as JsonObject).outcomes as JsonObject[];
    for (const row of (loaded.head.goal_outcomes as JsonObject).outcomes as JsonObject[]) {
      assert.deepEqual(row.sources, original.find(o => o.id === row.id)!.sources);
    }
  }
});

const original = authorityProjectionFixture(goal, [], [], "native");
function declared() {
  return refineGoalOutcome(original, goal, null, "author", {
    outcome_id: "report", owner_agent_id: "author", objective: "Retain the complete result",
    requirements: Array.from({length: 32}, (_, i) => ({id: `r${i}`, description: "保留证据😀".repeat(60)})),
    source: {ref: "message:original", summary: "Synthetic original request"},
  });
}

test("the independent review budget accepts its exact UTF-8 boundary and diagnoses growth", () => {
  const state = declared(), head = {...original, goal_outcomes: state};
  const before = structuredClone(head);
  const review = state.outcomes[0].requirements.map(r => ({id: r.id, conclusion: "uncertain",
    evidence_refs: Array.from({length: 8}, () => "e"), note: "保留证据😀"}));
  // Construct the candidate independently: the digest has fixed width, not an
  // asserted value copied from production. Fill legal reference fields to the
  // frozen byte boundary; character count is deliberately not the oracle.
  const candidate = {...state, revision: 2, outcomes: [{...state.outcomes[0], revision: 2,
    review: {basis_digest: "0".repeat(64), items: review}}]};
  const declaration = {...candidate, outcomes: candidate.outcomes.map(({review: _review, ...row}) => row)};
  let remaining = 131072 - (bytes(candidate) - bytes(declaration));
  assert.ok(remaining > 0);
  for (const item of review) for (let i = 0; i < item.evidence_refs.length && remaining > 0; i++) {
    const added = Math.min(1023, remaining);
    item.evidence_refs[i] += "x".repeat(added); remaining -= added;
  }
  assert.equal(remaining, 0);
  const exact = refineGoalOutcome(head, goal, null, "author", {outcome_id: "report", review});
  assert.equal(bytes(exact) - bytes(declaration), 131072);
  assert.ok(JSON.stringify(exact).length - JSON.stringify(declaration).length < 131072);
  review[0].note += "😀";
  assert.throws(() => refineGoalOutcome(head, goal, null, "author", {outcome_id: "report", review}), error => {
    assert.ok(error instanceof GoalOutcomeValidationError);
    assert.match(error.message, /candidate 131076 UTF-8 bytes/);
    assert.match(error.message, /outcome review capacity exceeded/);
    assert.match(error.message, /retained 0, limit 131072/);
    assert.match(error.message, /all outcomes in the Goal, not the source count/);
    assert.ok(!error.message.includes("Synthetic original request"));
    return true;
  });
  assert.deepEqual(head, before);
  assert.equal(refineGoalOutcome(head, goal, null, "author", {outcome_id: "report", review: review.map(item => ({...item,
    evidence_refs: ["artifact:full-report"], note: "Further validation needed"}))}).outcomes[0].sources.length, 1);
});

test("the aggregate limit counts other outcomes while the source-count limit remains distinct", () => {
  let state = declared();
  for (let i = 0; i < 63; i++) state = refineGoalOutcome({...original, goal_outcomes: state}, goal, null, "author", {
    outcome_id: "report", source: {ref: `message:origin-${i}`, summary: "Retained original provenance"}});
  const head = {...original, goal_outcomes: state};
  assert.throws(() => refineGoalOutcome(head, goal, null, "author", {outcome_id: "report",
    source: {ref: "message:overflow", summary: "Additional request"}}), /outcome source capacity reached/);
  // Individually fitting reviews still share the Goal's review allowance.
  const large = {...state.outcomes[0], id: "another", sources: [{ref: "message:another", summary: "Another request"}]};
  const combined = {...original, goal_outcomes: {...state, outcomes: [...state.outcomes, large]}};
  const review = large.requirements.map(r => ({id: r.id, conclusion: "uncertain", evidence_refs: ["e".repeat(1024), "f".repeat(1024)], note: "n".repeat(512)}));
  assert.ok(refineGoalOutcome({...original, goal_outcomes: {...state, outcomes: [large]}}, goal, null, "author", {outcome_id: "another", review}));
  const first = refineGoalOutcome(combined, goal, null, "author", {outcome_id: "report", review});
  assert.throws(() => refineGoalOutcome({...original, goal_outcomes: first}, goal, null, "author", {outcome_id: "another", review}), error => {
    assert.ok(error instanceof GoalOutcomeValidationError);
    assert.match(error.message, /outcome review capacity exceeded/);
    assert.match(error.message, /all outcomes in the Goal/);
    const retained = bytes(first) - bytes({...first, outcomes: first.outcomes.map(({review: _review, ...row}) => row)});
    assert.ok(retained > 0 && error.message.includes(`retained ${retained}, limit 131072`));
    return true;
  });
});

// The shared owner must reject before any real PostgreSQL transaction commits.
// Run with the existing isolated integration database, never an active Goal.
test("postgresql: rejected capacity update retains state and recovers once", {skip: !process.env.LOOPX_TEST_POSTGRES_URL}, async t => {
  const pool = new Pool({connectionString: process.env.LOOPX_TEST_POSTGRES_URL, max: 2});
  t.after(() => pool.end());
  const database = {connect: async () => {
    const client = await pool.connect();
    return {query: async (sql: string, values?: readonly unknown[]) => client.query(sql, values ? [...values] : undefined),
      release: () => client.release()};
  }};
  await installPostgreSqlAuthorityStoreSchema(database, `postgresql:${"b".repeat(32)}`);
  const store = new PostgreSqlAuthorityStore(database, {tenant_id: `capacity-${randomUUID()}`, goal_id: goal});
  const state = declared();
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    next_projection: {...original, goal_outcomes: state}, events: [], receipts: []});
  const before = await store.loadAuthority();
  assert.equal(before.status, "loaded");
  if (before.status !== "loaded") throw new Error("synthetic authority unavailable");
  const review = state.outcomes[0].requirements.map(r => ({id: r.id, conclusion: "uncertain",
    evidence_refs: Array.from({length: 8}, () => "e".repeat(500)), note: "n".repeat(512)}));
  const request = {goal_id: goal, actor_agent_id: "author", operation_id: "review",
    expected_provider_revision: before.provider_revision, refinement: {outcome_id: "report", review}};
  await assert.rejects(() => refineGoalAcceptanceOutcome(store, request), /all outcomes in the Goal/);
  assert.deepEqual(await store.loadAuthority(), before);
  for (const item of review) {item.evidence_refs = ["artifact:full-report"]; item.note = "Further validation needed";}
  assert.equal((await refineGoalAcceptanceOutcome(store, request)).status, "applied");
  const after = await store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status !== "loaded") throw new Error("synthetic authority unavailable");
  assert.deepEqual(after.head.goal_outcomes.outcomes[0].requirements, state.outcomes[0].requirements);
  assert.deepEqual(after.head.goal_outcomes.outcomes[0].sources, state.outcomes[0].sources);
  assert.equal((await refineGoalAcceptanceOutcome(store, request)).status, "replayed");
  assert.deepEqual(await store.loadAuthority(), after);
});


test("declaration boundary diagnostics do not suggest reducing the separate review envelope", () => {
  const head = crowded(), before = structuredClone(head);
  const source = {ref: "exact-boundary", summary: "x"};
  const probe = refineGoalOutcome(head, goal, null, "author", {outcome_id: "outcome-0", source});
  source.summary += "x".repeat(131072 - bytes(probe));
  const exact = refineGoalOutcome(head, goal, null, "author", {outcome_id: "outcome-0", source});
  assert.equal(bytes(exact), 131072);
  assert.throws(() => refineGoalOutcome(head, goal, null, "author", {
    outcome_id: "outcome-0", source: {...source, summary: source.summary + "😀"},
  }), error => {
    assert.ok(error instanceof GoalOutcomeValidationError);
    assert.match(error.message, /outcome declaration capacity exceeded: candidate 131076 UTF-8 bytes, retained 131000, limit 131072/);
    assert.match(error.message, /Shortening reviews cannot reduce this declaration budget/);
    assert.ok(!error.message.includes("Compact only proposed review"));
    return true;
  });
  assert.deepEqual(head, before);
});
