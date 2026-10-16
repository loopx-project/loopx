import assert from "node:assert/strict";
import test, {type TestContext} from "node:test";
import {mkdir, mkdtemp, readFile, realpath, rm, stat, symlink, writeFile} from "node:fs/promises";
import {join} from "node:path";
import {tmpdir} from "node:os";
import {planGoalTransferWorkspace, readGoalTransferWorkspaceReview, retainGoalTransferWorkspace,
  reviewGoalTransferWorkspace, verifyGoalTransferWorkspaceReview} from "../../loopx/control_plane/goals/planned_transfer_workspace.ts";
import {inspectTransferFile} from "../../loopx/control_plane/goals/planned_transfer_container.ts";
import {reviewProtectedGoalTransferInputs} from "../../loopx/control_plane/goals/planned_transfer_payload.ts";

async function fixture(t: TestContext) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "loopx-workspace-transfer-")));
  t.after(() => rm(root, {recursive: true, force: true}));
  const source = join(root, "source"), project = join(root, "project"), runtime = join(root, "runtime");
  await writeFile(source, "Retain the original pending work input\n");
  const material = {id: "00000002", source_relative_path: "input.txt", path: source, ...await inspectTransferFile(source)};
  const plan = planGoalTransferWorkspace(project, runtime, [material], null);
  const target = {project, runtime_root: runtime, registry_path: join(project, ".loopx/registry.json"), provider: "file"};
  const witness = {path: target.registry_path, sha256: null}, artifact = "a".repeat(64);
  const directory = join(runtime, "original-operation"), identity = {goal_id: "original", operation_id: "reviewed-move"};
  const review = await reviewGoalTransferWorkspace(plan, artifact, target, witness);
  return {root, source, project, runtime, material, plan, target, witness, artifact, directory, identity, review};
}

test("reviewed placement makes original relative input usable and retry neither republishes nor overwrites later work", async t => {
  const f = await fixture(t), path = join(f.project, "input.txt");
  assert.equal(f.review.ready, true); assert.equal(f.review.materials[0]!.presence, "missing");
  await verifyGoalTransferWorkspaceReview(f.plan, f.review, f.directory, f.identity, f.review.preview_sha256, false);
  await retainGoalTransferWorkspace(f.plan, f.directory, f.identity, f.review.preview_sha256);
  assert.equal(await readFile(path, "utf8"), await readFile(f.source, "utf8"));
  const before = await stat(path);
  const current = await reviewGoalTransferWorkspace(f.plan, f.artifact, f.target, f.witness);
  assert.equal(current.materials[0]!.presence, "matched"); assert.notEqual(current.preview_sha256, f.review.preview_sha256);
  assert.equal(await readGoalTransferWorkspaceReview(f.directory, f.identity), f.review.preview_sha256);
  await verifyGoalTransferWorkspaceReview(f.plan, current, f.directory, f.identity, f.review.preview_sha256, true);
  await retainGoalTransferWorkspace(f.plan, f.directory, f.identity, f.review.preview_sha256);
  assert.equal((await stat(path)).mtimeMs, before.mtimeMs);
  await writeFile(path, "Later destination work");
  const changed = await reviewGoalTransferWorkspace(f.plan, f.artifact, f.target, f.witness);
  assert.equal(changed.ready, false); assert.equal(changed.materials[0]!.presence, "occupied");
  await assert.rejects(verifyGoalTransferWorkspaceReview(f.plan, changed, f.directory, f.identity, f.review.preview_sha256, true), /occupied/);
  assert.equal(await readFile(path, "utf8"), "Later destination work");
  await rm(path);
  const missing = await reviewGoalTransferWorkspace(f.plan, f.artifact, f.target, f.witness);
  await assert.rejects(verifyGoalTransferWorkspaceReview(f.plan, missing, f.directory, f.identity, f.review.preview_sha256, true), /attempted workspace material is missing/);
  await assert.rejects(retainGoalTransferWorkspace(f.plan, f.directory, f.identity, f.review.preview_sha256), /attempted workspace material is missing/);
  await assert.rejects(stat(path), {code: "ENOENT"});
});

test("stale target state or changed operation cannot consume an earlier review", async t => {
  const f = await fixture(t); await mkdir(f.project);
  const path = join(f.project, "input.txt"); await writeFile(path, "Unrelated destination input");
  const changed = await reviewGoalTransferWorkspace(f.plan, f.artifact, f.target, f.witness);
  await assert.rejects(verifyGoalTransferWorkspaceReview(f.plan, changed, f.directory, f.identity, f.review.preview_sha256, false), /preview changed/);
  await assert.rejects(stat(f.directory), {code: "ENOENT"});
  assert.equal(await readFile(path, "utf8"), "Unrelated destination input");
  await rm(path);
  await retainGoalTransferWorkspace(f.plan, f.directory, f.identity, f.review.preview_sha256);
  await assert.rejects(readGoalTransferWorkspaceReview(f.directory, {...f.identity, operation_id: "different"}), /review changed/);
  const intent = join(f.directory, "workspace-materials", "00000002.json"); await rm(intent);
  const current = await reviewGoalTransferWorkspace(f.plan, f.artifact, f.target, f.witness);
  await assert.rejects(verifyGoalTransferWorkspaceReview(f.plan, current, f.directory, f.identity, f.review.preview_sha256, true), /intent is missing/);
});

test("matching existing input is read and retained without changing its inode, mode or timestamp", async t => {
  const f = await fixture(t); await mkdir(f.project);
  const path = join(f.project, "input.txt"); await writeFile(path, await readFile(f.source), {mode: 0o640});
  const before = await stat(path), review = await reviewGoalTransferWorkspace(f.plan, f.artifact, f.target, f.witness);
  assert.equal(review.materials[0]!.presence, "matched");
  await verifyGoalTransferWorkspaceReview(f.plan, review, f.directory, f.identity, review.preview_sha256, false);
  await retainGoalTransferWorkspace(f.plan, f.directory, f.identity, review.preview_sha256);
  const after = await stat(path);
  assert.deepEqual([after.ino, after.mode, after.mtimeMs], [before.ino, before.mode, before.mtimeMs]);
});

test("explicit complete mapping refuses control state, traversal, omission, collisions and linked destinations", async t => {
  const f = await fixture(t);
  for (const path of ["../escape", ".loopx/goals/other/state", ".git/config", "./input.txt", "data//input", ""]) {
    assert.throws(() => planGoalTransferWorkspace(f.project, f.runtime, [f.material], {"00000002": path}), /destination/);
  }
  assert.throws(() => planGoalTransferWorkspace(f.project, f.runtime, [f.material], {}), /every selected material/);
  const materials = [f.material, {...f.material, id: "00000003"}, {...f.material, id: "00000004"}];
  for (const paths of [["data", "data/input", "data-other"], ["same", "same", "elsewhere"]]) {
    assert.throws(() => planGoalTransferWorkspace(f.project, f.runtime, materials, Object.fromEntries(materials.map((m, i) => [m.id, paths[i]]))), /collide|contain/);
  }
  const outside = join(f.root, "outside"); await mkdir(f.project); await mkdir(outside);
  await symlink(outside, join(f.project, "linked"));
  const linked = planGoalTransferWorkspace(f.project, f.runtime, [f.material], {"00000002": "linked/input"});
  await assert.rejects(reviewGoalTransferWorkspace(linked, f.artifact, f.target, f.witness), /linked ancestor/);
  await assert.rejects(stat(join(outside, "input")), {code: "ENOENT"});
});

test("interrupted publication with a missing original file is held, rather than inferred safe to repeat", async t => {
  const f = await fixture(t); await rm(f.source);
  await assert.rejects(retainGoalTransferWorkspace(f.plan, f.directory, f.identity, f.review.preview_sha256), {code: "ENOENT"});
  assert.equal(JSON.parse(await readFile(join(f.directory, "workspace-materials", "00000002.json"), "utf8")).sha256, f.material.sha256);
  await writeFile(f.source, "Retain the original pending work input\n");
  await assert.rejects(retainGoalTransferWorkspace(f.plan, f.directory, f.identity, f.review.preview_sha256), /attempted workspace material is missing/);
  await assert.rejects(stat(join(f.project, "input.txt")), {code: "ENOENT"});
});

test("unsupported source platform refuses before recipient validation or preparation effects", async () => {
  const original = Object.getOwnPropertyDescriptor(process, "platform")!;
  Object.defineProperty(process, "platform", {...original, value: "win32"});
  try { await assert.rejects(reviewProtectedGoalTransferInputs({}, {}, "test"), /POSIX profile before preparation/); }
  finally { Object.defineProperty(process, "platform", original); }
});
