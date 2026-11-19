import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test, {type TestContext} from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {sqliteRuntimeIdentity} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {configureGoalAcceptance, inspectGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";

const goal = "acceptance-wire-recovery";
const qualified = sqliteRuntimeIdentity().sqlite_authority_qualified === true;
const providers = ["file", "sqlite"] as const;
function document(): JsonObject {
  return {scope: {kind: "all_advancement"}, objective: "Retain an exact lost-reply operation",
    non_goals: [], criteria: [{id: "check", description: "Owner check", validation_argv: ["node", "-e", "process.exit(0)"]}],
    bindings: []};
}
async function head(store: AuthorityStore) {
  const value = await store.loadAuthority();
  assert.equal(value.status, "loaded");
  if (value.status !== "loaded") throw new Error("fixture head unavailable");
  return value;
}
async function fixture(t: TestContext, provider: typeof providers[number]): Promise<AuthorityStore> {
  const dir = await mkdtemp(join(tmpdir(), "acceptance-wire-recovery-"));
  t.after(() => rm(dir, {recursive: true, force: true}));
  const store = provider === "file" ? new FileAuthorityStore(dir, goal) : new SqliteAuthorityStore(dir, goal);
  assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    next_projection: authorityProjectionFixture(goal, []), events: [], receipts: []})).status, "applied");
  return store;
}
async function request(store: AuthorityStore, disable: boolean): Promise<JsonObject> {
  return {goal_id: goal, actor_agent_id: null, operation_id: disable ? "legacy-clear" : "legacy-configure",
    expected_provider_revision: (await head(store)).provider_revision,
    document: disable ? null : document(), disable};
}
function currentWire(legacy: JsonObject): JsonObject {
  const value = {...legacy}; delete value.disable; return value;
}
function wrap(store: AuthorityStore, overrides: Partial<AuthorityStore>): AuthorityStore {
  return new Proxy(store, {get(target, key) {
    const value = Reflect.get(Object.hasOwn(overrides, key) ? overrides : target, key);
    return typeof value === "function" ? value.bind(target) : value;
  }});
}
async function retained(store: AuthorityStore, value: JsonObject) {
  assert.equal((await configureGoalAcceptance(store, value)).status, "applied");
  const receipt = await store.readReceipt(String(value.operation_id));
  assert.equal(receipt.status, "found");
  if (receipt.status !== "found") throw new Error("fixture receipt unavailable");
  // Independent old-wire digest oracle; no normalization of the supplied document.
  assert.equal(receipt.receipts[0].request_sha256, canonicalAuthoritySha256({kind: "configure", ...value}));
  return receipt;
}

for (const provider of providers) {
  const options = {skip: provider === "sqlite" && !qualified};
  for (const disable of [false, true]) {
    const action = disable ? "clear" : "configure";
    test(`${provider}: old ${action} receipt replays after the public wire omits disable`, options, async t => {
      const store = await fixture(t, provider), original = await request(store, disable);
      const receipt = await retained(store, original), before = await head(store);
      const wire = currentWire(original), untouched = structuredClone(wire);
      for (const dry_run of [false, true]) {
        const result = await configureGoalAcceptance(store, {...wire, dry_run});
        assert.equal(result.status, "replayed"); assert.equal(result.changed, false);
        assert.equal(result.operation_id, original.operation_id);
        assert.equal(result.provider_revision, receipt.provider_revision);
        assert.deepEqual(await head(store), before);
        assert.deepEqual(await store.readReceipt(String(original.operation_id)), receipt);
      }
      assert.deepEqual(wire, untouched);
    });
    test(`${provider}: old ${action} recovery rejects every changed identity field`, options, async t => {
      const store = await fixture(t, provider), original = await request(store, disable);
      await retained(store, original); const before = await head(store), wire = currentWire(original);
      const changes: JsonObject[] = [
        {expected_provider_revision: "other-revision"}, {goal_id: "different-goal"},
        {goal_ref: {goal_id: goal, goal_instance_id: `ginst_${"a".repeat(32)}`}},
        {document: disable ? document() : {...document(), objective: "Retain an exact lost-reply operation "}},
        {disable: !disable}, ...(!disable ? [{review_context_digest: "a".repeat(64)}] : []),
      ];
      for (const change of changes) {
        // disable:true with a document is an invalid request, not a replay candidate.
        if (change.disable === true && wire.document !== null) {
          await assert.rejects(configureGoalAcceptance(store, {...wire, ...change}), /legacy disable/);
        } else {
          assert.equal((await configureGoalAcceptance(store, {...wire, ...change})).reason_code,
            "coordination_operation_identity_mismatch");
        }
      }
      assert.deepEqual(await head(store), before);
    });
    test(`${provider}: old ${action} receipt appearing during observation still wins`, options, async t => {
      const store = await fixture(t, provider), original = await request(store, disable);
      await retained(store, original); const before = await head(store);
      let reads = 0, writes = 0;
      const raced = wrap(store, {
        readReceipt: async id => ++reads === 1 ? {status: "missing"} : store.readReceipt(id),
        commitAuthority: async value => {writes++; return store.commitAuthority(value);},
      });
      assert.equal((await configureGoalAcceptance(raced, currentWire(original))).status, "replayed");
      assert.equal(writes, 0); assert.deepEqual(await head(store), before);
    });
    test(`${provider}: concurrent old ${action} commit is recovered without a second write`, options, async t => {
      const store = await fixture(t, provider), original = await request(store, disable);
      let attempts = 0;
      const raced = wrap(store, {commitAuthority: async value => {
        attempts++;
        await retained(store, original);
        return store.commitAuthority(value);
      }});
      const result = await configureGoalAcceptance(raced, currentWire(original));
      assert.equal(result.status, "replayed"); assert.equal(result.changed, false);
      assert.equal(attempts, 1);
      const receipt = await store.readReceipt(String(original.operation_id));
      assert.equal(receipt.status, "found");
      if (receipt.status === "found") {
        assert.equal((await head(store)).provider_revision, receipt.provider_revision);
        assert.equal(receipt.receipts[0].request_sha256, canonicalAuthoritySha256({kind: "configure", ...original}));
      }
    });
  }
  test(`${provider}: null document with the old default disable:false also recovers`, options, async t => {
    const store = await fixture(t, provider), original: JsonObject = {...await request(store, true), disable: false};
    const receipt = await retained(store, original), before = await head(store);
    const result = await configureGoalAcceptance(store, currentWire(original));
    assert.equal(result.status, "replayed"); assert.equal(result.changed, false);
    assert.deepEqual(await head(store), before);
    assert.deepEqual(await store.readReceipt(String(original.operation_id)), receipt);
  });
  test(`${provider}: new wire identities and same-request replay remain unchanged`, options, async t => {
    const store = await fixture(t, provider), wire = currentWire(await request(store, false));
    const receipt = await retained(store, wire), before = await head(store);
    assert.equal((await configureGoalAcceptance(store, wire)).status, "replayed");
    assert.deepEqual(await head(store), before);
    assert.deepEqual(await store.readReceipt(String(wire.operation_id)), receipt);
    assert.equal(Object.hasOwn(before.head.goal_acceptance as JsonObject, "enabled"), false);
  });
  test(`${provider}: historical recovery does not restore an older configuration`, options, async t => {
    const store = await fixture(t, provider), original = await request(store, false);
    const receipt = await retained(store, original);
    const newer = {...currentWire(await request(store, false)), operation_id: "newer",
      document: {...document(), objective: "Newer owner configuration"}};
    await retained(store, newer); const before = await head(store);
    const result = await configureGoalAcceptance(store, currentWire(original));
    assert.equal(result.status, "replayed");
    assert.equal((result.goal_acceptance_contract as JsonObject).objective, "Newer owner configuration");
    assert.deepEqual(await head(store), before);
    assert.deepEqual(await store.readReceipt(String(original.operation_id)), receipt);
  });
  test(`${provider}: missing compatibility receipt never turns recovery into a write`, options, async t => {
    const store = await fixture(t, provider), original = await request(store, false);
    await retained(store, original); const before = await head(store);
    let reads = 0, writes = 0;
    const raced = wrap(store, {
      readReceipt: async id => ++reads === 1 ? store.readReceipt(id) : {status: "missing"},
      commitAuthority: async value => {writes++; return store.commitAuthority(value);},
    });
    assert.equal((await configureGoalAcceptance(raced, currentWire(original))).reason_code,
      "coordination_operation_identity_mismatch");
    assert.equal(writes, 0); assert.deepEqual(await head(store), before);
  });
  test(`${provider}: malformed or multiple historical receipts remain rejected`, options, async t => {
    const store = await fixture(t, provider), original = await request(store, false);
    const old = await retained(store, original), before = await head(store);
    for (const receipts of [[{...old.receipts[0], schema_version: "unrelated_operation"}],
      [old.receipts[0], old.receipts[0]], [{...old.receipts[0], result: null}]]) {
      const corrupt = wrap(store, {readReceipt: async () => ({...old, receipts})});
      assert.equal((await configureGoalAcceptance(corrupt, currentWire(original))).status, "failed");
    }
    assert.deepEqual(await head(store), before);
  });
  test(`${provider}: owner authorization is still checked before historical replay`, options, async t => {
    const store = await fixture(t, provider), original = await request(store, false);
    await retained(store, original); const before = await head(store);
    await assert.rejects(configureGoalAcceptance(store, {...currentWire(original), actor_agent_id: "agent-a"}), /trusted owner/);
    assert.deepEqual(await head(store), before);
  });
  test(`${provider}: no receipt keeps ordinary CAS and scope admission`, options, async t => {
    const store = await fixture(t, provider), wire = currentWire(await request(store, false));
    const before = await head(store), doc = document(); delete doc.scope;
    assert.equal((await configureGoalAcceptance(store, {...wire, document: doc})).reason_code,
      "goal_acceptance_scope_required");
    assert.equal((await configureGoalAcceptance(store, {...wire, expected_provider_revision: "stale"})).reason_code,
      "goal_acceptance_provider_revision_mismatch");
    assert.deepEqual(await head(store), before);
    assert.equal((await inspectGoalAcceptance(store, goal)).contract, null);
  });
}
