import {inspectCheckpointAttempt, inspectCheckpointReadHistory} from "../../loopx/control_plane/goals/checkpoint_commit.ts";
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdir, mkdtemp, rm} from "node:fs/promises";
import {writeFileSync, readFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {sqliteRuntimeIdentity} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";

const sqliteSkip = sqliteRuntimeIdentity().sqlite_authority_qualified
  ? false : "requires the qualified SQLite runtime";

for (const [name, Store] of [["File", FileAuthorityStore], ["SQLite", SqliteAuthorityStore]] as const) {
  test(`${name} checkpoint fence releases on failure without advancing provider revision`,
    {skip: name === "SQLite" && sqliteSkip}, async t => {
    const directory = await mkdtemp(join(tmpdir(), "checkpoint-fence-"));
    t.after(() => rm(directory, {recursive: true, force: true}));
    const store = new Store(directory, "goal");
    const created = await store.commitAuthority({expected_provider_revision: null, operation_id: "seed",
      next_projection: {value: 1}, events: [], receipts: []});
    assert.equal(created.status, "applied");
    const before = await store.loadAuthority();
    await assert.rejects(store.withCheckpointHead(() => {throw new Error("save failed");}), /save failed/);
    assert.deepEqual(await store.loadAuthority(), before);
    assert.equal(before.status, "loaded");
    if (before.status !== "loaded") return;
    const updated = await store.commitAuthority({expected_provider_revision: before.provider_revision,
      operation_id: "next", next_projection: {value: 2}, events: [], receipts: []});
    assert.equal(updated.status, "applied");
  });
}

test("SQLite concurrent requests in one runtime do not busy-wait on an awaiting checkpoint holder",
  {skip: sqliteSkip}, async t => {
  const directory = await mkdtemp(join(tmpdir(), "checkpoint-sqlite-loop-"));
  t.after(() => rm(directory, {recursive: true, force: true}));
  const a = new SqliteAuthorityStore(directory, "goal");
  const b = new SqliteAuthorityStore(directory, "goal");
  const other = new SqliteAuthorityStore(directory, "other-goal");
  await a.commitAuthority({expected_provider_revision: null, operation_id: "seed",
    next_projection: {value: 1}, events: [], receipts: []});
  let competing: Promise<unknown> | undefined;
  const order: string[] = [];
  const started = performance.now();
  const result = await a.withCheckpointHead(head => {
    // Queue the independent request at the final read. It must start after the
    // synchronous append and rollback, rather than block an awaiting holder.
    competing = Promise.resolve().then(async () => {
      order.push("writer");
      return await b.commitAuthority({expected_provider_revision: head.provider_revision,
        operation_id: "peer", next_projection: {value: 2}, events: [], receipts: []});
    });
    writeFileSync(join(directory, "checkpoint.json"), JSON.stringify(head));
    order.push("saved");
    return {ok: true};
  });
  const [updated, independent] = await Promise.all([competing,
    other.commitAuthority({expected_provider_revision: null, operation_id: "other",
      next_projection: {value: 3}, events: [], receipts: []})]);
  assert.equal(result.ok, true);
  assert.equal((updated as {status: string}).status, "applied");
  assert.equal(independent.status, "applied");
  assert.deepEqual(order, ["saved", "writer"]);
  assert.equal(JSON.parse(readFileSync(join(directory, "checkpoint.json"), "utf8")).head.value, 1);
  assert.ok(performance.now() - started < 4000, "must not incur the SQLite 5000ms busy timeout");
});


for (const work of [true, false]) {
  test(`checkpoint rejects competing ${work ? "work" : "direction"} proofs on one Turn`, async t => {
    const root = await mkdtemp(join(tmpdir(), "checkpoint-conflict-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const runs = join(root, "goals", "goal", "runs");
    await mkdir(runs, {recursive: true});
    const identity = {goal_id: "goal", agent_id: "agent", todo_id: "todo", turn_instance_id: "turn", effect_id: "effect"};
    const rows = ["read-one", "read-two"].map(read_context_id => ({settlement_identity: identity,
      refresh_recovery: {}, json_path: join(runs, "result.json"), markdown_path: join(runs, "result.md"),
      vision_checkpoint: work ? {decision: "missing_required", satisfied: false} :
        {satisfied: true, read_context: {read_context_id}},
      ...(work ? {result_work_context: {read_context_id}} : {})}));
    writeFileSync(join(runs, "index.jsonl"), JSON.stringify(rows[0]) + "\n");
    assert.equal(inspectCheckpointAttempt({runtime_root: root, identity: {...identity, effect_id: "another-turn"}}).status, "absent");
    writeFileSync(join(runs, "index.jsonl"), rows.map(row => JSON.stringify(row)).join("\n") + "\n");
    assert.throws(() => inspectCheckpointAttempt({runtime_root: root, identity,
      purpose: work ? "delivery_result" : "first_delivery"}), (error: unknown) => (error as {code: string}).code === "checkpoint_commit_unknown");
  });
}

for (const readWork of [false, true]) {
  for (const damage of ["none", "missing-json", "mismatched-json", "missing-markdown", "torn-index", "no-proof", "work-only-proof", "invalid-work-proof"] as const) {
    test(`obsolete ${readWork ? "work" : "direction"} receipt needs a verified matching indexed effect: ${damage}`, async t => {
      const root = await mkdtemp(join(tmpdir(), "checkpoint-old-read-"));
      t.after(() => rm(root, {recursive: true, force: true}));
      const runs = join(root, "goals", "goal", "runs");
      const contexts = join(root, "goals", "goal", "checkpoint-contexts");
      await mkdir(runs, {recursive: true});
      await mkdir(contexts, {recursive: true});
      const old = {goal_id: "goal", agent_id: "peer", todo_id: "old-task", turn_instance_id: "old-turn", effect_id: "old-effect"};
      const current = {goal_id: "goal", agent_id: "agent", todo_id: "new-task", turn_instance_id: "new-turn", effect_id: "new-effect"};
      const jsonPath = join(runs, "old.json");
      const markdownPath = join(runs, "old.md");
      const row = {settlement_identity: old, refresh_recovery: {}, json_path: jsonPath, markdown_path: markdownPath,
        ...(damage === "invalid-work-proof" ? {result_work_context: false} :
          damage === "work-only-proof" ? {result_work_context: {read_context_id: "old-work"}} :
          damage === "no-proof" ? {classification: "quota_slot_spent"} :
          {vision_checkpoint: {satisfied: true, read_context: {read_context_id: "old-direction"}}})};
      if (damage !== "missing-json") writeFileSync(jsonPath, JSON.stringify(damage === "mismatched-json" ? {...row, settlement_identity: current} : row));
      if (damage !== "missing-markdown") writeFileSync(markdownPath, "Retained checkpoint\n");
      writeFileSync(join(runs, "index.jsonl"), JSON.stringify(row) + (damage === "torn-index" ? "" : "\n"));
      const name = createHash("sha256").update(old.effect_id + (readWork ? ":delivery_result" : "")).digest("hex") + ".json";
      writeFileSync(join(contexts, name), "{");
      const inspect = () => inspectCheckpointAttempt({runtime_root: root, identity: current,
        purpose: "first_delivery", check_other_attempts: true});
      const observe = () => inspectCheckpointReadHistory({runtime_root: root, goal_id: "goal", receipt_names: [name]});
      if (damage === "none" || damage === "work-only-proof" && readWork) {
        assert.deepEqual(observe().verified, [{receipt_name: name, identity: old,
          purpose: damage === "work-only-proof" ? "delivery_result" : "first_delivery"}]);
        assert.equal(inspect().status, "absent");
        assert.equal(readFileSync(join(contexts, name), "utf8"), "{", "Inspection must not repair another identity's read");
        // An unattributable or current receipt remains unknown, even beside a verified old effect.
        for (const effect of [current.effect_id, "unattributed-effect"]) {
          const unknownPath = join(contexts, createHash("sha256").update(effect).digest("hex") + ".json");
          writeFileSync(unknownPath, "{");
          assert.throws(inspect, (error: unknown) => (error as {code: string}).code === "checkpoint_commit_unknown");
          await rm(unknownPath);
        }
      } else {
        assert.throws(inspect, (error: unknown) => (error as {code: string}).code === "checkpoint_commit_unknown");
        if (damage === "torn-index") assert.throws(observe);
        else assert.deepEqual(observe().verified, []);
      }
    });
  }
}

test("history inspection cannot borrow a valid checkpoint from another Goal", async t => {
  const root = await mkdtemp(join(tmpdir(), "checkpoint-history-goal-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const runs = join(root, "goals", "other-goal", "runs");
  const currentRuns = join(root, "goals", "goal", "runs");
  const contexts = join(root, "goals", "goal", "checkpoint-contexts");
  for (const path of [runs, currentRuns, contexts]) await mkdir(path, {recursive: true});
  const identity = {goal_id: "other-goal", agent_id: "peer", todo_id: "task", turn_instance_id: "turn", effect_id: "effect"};
  const row = {settlement_identity: identity, refresh_recovery: {},
    vision_checkpoint: {satisfied: true, read_context: {read_context_id: "read"}},
    json_path: join(runs, "saved.json"), markdown_path: join(runs, "saved.md")};
  writeFileSync(row.json_path, JSON.stringify(row));
  writeFileSync(row.markdown_path, "Saved checkpoint\n");
  for (const path of [runs, currentRuns]) writeFileSync(join(path, "index.jsonl"), JSON.stringify(row) + "\n");
  const name = createHash("sha256").update(identity.effect_id).digest("hex") + ".json";
  writeFileSync(join(contexts, name), "{");
  assert.equal(inspectCheckpointAttempt({runtime_root: root, identity}).status, "committed");
  assert.deepEqual(inspectCheckpointReadHistory({runtime_root: root, goal_id: "goal", receipt_names: [name]}).verified, []);
  assert.throws(() => inspectCheckpointAttempt({runtime_root: root,
    identity: {...identity, goal_id: "goal", effect_id: "new-effect"}, check_other_attempts: true}),
    (error: unknown) => (error as {code: string}).code === "checkpoint_commit_unknown");
});

test("an indexed work result cannot discharge another Turn's unindexed direction", async t => {
  const root = await mkdtemp(join(tmpdir(), "checkpoint-direction-proof-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const runs = join(root, "goals", "goal", "runs");
  const contexts = join(root, "goals", "goal", "checkpoint-contexts");
  await mkdir(runs, {recursive: true});
  await mkdir(contexts, {recursive: true});
  const old = {goal_id: "goal", agent_id: "agent", todo_id: "task", turn_instance_id: "old", effect_id: "old-effect"};
  const current = {...old, turn_instance_id: "new", effect_id: "new-effect"};
  const work = {settlement_identity: old, refresh_recovery: {}, result_work_context: {read_context_id: "work"},
    json_path: join(runs, "work.json"), markdown_path: join(runs, "work.md")};
  const direction = {settlement_identity: old, refresh_recovery: {}, vision_checkpoint: {satisfied: true,
    read_context: {read_context_id: "direction"}}, json_path: join(runs, "direction.json"), markdown_path: join(runs, "direction.md")};
  writeFileSync(work.json_path, JSON.stringify(work));
  writeFileSync(work.markdown_path, "Committed work\n");
  writeFileSync(direction.json_path, JSON.stringify(direction));
  writeFileSync(direction.markdown_path, "Retained direction bytes\n");
  writeFileSync(join(contexts, createHash("sha256").update(old.effect_id).digest("hex") + ".json"), JSON.stringify({
    identity: old, purpose: "first_delivery", commit_attempt: {...direction, index_record: direction}}));
  const inspect = () => inspectCheckpointAttempt({runtime_root: root, identity: current, check_other_attempts: true});
  writeFileSync(join(runs, "index.jsonl"), JSON.stringify(work) + "\n");
  assert.throws(inspect, (error: unknown) => (error as {code: string}).code === "checkpoint_commit_unknown");
  writeFileSync(join(runs, "index.jsonl"), [work, direction].map(row => JSON.stringify(row)).join("\n") + "\n");
  assert.equal(inspect().status, "absent");
  writeFileSync(direction.json_path, JSON.stringify({...direction, settlement_identity: current}));
  assert.throws(inspect, (error: unknown) => (error as {code: string}).code === "checkpoint_commit_unknown");
});
