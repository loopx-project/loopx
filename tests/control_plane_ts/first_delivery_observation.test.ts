import assert from "node:assert/strict";
import fs from "node:fs";
import fsp from "node:fs/promises";
import {createHash} from "node:crypto";
import {syncBuiltinESMExports} from "node:module";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";
import test from "node:test";
import {settlementIdentityPayload, type JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} from "../../loopx/control_plane/effect_runtime_handlers.ts";

const goal = "observation-goal", agent = "observation-agent";
async function fixture(count: number, loss = "intact", pending = false) {
  const root = await fsp.mkdtemp(join(tmpdir(), "loopx-delivery-observation-"));
  const runs = join(root, "goals", goal, "runs"), contexts = join(root, "goals", goal, "checkpoint-contexts");
  await fsp.mkdir(runs, {recursive: true}); await fsp.mkdir(contexts, {recursive: true});
  const rows: JsonObject[] = [], events: JsonObject[] = [], todos: JsonObject[] = [], identities: JsonObject[] = [];
  for (let i = 0; i < count; i++) {
    const identity = settlementIdentityPayload({goal_id: goal, agent_id: agent, todo_id: `todo_work_${i}`, turn_instance_id: `turn-${i}`});
    identities.push(identity);
    const common = {goal_id: goal, agent_id: agent, todo_id: identity.todo_id, turn_instance_id: identity.turn_instance_id,
      settlement_identity: identity};
    const row = {...common, classification: "state_refreshed", delivery_outcome: "outcome_progress",
      refresh_recovery: null,
      vision_checkpoint: {required: true, satisfied: true, read_context: {purpose: "first_delivery"}},
      json_path: join(runs, `run-${i}.json`), markdown_path: join(runs, `run-${i}.md`)};
    rows.push(row);
    await fsp.writeFile(row.json_path, JSON.stringify(row)); await fsp.writeFile(row.markdown_path, "Verified direction\n");
    const name = createHash("sha256").update(String(identity.effect_id)).digest("hex") + ".json";
    if (loss !== "deleted") await fsp.writeFile(join(contexts, name), loss === "damaged" ? "{" : JSON.stringify({
      schema_version: "checkpoint_read_context_v2", purpose: "first_delivery", identity}));
    for (const kind of ["quota_should_run", "refresh_state", ...(pending && i === 0 ? [] : ["quota_spend"])]) {
      events.push({schema_version: "loopx_rollout_event_v0", event_id: `${kind}-${i}`, event_kind: kind,
        goal_id: goal, agent_id: agent, run_id: identity.turn_instance_id,
        details: {todo_id: identity.todo_id, settlement_effect_id: identity.effect_id}});
    }
    if (!(pending && i === 0)) rows.push({...common, classification: "quota_slot_spent"});
    todos.push({todo_id: identity.todo_id, status: "done", completion_turn_key: identity.effect_id});
  }
  const index = join(runs, "index.jsonl"), log = join(root, "goals", goal, "rollout-event-log.jsonl");
  await fsp.writeFile(index, rows.map(row => JSON.stringify(row) + "\n").join(""));
  await fsp.writeFile(log, events.map(row => JSON.stringify(row) + "\n").join(""));
  return {root, index, log, rows, identities, request: {runtime_root: root, goal_id: goal, agent_id: agent, todos}};
}

const observe = (request: JsonObject) => dispatchEffectRuntimeMethod(createEffectRuntimeHandlers(), "turn.first_delivery.observe", request);

for (const loss of ["intact", "damaged", "deleted"]) for (const count of [0, 1, 100, 1000]) {
  test(`one batch bounds source reads across ${count} ${loss} historical Turns`, async t => {
    const f = await fixture(count, loss);
    const syncRead = fs.readFileSync, asyncRead = fsp.readFile;
    const reads = {index: 0, rollout: 0};
    const record = (path: unknown) => {
      if (resolve(String(path)) === resolve(f.index)) reads.index++;
      if (resolve(String(path)) === resolve(f.log)) reads.rollout++;
    };
    const a = t.mock.method(fs, "readFileSync", (...args: Parameters<typeof fs.readFileSync>) => {record(args[0]); return syncRead(...args);});
    const b = t.mock.method(fsp, "readFile", (...args: Parameters<typeof fsp.readFile>) => {record(args[0]); return asyncRead(...args);});
    syncBuiltinESMExports();
    try {
      assert.equal(await observe(f.request), null);
      assert.equal(reads.index, count ? 2 : 1, "strict proof and settlement each capture the full index once");
      assert.equal(reads.rollout, count ? 1 : 0);
    } finally {a.mock.restore(); b.mock.restore(); syncBuiltinESMExports(); await fsp.rm(f.root, {recursive: true, force: true});}
  });
}

for (const loss of ["intact", "damaged", "deleted"]) test(`old pending accounting survives newer settled ${loss} history`, async () => {
  const f = await fixture(100, loss, true);
  try {
    const progress = await observe(f.request) as JsonObject;
    assert.equal(progress.stage, "settlement_pending");
    assert.deepEqual(progress.settlement_identity, f.identities[0]);
    assert.equal(progress.quota_spent, false);
    assert.equal(progress.result_committed, true);
    assert.equal(await observe({...f.request, agent_id: "independent-peer"}), null);
    assert.deepEqual(await observe(f.request), progress, "readback cannot repair or debit the original Turn");
  } finally {await fsp.rm(f.root, {recursive: true, force: true});}
});

for (const fault of ["tail", "conflicting-row", "artifact", "changed-index"]) test(`unproved ${fault} history stays uncertain`, async t => {
  const f = await fixture(3, "damaged");
  const before = fs.readFileSync(f.index, "utf8");
  let hook: ReturnType<typeof t.mock.method> | undefined;
  try {
    if (fault === "tail") await fsp.writeFile(f.index, before.trimEnd());
    if (fault === "conflicting-row") await fsp.appendFile(f.index, JSON.stringify({...f.rows[0], json_path: join(f.root, "other.json")}) + "\n");
    if (fault === "artifact") await fsp.writeFile(String(f.rows[0].json_path), JSON.stringify({...f.rows[0], settlement_identity: f.identities[1]}));
    if (fault === "changed-index") {
      const read = fsp.readFile;
      hook = t.mock.method(fsp, "readFile", async (...args: Parameters<typeof fsp.readFile>) => {
        if (resolve(String(args[0])) === resolve(f.index)) await fsp.appendFile(f.index, JSON.stringify({classification: "state_refreshed"}) + "\n");
        return read(...args);
      }); syncBuiltinESMExports();
    }
    const observed = await observe(f.request) as JsonObject;
    assert.ok(["observation_unavailable", "operation_unknown"].includes(String(observed.stage)), JSON.stringify(observed));
    assert.equal(observed.goal_completion_certified, false);
  } finally {hook?.mock.restore(); syncBuiltinESMExports(); await fsp.rm(f.root, {recursive: true, force: true});}
});

test("batch observation cannot invent or partially supply exact Goal admission", async () => {
  const f = await fixture(1);
  try {
    await assert.rejects(observe({...f.request, goal_ref: {goal_id: goal, goal_instance_id: "ginst_" + "a".repeat(32)}}), /supplied together/);
  } finally {await fsp.rm(f.root, {recursive: true, force: true});}
});

test("the caller's historical scan order selects between pending original Turns", async () => {
  const f = await fixture(2, "intact", true);
  try {
    await fsp.writeFile(f.index, f.rows.filter(row => !(row.classification === "quota_slot_spent" &&
      row.turn_instance_id === "turn-1")).map(row => JSON.stringify(row) + "\n").join(""));
    const events = fs.readFileSync(f.log, "utf8").trim().split("\n").map(line => JSON.parse(line));
    await fsp.writeFile(f.log, events.filter(row => !(row.event_kind === "quota_spend" && row.run_id === "turn-1"))
      .map(row => JSON.stringify(row) + "\n").join(""));
    const names = f.identities.map(identity => createHash("sha256").update(String(identity.effect_id)).digest("hex") + ".json");
    assert.deepEqual((await observe({...f.request, receipt_names: names}) as JsonObject).settlement_identity, f.identities[1]);
    assert.deepEqual((await observe({...f.request, receipt_names: names.reverse()}) as JsonObject).settlement_identity, f.identities[0]);
  } finally {await fsp.rm(f.root, {recursive: true, force: true});}
});

test("an unassignable malformed receipt name is scoped unavailable evidence", async () => {
  const f = await fixture(1);
  try {
    const path = join(f.root, "goals", goal, "checkpoint-contexts", "unassignable.json");
    await fsp.writeFile(path, "{");
    const observed = await observe(f.request) as JsonObject;
    assert.equal(observed.stage, "observation_unavailable");
    assert.equal(observed.settlement_identity, undefined);
    assert.equal(fs.readFileSync(path, "utf8"), "{");
  } finally {await fsp.rm(f.root, {recursive: true, force: true});}
});

test("the existing identity owner rejects forged effects and retains opaque historical facts", async () => {
  const f = await fixture(1, "intact", true);
  try {
    const name = createHash("sha256").update(String(f.identities[0].effect_id)).digest("hex") + ".json";
    const path = join(f.root, "goals", goal, "checkpoint-contexts", name);
    const receipt = JSON.parse(fs.readFileSync(path, "utf8"));
    receipt.identity = {...receipt.identity, historical_note: "retained opaque fact"};
    await fsp.writeFile(path, JSON.stringify(receipt));
    const observed = await observe(f.request) as JsonObject;
    assert.deepEqual(observed.settlement_identity, receipt.identity);
    receipt.identity.effect_id = "forged-effect";
    await fsp.writeFile(path, JSON.stringify(receipt));
    const rejected = await observe(f.request) as JsonObject;
    assert.equal(rejected.stage, "observation_unavailable");
    assert.equal(rejected.settlement_identity, undefined);
  } finally {await fsp.rm(f.root, {recursive: true, force: true});}
});
