import assert from "node:assert/strict";
import test from "node:test";
import {randomUUID} from "node:crypto";
import {Pool} from "pg";
import {PostgreSqlAuthorityStore, installPostgreSqlAuthorityStoreSchema} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {projectGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_contract.ts";
import {inspectGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {refineGoalOutcome, outcomeContextNavigation} from "../../loopx/control_plane/goals/outcome_requirements.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";

const goal = "navigation-goal", agent = "author", todo = "todo_selected";

function fixture(extra: JsonObject = {}, sharedSource = false) {
  let head = authorityProjectionFixture(goal, [
    {todo_id: todo, role: "agent", status: "open", done: false, text: "Selected task tail: preserve cancellation.",
      task_class: "advancement_task", claimed_by: agent, archive_state: "active", ...extra},
    {todo_id: "todo_peer", role: "agent", status: "open", done: false, text: "Independent peer task.",
      task_class: "advancement_task", claimed_by: "peer", archive_state: "active"},
  ]);
  for (const [owner, work] of [[agent, todo], ["peer", "todo_peer"]]) {
    head = {...head, goal_outcomes: refineGoalOutcome(head, goal, null, owner, {
      outcome_id: owner, owner_agent_id: owner, objective: `Preserve ${owner} requirements.`, todo_ids: [work],
      requirements: Array.from({length: 12}, (_, i) => ({id: `r${i}`,
        description: `${owner} requirement ${i}: ` + "Preserve exact behavior and evidence. ".repeat(8).trim()})),
      source: {ref: sharedSource ? "message:shared" : `message:${owner}`, summary: "Original explicit requirement."},
    })};
  }
  return head;
}

function navigation(head = fixture(), change?: (acceptance: JsonObject) => void) {
  const acceptance = projectGoalAcceptance(head, goal, [agent, "peer"]);
  change?.(acceptance);
  const before = structuredClone({head, acceptance});
  const result = outcomeContextNavigation(head, goal, agent, todo, acceptance)!;
  assert.deepEqual({head, acceptance}, before);
  assert.equal(result.acceptance_digest, canonicalAuthoritySha256(acceptance));
  return result;
}

test("independent work remains available; suggestions do not impose a read gate", () => {
  const result = navigation();
  assert.equal(result.coverage_complete, true);
  assert.deepEqual(result.suggested_outcome_ids, [agent]);
  assert.equal(result.required_outcome_ids, undefined);
  assert.match(String(result.instruction), /not a reading gate/);
});

for (const [name, extra, shared] of [
  ["successor dependency", {successor_todo_ids: ["todo_peer"]}, false],
  ["unblocks dependency", {unblocks_todo_id: "todo_peer"}, false],
  ["shared source", {}, true],
] as const) test(`${name} prioritizes the related peer`, () => {
  assert.deepEqual(navigation(fixture(extra, shared)).suggested_outcome_ids, [agent, "peer"]);
});

for (const status of ["failed", "stale"]) test(`${status} peer evidence draws attention across ownership`, () => {
  const result = navigation(fixture(), acceptance => {
    acceptance.outcome_requirements.outcomes[1].requirements[0].status = status;
  });
  assert.deepEqual(result.suggested_outcome_ids, [agent, "peer"]);
});

test("ownership alone does not make every past outcome relevant", () => {
  const head = fixture();
  head.goal_outcomes.outcomes[1].owner_agent_id = agent;
  assert.deepEqual(navigation(head).suggested_outcome_ids, [agent]);
});

test("shared review evidence joins tasks even with independent original sources", () => {
  const result = navigation(fixture(), acceptance => {
    for (const row of acceptance.outcome_requirements.outcomes)
      row.requirements[0].agent_review = {evidence_refs: ["evidence:shared"]};
  });
  assert.deepEqual(result.suggested_outcome_ids, [agent, "peer"]);
});

for (const kind of ["missing_relation", "unbound_work", "goal_wide", "lifetime"])
  test(`${kind} exposes incomplete coverage without deleting or mandating bodies`, () => {
    const head = fixture(kind === "missing_relation" ? {successor_todo_ids: ["todo_missing"]} : {});
    const result = navigation(head, acceptance => {
      if (kind === "unbound_work") acceptance.outcome_requirements.outcomes[0].todo_ids = [];
      if (kind === "goal_wide") { acceptance.criteria = [{id: "global"}]; acceptance.scope = {kind: "all_advancement"}; }
      if (kind === "lifetime") acceptance.outcome_requirements.current_lifetime = false;
    });
    assert.equal(result.coverage_complete, false);
    assert.ok(result.limitations.length);
    assert.deepEqual(result.suggested_outcome_ids, [agent, "peer"]);
    assert.match(String(result.instruction), /not an automatic full-file read/);
  });

for (const provider of ["file", "sqlite", "postgresql"]) test(`${provider}: inspect derives navigation from one actual canonical snapshot without mutations`,
  {skip: provider === "postgresql" && !process.env.LOOPX_TEST_POSTGRES_URL}, async t => {
  const root = await mkdtemp(join(tmpdir(), "scope-provider-")); t.after(() => rm(root, {recursive: true, force: true}));
  let store;
  if (provider === "postgresql") {
    const pool = new Pool({connectionString: process.env.LOOPX_TEST_POSTGRES_URL, max: 4});
    const db = {connect: async () => {
      const client = await pool.connect();
      return {query: async (sql: string, values?: readonly unknown[]) => client.query(sql, values ? [...values] : undefined),
        release: () => client.release()};
    }};
    const tenant = `outcome-navigation-${randomUUID()}`;
    t.after(async () => {
      try {
        for (const table of ["authority_commits", "authority_heads"])
          await pool.query(`DELETE FROM loopx_control_plane.${table} WHERE tenant_id=$1 AND goal_id=$2`, [tenant, goal]);
      } finally { await pool.end(); }
    });
    await installPostgreSqlAuthorityStoreSchema(db, `postgresql:${"b".repeat(32)}`);
    store = new PostgreSqlAuthorityStore(db, {tenant_id: tenant, goal_id: goal});
  } else store = provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  const seeded = await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    next_projection: fixture(), events: [], receipts: []});
  assert.equal(seeded.status, "applied", JSON.stringify(seeded));
  const before = await store.loadAuthority(); let reads = 0; const load = store.loadAuthority.bind(store);
  store.loadAuthority = async () => {reads++; return load();};
  const scoped = await inspectGoalAcceptance(store, goal, todo, undefined, false, undefined, [agent, "peer"], agent);
  assert.equal(reads, 1);
  assert.equal(scoped.outcome_context.coverage_complete, true);
  assert.deepEqual(scoped.outcome_context.suggested_outcome_ids, [agent]);
  assert.equal(scoped.outcome_context.acceptance_digest, canonicalAuthoritySha256(scoped.goal_acceptance_contract));
  const off = await inspectGoalAcceptance(store, goal, todo, undefined, false, undefined, [agent, "peer"]);
  assert.equal(off.outcome_context, undefined);
  assert.deepEqual(off.goal_acceptance_contract, scoped.goal_acceptance_contract);
  assert.deepEqual(await store.loadAuthority(), before);
});
