/** Read-only desktop CI probe. Asset/manifest decisions remain in the Python owner. */
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { appendFile, copyFile, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

type Platform = "macOS" | "Windows";
type ProbeState = "eligible" | "held" | "unavailable"; // Local CI report, never publication authority.
type Asset = { id: number; name: string; size: number; digest: string | null };
type Context = {
  repository: string; runId: number; attempt: number; tag: string;
  productCommit: string; toolingCommit: string; runCommit: string; releaseAssets: Asset[];
};
type Source = Context & { platform: Platform; files: Record<string, string> };
type Job = {
  id: number; name: string; status: string; conclusion: string | null;
  head_sha: string; run_attempt?: number; runner_id: number | null;
  steps: { name: string; started_at: string | null }[];
};
type Admission = { peerAttempt: number; peerJobId: number; ownJobId: number; startedAt: string };
type Report = {
  state: ProbeState; reason: string; publicationAuthorized: false;
  runId?: number; attempt?: number; productCommit?: string; toolingCommit?: string;
  admission?: Admission;
};
const ROOT = ".desktop-preflight";
const WORKFLOW = "loopx-project/loopx/.github/workflows/desktop-release-artifacts.yml";
const HASH = /^[0-9a-f]{40}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const TAG = /^v[0-9]+\.[0-9]+\.[0-9]+(?:[+-][0-9A-Za-z.-]+)?$/;
const toolingRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

function requireValue(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
export function identity(env: NodeJS.ProcessEnv): Omit<Context, "productCommit" | "releaseAssets"> {
  const repository = env.GITHUB_REPOSITORY ?? "";
  const runId = Number(env.GITHUB_RUN_ID), attempt = Number(env.GITHUB_RUN_ATTEMPT);
  const tag = env.RELEASE_TAG ?? "", toolingCommit = env.GITHUB_WORKFLOW_SHA ?? "";
  const runCommit = env.GITHUB_SHA ?? "";
  requireValue(repository === "loopx-project/loopx", "unexpected repository");
  requireValue(env.GITHUB_EVENT_NAME === "workflow_dispatch" && env.PUBLICATION_PROBE === "true", "manual opt-in required");
  requireValue(Number.isSafeInteger(runId) && runId > 0 && Number.isSafeInteger(attempt) && attempt > 0 && attempt <= 10, "unsupported run/attempt");
  requireValue(TAG.test(tag) && HASH.test(toolingCommit) && HASH.test(runCommit), "invalid tag or source identity");
  return { repository, runId, attempt, tag, toolingCommit, runCommit };
}
export function assetsInventory(value: unknown): Asset[] {
  requireValue(Array.isArray(value), "release asset inventory unavailable");
  const seen = new Set<string>();
  return value.map((a) => {
    requireValue(a && Number.isSafeInteger(a.id) && typeof a.name === "string" && Number.isSafeInteger(a.size) && a.size >= 0 && !seen.has(a.name), "ambiguous release asset inventory");
    seen.add(a.name);
    return { id: a.id, name: a.name, size: a.size, digest: a.digest ?? null };
  }).sort((a, b) => a.name.localeCompare(b.name));
}
export async function apiGet(path: string, deadline: number, request: typeof fetch = fetch): Promise<any> {
  const remaining = deadline - Date.now();
  requireValue(remaining > 0, "metadata deadline exhausted");
  const response = await request(`https://api.github.com/${path}`, {
    headers: { Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" },
    signal: AbortSignal.timeout(Math.min(15_000, remaining)),
  });
  requireValue(response.ok, `metadata HTTP ${response.status}`);
  return response.json();
}
export async function completeJobs(repository: string, runId: number, attempt: number, get: (path: string) => Promise<any>): Promise<Job[]> {
  const jobs: Job[] = [];
  let total: number | undefined;
  for (let page = 1; page <= 10; page++) {
    const x = await get(`repos/${repository}/actions/runs/${runId}/attempts/${attempt}/jobs?per_page=100&page=${page}`);
    requireValue(Number.isSafeInteger(x.total_count) && x.total_count >= 0 && Array.isArray(x.jobs), "invalid jobs page");
    requireValue(total === undefined || total === x.total_count, "jobs changed during pagination");
    total = x.total_count;
    jobs.push(...x.jobs);
    requireValue(new Set(jobs.map((j) => j.id)).size === jobs.length, "duplicate jobs page");
    requireValue(jobs.length <= total!, "jobs count overflow");
    if (jobs.length === total) return jobs;
    requireValue(x.jobs.length === 100, "incomplete jobs page");
  }
  throw new Error("jobs pagination limit");
}
export function remainingBudget(startedAt: string, now: number): number {
  const start = Date.parse(startedAt);
  requireValue(Number.isFinite(start) && start <= now, "real Setup job timestamp unavailable");
  return 1800 - (now - start) / 1000;
}
export async function admit(c: Context, get: (path: string) => Promise<any>, now: number): Promise<Admission | undefined> {
  const ownJobs = await completeJobs(c.repository, c.runId, c.attempt, get);
  const own = ownJobs.filter((j) => j.name === "Windows desktop");
  requireValue(own.length === 1, "Windows job identity ambiguous");
  const j = own[0], startedAt = j.steps?.find((s) => s.name === "Set up job")?.started_at;
  requireValue(Number.isSafeInteger(j.id) && j.id > 0 && Number.isSafeInteger(j.runner_id) && Number(j.runner_id) > 0 && j.status === "in_progress" && j.head_sha === c.runCommit && startedAt, "current Windows execution unproved");
  if (remainingBudget(startedAt, now) < 660) return undefined;
  // A later failed/unfinished Mac attempt supersedes earlier success. Missing jobs
  // on a failed-jobs-only rerun may reuse an explicitly proved earlier attempt.
  for (let attempt = c.attempt; attempt >= 1; attempt--) {
    const jobs = attempt === c.attempt ? ownJobs : await completeJobs(c.repository, c.runId, attempt, get);
    const matches = jobs.filter((peer) => peer.name === "macOS desktop");
    requireValue(matches.length <= 1, "Mac job identity ambiguous");
    if (!matches.length) continue;
    const peer = matches[0];
    if (peer.status !== "completed" || peer.conclusion !== "success") return undefined;
    requireValue(Number.isSafeInteger(peer.id) && peer.id > 0 && Number.isSafeInteger(peer.runner_id) && Number(peer.runner_id) > 0 && peer.head_sha === c.runCommit, "Mac runner/source unproved");
    const peerAttempt = peer.run_attempt ?? attempt;
    requireValue(Number.isSafeInteger(peerAttempt) && peerAttempt > 0 && peerAttempt <= attempt, "Mac attempt lineage ambiguous");
    return { peerAttempt, peerJobId: peer.id, ownJobId: j.id, startedAt };
  }
  return undefined;
}
export function verifySource(c: Context, s: Source, platform: Platform, attempt: number): void {
  for (const k of ["repository", "runId", "tag", "productCommit", "toolingCommit", "runCommit"] as const) {
    requireValue(s[k] === c[k], `source identity mismatch: ${k}`);
  }
  requireValue(s.platform === platform && s.attempt === attempt, "source platform/attempt mismatch");
  requireValue(s.files && Object.keys(s.files).length > 0, "source digest set missing");
  for (const [name, digest] of Object.entries(s.files)) {
    requireValue(name !== "DESKTOP-SHA256SUMS" && /^[A-Za-z0-9_.+-]+$/.test(name) && DIGEST.test(digest), "invalid source subject");
  }
}
async function tagCommit(c: Pick<Context, "repository" | "tag">, get: (path: string) => Promise<any>): Promise<string> {
  let x = (await get(`repos/${c.repository}/git/ref/tags/${encodeURIComponent(c.tag)}`)).object;
  for (let i = 0; i < 8; i++) {
    requireValue(x && HASH.test(x.sha), "tag object unavailable");
    if (x.type === "commit") return x.sha;
    requireValue(x.type === "tag", "tag does not resolve to commit");
    x = (await get(`repos/${c.repository}/git/tags/${x.sha}`)).object;
  }
  throw new Error("tag indirection limit");
}
async function sha256(path: string): Promise<string> {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest("hex");
}
async function json(path: string): Promise<any> { return JSON.parse(await readFile(path, "utf8")); }
async function save(path: string, value: unknown): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify(value, null, 2) + "\n");
}
async function report(value: Report): Promise<void> {
  await save(join(ROOT, "report.json"), value);
  if (process.env.GITHUB_STEP_SUMMARY) {
    await appendFile(process.env.GITHUB_STEP_SUMMARY, `### Desktop publication preflight\n\nState: **${value.state}**. ${value.reason}\n\nRead-only observation; publication is not authorized. Both build-job terminal outcomes and a separately reviewed writer remain required.\n`);
  }
  console.log(JSON.stringify(value));
}
function run(executable: string, args: string[], deadline: number): string {
  const timeout = Math.min(45_000, deadline - Date.now());
  requireValue(timeout > 0, "verification deadline exhausted");
  return execFileSync(executable, args, { encoding: "utf8", timeout, maxBuffer: 8 * 1024 * 1024 });
}
function attest(path: string, bundle: string, c: Context, deadline: number): void {
  const verified = JSON.parse(run("gh", ["attestation", "verify", path, "--bundle", bundle,
    "--repo", c.repository, "--signer-workflow", WORKFLOW, "--signer-digest", c.toolingCommit,
    "--deny-self-hosted-runners", "--format", "json"], deadline));
  requireValue(Array.isArray(verified) && verified.length > 0, "attestation verification empty");
}
export async function verifySubjects(
  c: Context, inputs: readonly (readonly [string, Platform, number])[], distDir: string,
  verifyAttestation: (path: string, bundle: string) => void,
  runOwner: (args: string[]) => void,
): Promise<void> {
  const files: Record<string, string> = {};
  for (const [dir, platform, attempt] of inputs) {
    const sourcePath = join(dir, "source.json");
    verifyAttestation(sourcePath, join(dir, "source-bundle.json"));
    const source: Source = await json(sourcePath);
    verifySource(c, source, platform, attempt);
    for (const [name, digest] of Object.entries(source.files)) {
      requireValue(!files[name], "duplicate platform subject");
      requireValue(await sha256(join(distDir, name)) === digest, "download/source digest mismatch");
      verifyAttestation(join(distDir, name), join(dir, "binary-bundle.json"));
      files[name] = digest;
    }
  }
  const manifest = join(distDir, "DESKTOP-SHA256SUMS");
  const args = ["--dist-dir", distDir, "--version", c.tag.slice(1)];
  runOwner(["write-checksums", ...args, "--output", manifest]);
  runOwner(["verify-checksums", ...args, "--checksum-file", manifest]);
  const lines = (await readFile(manifest, "ascii")).trim().split(/\r?\n/);
  requireValue(lines.length === Object.keys(files).length && lines.every((line) => {
    const [digest, name] = line.split("  "); return files[name] === digest;
  }), "manifest/source subject set mismatch");

}

export async function main(mode: string): Promise<void> {
  const deadline = Date.now() + (mode === "verify" ? 240_000 : 90_000);
  const get = (path: string) => apiGet(path, deadline);
  const base = identity(process.env);
  const contextPath = join(ROOT, "context.json"), sourceDir = join(ROOT, "source");
  if (mode === "capture") {
    const productCommit = run("git", ["rev-parse", "HEAD"], deadline).trim();
    requireValue(HASH.test(productCommit) && productCommit === await tagCommit(base, get), "product checkout/tag mismatch");
    const release = await get(`repos/${base.repository}/releases/tags/${encodeURIComponent(base.tag)}`);
    requireValue(release.tag_name === base.tag && !release.draft, "existing public Release required");
    await save(contextPath, { ...base, productCommit, releaseAssets: assetsInventory(release.assets) });
    return;
  }
  const c: Context = await json(contextPath);
  for (const [k, value] of Object.entries(base)) requireValue(c[k as keyof Context] === value, "capture identity changed");
  if (mode === "record") {
    requireValue(run("git", ["rev-parse", "HEAD"], deadline).trim() === c.productCommit, "checkout changed during build");
    const files: Record<string, string> = {};
    for (const file of await readdir("dist/desktop", { withFileTypes: true })) {
      requireValue(file.isFile(), "unexpected desktop directory entry");
      files[file.name] = await sha256(join("dist/desktop", file.name));
    }
    const platform = process.env.RUNNER_OS;
    requireValue(platform === "macOS" || platform === "Windows", "unsupported platform");
    await save(join(sourceDir, "source.json"), { ...c, platform, files });
  } else if (mode === "bundles") {
    requireValue(process.env.SOURCE_BUNDLE && process.env.BINARY_BUNDLE, "attestation bundles missing");
    await copyFile(process.env.SOURCE_BUNDLE, join(sourceDir, "source-bundle.json"));
    await copyFile(process.env.BINARY_BUNDLE, join(sourceDir, "binary-bundle.json"));
  } else if (mode === "admit") {
    const admission = await admit(c, get, Date.now());
    await save(join(ROOT, "admission.json"), admission ?? null);
    requireValue(process.env.GITHUB_OUTPUT, "Actions output unavailable");
    await appendFile(process.env.GITHUB_OUTPUT, `eligible=${Boolean(admission)}\nmac-source=desktop-source-macos-${admission?.peerAttempt ?? c.attempt}\n`);
    if (!admission) await report({ ...base, state: "held", reason: "Peer terminal success or 10m + 60s remaining budget absent; no peer wait.", publicationAuthorized: false });
  } else if (mode === "verify") {
    const admission: Admission | null = await json(join(ROOT, "admission.json"));
    if (!admission) return;
    requireValue(process.env.PEER_ARTIFACT_DOWNLOAD === "success" && process.env.PEER_SOURCE_DOWNLOAD === "success", "peer artifact download failed or skipped");
    requireValue(remainingBudget(admission.startedAt, Date.now()) >= 660, "remaining job budget insufficient");
    requireValue(await tagCommit(c, get) === c.productCommit, "product tag moved");
    const inputs = [[sourceDir, "Windows", c.attempt], [join(ROOT, "mac-source"), "macOS", admission.peerAttempt]] as const;
    const python = process.env.PREFLIGHT_PYTHON ?? "python";
    const owner = join(toolingRoot, "scripts/desktop_release_artifacts.py");
    await verifySubjects(c, inputs, "dist/desktop", (path, bundle) => attest(path, bundle, c, deadline),
      (args) => { run(python, [owner, ...args], deadline); });
    const release = await get(`repos/${c.repository}/releases/tags/${encodeURIComponent(c.tag)}`);
    requireValue(JSON.stringify(assetsInventory(release.assets)) === JSON.stringify(c.releaseAssets), "Release assets changed during probe");
    requireValue(await tagCommit(c, get) === c.productCommit && remainingBudget(admission.startedAt, Date.now()) >= 660, "source or remaining budget changed during verification");
    await report({ ...base, productCommit: c.productCommit, state: "eligible", reason: "Peer success, attested sources, complete four assets and manifest verified; Release inventory unchanged. Windows final job outcome remains pending.", publicationAuthorized: false, admission });
  } else {
    throw new Error("unknown probe mode");
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const mode = process.argv[2];
  main(mode).catch(async (error) => {
    await report({ state: "unavailable", reason: error instanceof Error ? error.message : "verification unavailable", publicationAuthorized: false });
    if (mode === "admit" && process.env.GITHUB_OUTPUT) await appendFile(process.env.GITHUB_OUTPUT, "eligible=false\n");
    process.exitCode = 1;
  });
}
