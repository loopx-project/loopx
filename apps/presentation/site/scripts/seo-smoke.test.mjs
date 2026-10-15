import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const env = { ...process.env };
delete env.LOOPX_SITE_BASE;
delete env.LOOPX_SITE_RESOLVE_DESKTOP_RELEASE;
const run = (script, args) => spawnSync(process.execPath, [
  fileURLToPath(new URL(script, import.meta.url)), ...args,
], { cwd: root, env, encoding: "utf8", timeout: 120_000 });
const diagnostics = (result) => `${result.error ?? ""}\n${result.stdout}\n${result.stderr}`;

for (const { name, args, href, wrongHref } of [
  { name: "default root preview", args: [], href: "/skill.md", wrongHref: "/loopx/skill.md" },
  { name: "project publication", args: ["--base", "/loopx/"], href: "/loopx/skill.md", wrongHref: "/skill.md" },
]) {
  test(`SEO validates the ${name} and rejects broken guide discovery`, async () => {
    const directory = await mkdtemp(join(tmpdir(), "loopx-site-seo-"));
    try {
      const build = run("build.mjs", [...args, "--outDir", directory]);
      assert.equal(build.status, 0, diagnostics(build));
      const index = join(directory, "index.html");
      const html = await readFile(index, "utf8");
      assert(html.includes(`href="${href}"`), "the real build uses the documented guide URL");
      const valid = run("seo-smoke.mjs", [directory]);
      assert.equal(valid.status, 0, diagnostics(valid));

      await writeFile(index, html.replace(`href="${href}"`, `href="${wrongHref}"`));
      const wrongBase = run("seo-smoke.mjs", [directory]);
      assert.notEqual(wrongBase.status, 0, "a guide link under the other base must fail");
      assert.match(wrongBase.stderr, /homepage footer discovers the skill/);

      await writeFile(index, html);
      await rm(join(directory, "skill.md"));
      const missing = run("seo-smoke.mjs", [directory]);
      assert.notEqual(missing.status, 0, "a missing published guide must fail");
      assert.match(missing.stderr, /ENOENT.*skill\.md/);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
}
