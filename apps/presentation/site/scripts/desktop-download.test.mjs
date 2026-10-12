import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { desktopArtifactNames, fetchLatestDesktopDownload, resolveDesktopDownload } from "./desktop-download.ts";

const origin = "https://github.com/loopx-project/loopx/releases/download";
const sha = value => createHash("sha256").update(value).digest("hex");
function fixture(version) {
  // Independent complete-set acceptance, shared with the canonical publisher.
  const names = ["LoopX.app.zip", `LoopX_${version}_aarch64.dmg`,
    `LoopX_${version}_x64-setup.exe`, `LoopX_${version}_x64_en-US.msi`];
  const body = names.map(name => `${sha(name)}  ${name}\n`).join("");
  const asset = (name, digest) => ({ name, state: "uploaded", size: 10,
    browser_download_url: `${origin}/v${version}/${name}`, digest: `sha256:${digest}` });
  return { release: { tag_name: `v${version}`, draft: false, prerelease: false,
    assets: [...names.map(name => asset(name, sha(name))), asset("DESKTOP-SHA256SUMS", sha(body))] }, body };
}
const older = fixture("1.3.1");
const newest = fixture("1.4.0");
const read = fixtures => async url => fixtures.find(f =>
  url === `${origin}/${f.release.tag_name}/DESKTOP-SHA256SUMS`).body;
const copy = value => structuredClone(value);

test("selects highest complete stable semantic version, not inventory order", async () => {
  const a = fixture("1.9.0"), b = fixture("1.10.0");
  assert.deepEqual(desktopArtifactNames("1.4.0"), newest.release.assets.slice(0, -1).map(a => a.name));
  assert.deepEqual(await resolveDesktopDownload([a.release, older.release, b.release], read([a, older, b])),
    { version: "1.10.0", url: `${origin}/v1.10.0/LoopX.app.zip` });
});

test("empty, partial, uploading and corrupt new releases retain previous complete download", async () => {
  const empty = copy(newest), partial = copy(newest), uploading = copy(newest), corrupt = copy(newest);
  empty.release.assets = [];
  partial.release.assets = partial.release.assets.filter(a => !a.name.endsWith(".msi"));
  uploading.release.assets[0].state = "starter";
  corrupt.body = corrupt.body.replace("LoopX.app.zip", "another.zip");
  corrupt.release.assets.at(-1).digest = `sha256:${sha(corrupt.body)}`;
  for (const invalid of [empty, partial, uploading, corrupt]) {
    assert.equal((await resolveDesktopDownload([invalid.release, older.release], read([invalid, older]))).version, "1.3.1");
  }
});

test("drafts, prereleases, plugin tags and mutable updater pointers cannot replace app release", async () => {
  const release = fixture("99.0.0").release;
  const excluded = [{ ...release, draft: true }, { ...release, prerelease: true },
    { ...release, tag_name: "v99.0.0-rc.1" }, { ...release, tag_name: "plugin-v99.0.0" },
    { ...release, tag_name: "desktop-stable" }];
  assert.equal((await resolveDesktopDownload([...excluded, newest.release], read([newest]))).version, "1.4.0");
});

test("foreign URL, absent digest, duplicate asset and checksum mismatch are unavailable", async () => {
  for (const mutate of [f => { f.release.assets[0].browser_download_url = "https://example.com/LoopX.app.zip"; },
    f => { f.release.assets[0].digest = null; },
    f => { f.release.assets.push(copy(f.release.assets[0])); }, f => { f.body += "\n"; }]) {
    const invalid = copy(newest); mutate(invalid);
    await assert.rejects(resolveDesktopDownload([invalid.release], read([invalid])), /No complete stable/);
  }
});

test("inventory is paginated; API credentials never reach manifest redirects", async () => {
  const requests = [];
  const request = async (url, options) => {
    requests.push({ url, options });
    if (url.includes("api.github.com")) {
      assert.equal(options.headers.Authorization, "Bearer synthetic-token");
      return Response.json(url.endsWith("page=1")
        ? Array.from({ length: 100 }, () => ({ tag_name: "plugin-v100.0.0" })) : [newest.release]);
    }
    assert.equal(options.headers, undefined);
    return new Response(newest.body);
  };
  assert.equal((await fetchLatestDesktopDownload({ token: "synthetic-token", request })).version, "1.4.0");
  assert.equal(requests.length, 3);
});

test("API failure, missing complete release and inaccessible manifest fail publication", async () => {
  for (const status of [403, 429, 500]) {
    await assert.rejects(fetchLatestDesktopDownload({ request: async () => new Response("", { status }) }), /inventory failed/);
  }
  await assert.rejects(fetchLatestDesktopDownload({ request: async () => { throw Error("network unavailable"); } }), /inventory request failed/);
  await assert.rejects(fetchLatestDesktopDownload({ request: async () => new Response("invalid JSON") }), /read as JSON/);
  await assert.rejects(resolveDesktopDownload({ message: "broken API" }, read([])), /must be an array/);
  await assert.rejects(resolveDesktopDownload([], read([])), /No complete stable/);
  await assert.rejects(fetchLatestDesktopDownload({ request: async url => url.includes("api.github.com")
    ? Response.json([newest.release]) : new Response("", { status: 404 }) }), /manifest download failed/);
});

test("truncated inventory cannot claim latest release", async () => {
  await assert.rejects(fetchLatestDesktopDownload({ request: async () => Response.json(
    Array.from({ length: 100 }, () => newest.release)) }), /exceeds 1,000/);
});

test("only successful trusted public desktop publication triggers the release refresh", () => {
  const workflow = readFileSync(new URL("../../../../.github/workflows/frontstage-pages.yml", import.meta.url), "utf8");
  const expression = workflow.match(/\n    if: (github\.event_name != 'workflow_run'[^\n]+)/)?.[1];
  assert.ok(expression, "workflow must guard the release refresh");
  const allowed = new Function("github", `return ${expression}`);
  const context = { event_name: "workflow_run", repository: "loopx-project/loopx",
    event: { workflow_run: { conclusion: "success", event: "release",
      head_repository: { full_name: "loopx-project/loopx" } } } };
  assert.equal(allowed(context), true);
  for (const mutate of [c => { c.event.workflow_run.conclusion = "failure"; },
    c => { c.event.workflow_run.event = "pull_request"; },
    c => { c.event.workflow_run.head_repository.full_name = "contributor/fork"; }]) {
    const rejected = copy(context); mutate(rejected); assert.equal(allowed(rejected), false);
  }
  const dispatch = copy(context); dispatch.event.workflow_run.event = "workflow_dispatch";
  assert.equal(allowed(dispatch), true);
  assert.equal(allowed({ event_name: "pull_request" }), true);
  assert.equal(allowed({ event_name: "push" }), true);
});
