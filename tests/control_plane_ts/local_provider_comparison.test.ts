import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {mkdir, mkdtemp, readFile, readdir, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import test from "node:test";

for (const provider of ["file", "sqlite"]) {
  test(`${provider} comparison verifies historical replay after later commits and cleans its store`,
    {timeout: 120000}, async t => {
      const directory = await mkdtemp(join(tmpdir(), "local-provider-report-"));
      t.after(() => rm(directory, {recursive: true, force: true}));
      const data = join(directory, "tmp"), output = join(directory, "report.json");
      await mkdir(data);
      const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
        fileURLToPath(new URL("../../examples/coordination/local-provider-comparison.ts", import.meta.url)),
        "--provider", provider, "--workload", "mixed", "--commits", "128", "--samples", "3", "--output", output],
      {encoding: "utf8", timeout: 110000, env: {...process.env, TMPDIR: data, TMP: data, TEMP: data}});
      assert.equal(child.status, 0, child.stderr);
      const report = JSON.parse(await readFile(output, "utf8"));
      assert.equal(report.provider, provider);
      assert.equal(report.commits, 128);
      assert.equal(report.complete_record_and_receipt_checks, "passed");
      assert.equal(report.original_receipt_recovery_after_reopen, "passed");
      assert.equal(report.historical_replay_and_conflict_checks, "passed");
      assert.equal(report.warm_head.n, 3);
      assert.deepEqual(await readdir(data), []);
    });
}
