#!/usr/bin/env node
/** Semantic probe cases. Synthetic CI metadata; real asset owner and disk bytes. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { resolveTestPython } from "../scripts/test-python.mjs";
import { admit, apiGet, assetsInventory, completeJobs, identity, remainingBudget, verifySource, verifySubjects } from "../scripts/ci/desktop_release_preflight.ts";

const sha = "a".repeat(40), now = Date.parse("2026-01-01T12:00:00Z");
const c = { repository: "loopx-project/loopx", runId: 100, attempt: 1, tag: "v0.0.1", productCommit: "b".repeat(40), toolingCommit: sha, runCommit: sha, releaseAssets: [] };
const win = { id: 1, name: "Windows desktop", status: "in_progress", conclusion: null, head_sha: sha, runner_id: 10, steps: [{ name: "Set up job", started_at: "2026-01-01T11:52:00Z" }] };
const mac = { id: 2, name: "macOS desktop", status: "completed", conclusion: "success", head_sha: sha, runner_id: 11, steps: [] };
const page = (jobs) => ({ total_count: jobs.length, jobs });
const get = async () => page([win, mac]);
let cases = 0;
async function check(name, fn) { await fn(); cases++; console.log(`ok: ${name}`); }
await check("manual opt-in only; PR, fork repository and default-off never admitted", async () => {
  const env = { GITHUB_REPOSITORY: c.repository, GITHUB_RUN_ID: "100", GITHUB_RUN_ATTEMPT: "1", GITHUB_EVENT_NAME: "workflow_dispatch", PUBLICATION_PROBE: "true", RELEASE_TAG: c.tag, GITHUB_WORKFLOW_SHA: sha, GITHUB_SHA: sha };
  assert.equal(identity(env).runId, 100);
  for (const change of [{ PUBLICATION_PROBE: "false" }, { PUBLICATION_PROBE: undefined }, { GITHUB_EVENT_NAME: "pull_request" }, { GITHUB_REPOSITORY: "fork/loopx" }, { RELEASE_TAG: "v0.0.1;echo" }]) assert.throws(() => identity({ ...env, ...change }));
});
await check("peer terminal success and a real setup timestamp required", async () => {
  assert.equal((await admit(c, get, now)).peerJobId, 2);
  for (const peer of [{ ...mac, status: "in_progress", conclusion: null }, { ...mac, conclusion: "failure" }, { ...mac, conclusion: "cancelled" }, { ...mac, conclusion: "skipped" }]) assert.equal(await admit(c, async () => page([win, peer]), now), undefined);
  await assert.rejects(admit(c, async () => page([{ ...win, steps: [] }, mac]), now));
  await assert.rejects(admit(c, async () => page([win, { ...mac, head_sha: "c".repeat(40) }]), now));
});
await check("no waiting; 10m plus 60s reserve stays within the original 30m job", async () => {
  assert.equal(remainingBudget("2026-01-01T11:41:00Z", now), 660);
  assert.equal(await admit(c, get, now + 1_000_000), undefined);
  assert.throws(() => remainingBudget("invalid", now));
  assert.throws(() => remainingBudget("2026-01-01T13:00:00Z", now));
});
await check("explicit older upstream lineage on failed-jobs-only rerun; later failure wins", async () => {
  const rerun = { ...c, attempt: 2 };
  const result = await admit(rerun, async (url) => url.includes("attempts/2/") ? page([win]) : page([mac]), now);
  assert.equal(result.peerAttempt, 1);
  let calls = 0;
  assert.equal(await admit(rerun, async () => { calls++; return page([win, { ...mac, conclusion: "failure" }]); }, now), undefined);
  assert.equal(calls, 1);
});
await check("complete pagination; duplicate, changed count and incomplete pages rejected", async () => {
  const first = Array.from({ length: 100 }, (_, id) => ({ id }));
  assert.equal((await completeJobs(c.repository, 100, 1, async (url) => ({ total_count: 101, jobs: url.endsWith("page=1") ? first : [{ id: 100 }] }))).length, 101);
  await assert.rejects(completeJobs(c.repository, 100, 1, async () => ({ total_count: 101, jobs: first })));
  await assert.rejects(completeJobs(c.repository, 100, 1, async () => ({ total_count: 3, jobs: [{ id: 1 }] })));
  await assert.rejects(admit(c, async () => page([win, mac, { ...mac, id: 3 }]), now));
});
await check("403/429/deadline/timeout never yield metadata", async () => {
  for (const status of [403, 429]) await assert.rejects(apiGet("repos/example", Date.now() + 1000, async () => ({ ok: false, status })), new RegExp(String(status)));
  await assert.rejects(apiGet("repos/example", Date.now() - 1));
  await assert.rejects(apiGet("repos/example", Date.now() + 1000, async () => { throw new DOMException("timeout", "TimeoutError"); }));
});
await check("source run, actual product, tooling, platform and attempt must match", async () => {
  const source = { ...c, platform: "macOS", files: { "LoopX.app.zip": "0".repeat(64) } };
  verifySource(c, source, "macOS", 1);
  for (const change of [{ runId: 101 }, { attempt: 2 }, { productCommit: sha }, { toolingCommit: "c".repeat(40) }, { platform: "Windows" }, { files: { "../escape": "0".repeat(64) } }]) assert.throws(() => verifySource(c, { ...source, ...change }, "macOS", 1));
});
await check("ambiguous release inventory rejected", async () => {
  assert.deepEqual(assetsInventory([]), []);
  assert.throws(() => assetsInventory([{ id: 1, name: "same", size: 1 }, { id: 2, name: "same", size: 2 }]));
});
await check("real disk digests and Python manifest owner; missing, extra, stale and verification errors", async () => {
  const temp = await mkdtemp(join(tmpdir(), "loopx-desktop-preflight-"));
  try {
    const dist = join(temp, "dist"), w = join(temp, "win"), m = join(temp, "mac");
    await Promise.all([dist, w, m].map((dir) => mkdir(dir)));
    const names = ["LoopX.app.zip", "LoopX_0.0.1_aarch64.dmg", "LoopX_0.0.1_x64-setup.exe", "LoopX_0.0.1_x64_en-US.msi"];
    const digestSets = [{}, {}];
    for (const [i, name] of names.entries()) {
      const bytes = Buffer.from(`synthetic artifact ${name}`);
      await writeFile(join(dist, name), bytes);
      digestSets[i < 2 ? 1 : 0][name] = createHash("sha256").update(bytes).digest("hex");
    }
    for (const [dir, platform, files] of [[w, "Windows", digestSets[0]], [m, "macOS", digestSets[1]]]) await writeFile(join(dir, "source.json"), JSON.stringify({ ...c, platform, files }));
    const inputs = [[w, "Windows", 1], [m, "macOS", 1]];
    // Only cryptographic transport is substituted here. The owner and byte IO are real.
    const owner = new URL("../scripts/desktop_release_artifacts.py", import.meta.url);
    const python = resolveTestPython();
    const runOwner = (args) => execFileSync(python, [fileURLToPath(owner), ...args], { stdio: "pipe" });
    let verifications = 0;
    const verify = () => { verifications++; };
    for (const newline of ["\n", "\r\n"]) {
      const manifest = join(dist, "DESKTOP-SHA256SUMS");
      // Force each host's bytes after the real writer; the real verifier still runs.
      const runOwnerWithNewline = (args) => {
        runOwner(args);
        if (args[0] === "write-checksums") writeFileSync(manifest, readFileSync(manifest, "ascii").replace(/\r?\n/g, newline), "ascii");
      };
      verifications = 0;
      await verifySubjects(c, inputs, dist, verify, runOwnerWithNewline);
      assert.equal(verifications, 6);
      assert.deepEqual((await readFile(manifest, "ascii")).trim().split(/\r?\n/).map((line) => line.split("  ")),
        names.map((name) => [digestSets[1][name] ?? digestSets[0][name], name]));
    }
    await assert.rejects(verifySubjects(c, inputs, dist, () => { throw new Error("signature verification failed"); }, runOwner), /signature verification/);
    await writeFile(join(dist, names[0]), "tampered");
    await assert.rejects(verifySubjects(c, inputs, dist, verify, runOwner), /digest mismatch/);
    await writeFile(join(dist, names[0]), `synthetic artifact ${names[0]}`);
    await writeFile(join(dist, "extra.bin"), "extra");
    await assert.rejects(verifySubjects(c, inputs, dist, verify, runOwner));
    await rm(join(dist, "extra.bin"));
    await rm(join(dist, names[3]));
    await assert.rejects(verifySubjects(c, inputs, dist, verify, runOwner));
  } finally { await rm(temp, { recursive: true, force: true }); }
});
console.log(`desktop-release-preflight-smoke: ${cases} semantic cases passed (hosted Windows/cryptographic transport not qualified)`);
