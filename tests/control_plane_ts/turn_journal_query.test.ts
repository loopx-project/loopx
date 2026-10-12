import assert from "node:assert/strict";
import { mkdtemp, mkdir, readdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { type TestContext } from "node:test";

import { EffectRuntimeConflictError, EffectRuntimeRequestError } from "../../loopx/control_plane/effect_runtime_errors.ts";
import { commitTurnJournal } from "../../loopx/control_plane/turn_driver/turn_journal_effects.ts";
import { findTurnJournalBySettlement, readTurnJournalCapabilities } from "../../loopx/control_plane/turn_driver/turn_journal_query.ts";

const phases = ["host_execute", "typed_result", "validation", "durable_writeback", "quota_spend", "scheduler_apply", "scheduler_ack"];
const selector = { goal_id: "goal", agent_id: "actor", todo_id: "todo_task", turn_instance_id: "turn-7" };
const identity = { schema_version: "quota_settlement_identity_v0", ...selector, effect_id: "goal:actor:todo_task:turn-7" };
const goalRefA = { goal_id: "goal", goal_instance_id: `ginst_${"a".repeat(32)}` };
const goalRefB = { goal_id: "goal", goal_instance_id: `ginst_${"b".repeat(32)}` };
const key = `sha256:${"a".repeat(64)}`;
function journal(goalRef?: { goal_id: string; goal_instance_id: string }) {
  return {
    schema_version: "loopx_turn_journal_v0", goal_id: "goal", turn_key: key,
    status: "committed", completed_phases: [...phases],
    plan: {
      ...(goalRef ? { goal_ref: goalRef } : {}),
      turn_envelope: {
        goal_id: "goal", agent_id: "actor", action: { selected_todo: { todo_id: "todo_task" } },
        boundary: { available_capabilities: ["network"] },
      },
      transaction: { turn_key: key, turn_instance_id: "turn-7", ...(goalRef ? { goal_ref: goalRef } : {}), settlement_plan: {
        schema_version: "quota_settlement_plan_v1", identity: { ...identity },
      } },
    },
  };
}

async function fixture(t: TestContext) {
  const root = await mkdtemp(join(tmpdir(), "loopx-journal-query-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const directory = join(root, "goals", "goal", "turns");
  await mkdir(directory, { recursive: true });
  return { root, directory, path: join(directory, `${"a".repeat(64)}.json`) };
}

const lookup = (runtime_root: string) => findTurnJournalBySettlement({ runtime_root, ...selector });
const capabilities = (
  runtime_root: string,
  settlement_identity: unknown = identity,
  goal_ref?: unknown,
) =>
  readTurnJournalCapabilities({
    runtime_root,
    settlement_identity,
    ...(goal_ref === undefined ? {} : { goal_ref }),
  });

test("missing history is a read-only observation", async t => {
  const { root, directory } = await fixture(t);
  assert.deepEqual(await lookup(root), { turn_key: null });
  assert.deepEqual(await capabilities(root), { observed_capabilities: null });
  assert.deepEqual(await readdir(directory), []);
  await rm(directory, { recursive: true });
  assert.deepEqual(await lookup(root), { turn_key: null });
  await assert.rejects(readdir(directory), { code: "ENOENT" });
});

test("real writer prefixes remain discoverable; only terminal history lends capabilities", async t => {
  const { root, path, directory } = await fixture(t);
  for (const count of [0, 2, 3, 4, 5, 7]) {
    const snapshot = journal();
    snapshot.status = count === 7 ? "committed" : "in_progress";
    snapshot.completed_phases = phases.slice(0, count);
    await commitTurnJournal({ path, journal: snapshot, expected_effect_id: identity.effect_id });
    const before = await readFile(path);
    const files = await readdir(directory);
    assert.deepEqual(await lookup(root), { turn_key: key });
    assert.deepEqual(await capabilities(root), { observed_capabilities: count === 7 ? ["network"] : null });
    assert.deepEqual(await readFile(path), before);
    assert.deepEqual(await readdir(directory), files);
  }
});

test("capability evidence is bound to the current exact GoalRef", async t => {
  const { root, path } = await fixture(t);
  await writeFile(path, JSON.stringify(journal(goalRefA)));

  assert.deepEqual(await capabilities(root, identity, goalRefA), {
    observed_capabilities: ["network"],
  });
  assert.deepEqual(await capabilities(root, identity, goalRefB), {
    observed_capabilities: null,
  });
  assert.deepEqual(await capabilities(root), {
    observed_capabilities: ["network"],
  });

  await writeFile(path, JSON.stringify(journal()));
  assert.deepEqual(await capabilities(root, identity, goalRefA), {
    observed_capabilities: null,
  });
  assert.deepEqual(await capabilities(root), {
    observed_capabilities: ["network"],
  });
});

test("capability query rejects malformed or contradictory GoalRefs", async t => {
  const { root } = await fixture(t);
  for (const goalRef of [
    { goal_id: "goal" },
    { goal_id: "goal", goal_instance_id: "ginst_invalid" },
    { goal_id: "other-goal", goal_instance_id: `ginst_${"c".repeat(32)}` },
  ]) {
    await assert.rejects(
      capabilities(root, identity, goalRef),
      EffectRuntimeRequestError,
    );
  }
});

for (const damage of ["owner", "binding", "phase", "filename", "goal_ref", "prepared", "turn_instance"] as const) {
  test(`matching ${damage} contradiction cannot disappear as absence`, async t => {
    const { root, path } = await fixture(t);
    const value = journal();
    if (damage === "owner") value.plan.turn_envelope.agent_id = "other";
    if (damage === "binding") Object.assign(value.plan.transaction.settlement_plan.identity, { binding_id: "other" });
    if (damage === "phase") value.completed_phases = ["quota_spend"];
    if (damage === "filename") value.turn_key = "sha256:" + "b".repeat(64);
    if (damage === "goal_ref") Object.assign(value.plan, { goal_ref: { goal_id: "goal", goal_instance_id: "ginst_" + "a".repeat(32) } });
    if (damage === "prepared") Object.assign(value, { effect_attempts: { quota_spend: {} } });
    if (damage === "turn_instance") value.plan.transaction.turn_instance_id = "other";
    await writeFile(path, JSON.stringify(value));
    await assert.rejects(lookup(root), EffectRuntimeConflictError);
    await assert.rejects(capabilities(root), EffectRuntimeConflictError);
  });
}

for (const opaque of ["{truncated", "null", "{}", "[]"]) {
  test(`opaque history ${opaque} blocks a fresh recovery selection`, async t => {
    const { root, path } = await fixture(t);
    await writeFile(path, opaque);
    await assert.rejects(lookup(root), EffectRuntimeConflictError);
  });
}

test("an unreadable neighbor also prevents falsely unique selection", async t => {
  const { root, path, directory } = await fixture(t);
  await writeFile(path, JSON.stringify(journal()));
  await writeFile(join(directory, `${"f".repeat(64)}.json`), "{truncated");
  await assert.rejects(lookup(root), EffectRuntimeConflictError);
  await assert.rejects(capabilities(root), EffectRuntimeConflictError);
});

test("duplicate identity is ambiguous in either filename order", async t => {
  const { root, path, directory } = await fixture(t);
  await writeFile(path, JSON.stringify(journal()));
  for (const digest of ["0", "f"]) {
    const duplicate = journal();
    duplicate.turn_key = duplicate.plan.transaction.turn_key = `sha256:${digest.repeat(64)}`;
    const other = join(directory, `${digest.repeat(64)}.json`);
    await writeFile(other, JSON.stringify(duplicate));
    await assert.rejects(lookup(root), /matched multiple journals/);
    await assert.rejects(capabilities(root), /matched multiple journals/);
    await rm(other);
  }
});

test("exact field binding does not borrow a foreign settlement or a delimiter collision", async t => {
  const { root, path } = await fixture(t);
  await writeFile(path, JSON.stringify(journal()));
  for (const changes of [{ agent_id: "other" }, { todo_id: "other" }, { turn_instance_id: "other" }]) {
    assert.deepEqual(await findTurnJournalBySettlement({ runtime_root: root, ...selector, ...changes }), { turn_key: null });
  }
  const value = journal();
  Object.assign(value.plan.transaction.settlement_plan.identity, {
    agent_id: "actor:todo", todo_id: "task", effect_id: "goal:actor:todo:task:turn-7",
  });
  value.plan.turn_envelope.agent_id = "actor:todo";
  value.plan.turn_envelope.action.selected_todo.todo_id = "task";
  await writeFile(path, JSON.stringify(value));
  assert.deepEqual(await findTurnJournalBySettlement({ runtime_root: root, ...selector, todo_id: "todo:task" }), { turn_key: null });
});

test("scoped identity cannot bypass the Todo-only journal binding contract", async t => {
  const { root, path } = await fixture(t);
  const value = journal();
  const replan = {
    schema_version: "quota_settlement_identity_v1", goal_id: "goal", agent_id: "actor",
    turn_instance_id: "turn-7", replan_obligation_id: "obligation-1", binding_kind: "autonomous_replan",
    binding_id: "obligation-1", effect_id: "goal:actor:autonomous_replan:obligation-1:turn-7",
  };
  Object.assign(value.plan.turn_envelope, { action: { selected_todo: null, replan_obligation_id: "obligation-1" } });
  Object.assign(value.plan.transaction.settlement_plan, { identity: replan });
  await writeFile(path, JSON.stringify(value));
  await assert.rejects(capabilities(root, replan), /settlement_binding_mismatch/);
  await assert.rejects(capabilities(root, { ...replan, binding_id: "other" }), EffectRuntimeRequestError);
});

test("capability evidence preserves ordered union and explicit missing-gate exclusion", async t => {
  const { root, path } = await fixture(t);
  const value = journal();
  value.plan.turn_envelope.boundary.available_capabilities = ["\u001c network \u0085", "network", "", "\ufeffliteral"];
  Object.assign(value.plan.turn_envelope, { capability_gate: { required_capabilities: ["shell", "network"], missing_capabilities: [] } });
  await writeFile(path, JSON.stringify(value));
  assert.deepEqual(await capabilities(root), { observed_capabilities: ["network", "\ufeffliteral", "shell"] });
  Object.assign(value.plan.turn_envelope, { capability_gate: { required_capabilities: ["shell"], missing_capabilities: ["shell"] } });
  await writeFile(path, JSON.stringify(value));
  assert.deepEqual(await capabilities(root), { observed_capabilities: ["network", "\ufeffliteral"] });
});

test("sidecars are ignored, but a journal symlink or directory cannot supply history", async t => {
  const { root, path, directory } = await fixture(t);
  await writeFile(join(directory, "holder.lock.json"), "{partial");
  await mkdir(path);
  await assert.rejects(lookup(root), /non-regular/);
  await rm(path, { recursive: true });
  if (process.platform === "win32") return; // symlink creation requires separate Windows privileges
  const source = join(root, "source.json");
  await writeFile(source, JSON.stringify(journal()));
  await symlink(source, path);
  await assert.rejects(lookup(root), /non-regular/);
});

test("request boundaries reject traversal, relative roots and contradictory identities", async t => {
  const { root } = await fixture(t);
  for (const goal_id of ["..", ".", "a/b", "a\\b", "a\0b"]) {
    await assert.rejects(findTurnJournalBySettlement({ runtime_root: root, ...selector, goal_id }), EffectRuntimeRequestError);
  }
  await assert.rejects(lookup("relative"), EffectRuntimeRequestError);
  for (const changes of [{ effect_id: "wrong" }, { schema_version: "unknown" }, { todo_id: true }, { binding_kind: "unbound" }]) {
    await assert.rejects(capabilities(root, { ...identity, ...changes }), EffectRuntimeRequestError);
  }
});

for (const [status, count, failedPhase] of [
  ["committed", 3, null], ["stopped", 0, null],
  ["scheduler_action_required", 3, null], ["in_progress", 7, null],
  ["failed", 3, "quota_spend"], ["failed", 0, null],
] as const) {
  test(`write and recovery agree on impossible ${status}/${count}/${failedPhase}`, async t => {
    const { root, path } = await fixture(t);
    const value = journal();
    value.status = status;
    value.completed_phases = phases.slice(0, count);
    Object.assign(value, { receipt: { failed_phase: failedPhase } });
    await assert.rejects(commitTurnJournal({ path, journal: value, expected_effect_id: identity.effect_id }));
    // A historical/corrupt file must not acquire validity merely by bypassing
    // the writer. Both query outcomes use the same status/phase invariant.
    await writeFile(path, JSON.stringify(value));
    await assert.rejects(lookup(root), EffectRuntimeConflictError);
    await assert.rejects(capabilities(root), EffectRuntimeConflictError);
  });
}


test("invalid UTF-8 cannot be normalized into readable history", async t => {
  const { root, path } = await fixture(t);
  await writeFile(path, Buffer.concat([Buffer.from('{"comment":"'), Buffer.from([0xff]), Buffer.from('"}') ]));
  await assert.rejects(lookup(root), /unreadable/);
});
