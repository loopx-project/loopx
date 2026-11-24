import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {copyFile, link, mkdir, mkdtemp, readFile, realpath, rm, stat, symlink, writeFile} from "node:fs/promises";
import {platform, tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {evaluateTaskLeaseWriteScopesOverlap} from "../../loopx/control_plane/work_items/task_lease_acquire_decision.ts";
import {observeLeaseWorktree} from "../../loopx/control_plane/work_items/task_lease_workspace.ts";

test("code-edit worktree admission rejects shared hardlinks and recovers after a private copy", {
  skip: !["darwin", "linux"].includes(platform()),
}, async t => {
  const temporary = await mkdtemp(join(tmpdir(), "lease-hardlinks-"));
  t.after(() => rm(temporary, {recursive: true, force: true}));
  const main = join(temporary, "main");
  const git = (...args: string[]) => execFileSync("git", args, {stdio: "pipe"});
  git("init", main);
  git("-C", main, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
    "commit", "--allow-empty", "-m", "fixture");
  git("-C", main, "config", "remote.origin.url", "https://github.com/example/project.git");
  const a = join(temporary, "a"), b = join(temporary, "b");
  git("-C", main, "worktree", "add", "-b", "a", a);
  git("-C", main, "worktree", "add", "-b", "b", b);
  await mkdir(join(a, "src"));
  await mkdir(join(b, "src"));
  await writeFile(join(b, ".gitignore"), "src/\n");
  const source = join(a, "src/main.ts"), shared = join(b, "src/main.ts");
  await writeFile(source, "original\n");
  await writeFile(shared, "private\n");
  const overlaps = (path: string) => evaluateTaskLeaseWriteScopesOverlap({
    left: ["src/**"], right: [path],
  }).overlap === true;
  const original = await observeLeaseWorktree(b, overlaps);
  await rm(shared);
  await link(source, shared);
  assert.equal((await stat(shared)).nlink, 2);
  assert.equal((await stat(source)).ino, (await stat(shared)).ino);
  // Different checkout roots still share bytes: an ordinary write in B changes A.
  await writeFile(shared, "peer changed\n");
  assert.equal(await readFile(source, "utf8"), "peer changed\n");
  await assert.rejects(observeLeaseWorktree(b, overlaps), /hard.link|shared|file links/);

  await rm(shared);
  await copyFile(source, shared);
  assert.deepEqual(await observeLeaseWorktree(b, overlaps), original);
  await writeFile(shared, "private edit\n");
  assert.equal(await readFile(source, "utf8"), "peer changed\n");

  // A direct, permitted executable symlink must not hide a shared target inode.
  const bin = join(b, "src/tool");
  await symlink("main.ts", bin);
  assert.deepEqual(await observeLeaseWorktree(b, overlaps), original);
  await rm(shared);
  await link(source, shared);
  await assert.rejects(observeLeaseWorktree(b, overlaps), /hard.link|shared|file links/);
  await rm(bin);
  // Hardlinks outside the declared edit scope do not change narrow admission.
  await rm(shared);
  await copyFile(source, shared);
  await link(source, join(b, "outside.ts"));
  assert.deepEqual(await observeLeaseWorktree(await realpath(b), overlaps), original);
});
