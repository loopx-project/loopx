import assert from "node:assert/strict";
import {test} from "node:test";
import {mkdtemp, mkdir, writeFile, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {writeDashboardBrowserCoverage} from "../../examples/dashboard-browser-coverage.mjs";
import {spawnSync} from "node:child_process";
test("coverage merge preserves disjoint hits and rejects missing or duplicate acceptance", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "loopx-dashboard-coverage-"));
  const source = "apps/presentation/dashboard/src/example.ts";
  const file = hit => ({path:source, statementMap:{0:{start:{line:1,column:0},end:{line:1,column:1}}},fnMap:{},branchMap:{},s:{0:hit},f:{},b:{}});
  const writeReceipt = async (index, id) => writeFile(resolve(root, `dashboard-coverage-${index}/acceptance-results.json`), JSON.stringify({shard:`${index}/3`,catalog_count:3,selected:[id],scenarios:{[id]:{status:"PASS"}}}));
  const run = () => spawnSync(process.execPath, [resolve("scripts/ci/merge-dashboard-coverage.mjs"),root,resolve(root,"merged")],{encoding:"utf8"});
  try {
    for (let index=1;index<=3;index+=1) {
      const shard = resolve(root,`dashboard-coverage-${index}`);
      await mkdir(shard);
      await writeReceipt(index,`scenario-${index}`);
      await writeFile(resolve(shard,"browser-coverage.json"),JSON.stringify({[source]:file(index===2?1:0)}));
      await writeFile(resolve(shard,"lcov.info"),"unit-coverage-preserved");
    }
    let result = run();
    assert.equal(result.status,0,result.stderr);
    assert.match(await readFile(resolve(root,"merged/browser-lcov.info"),"utf8"),/DA:1,1/);
    assert.equal(await readFile(resolve(root,"merged/lcov.info"),"utf8"),"unit-coverage-preserved");
    await writeReceipt(3,"scenario-2");
    result=run();assert.notEqual(result.status,0);assert.match(result.stderr,/overlapping/);
    await rm(resolve(root,"dashboard-coverage-3"),{recursive:true});
    assert.notEqual(run().status,0);
  } finally { await rm(root,{recursive:true,force:true}); }
});

test("browser coverage creates a fresh output directory for independent acceptance", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "loopx-browser-coverage-"));
  try {
    const outputDir = resolve(root,"coverage/dashboard");
    await writeDashboardBrowserCoverage([{url:"http://localhost/src/example.ts",source:"const a = 1;\n",functions:[{functionName:"",isBlockCoverage:true,ranges:[{startOffset:0,endOffset:13,count:1}]}]}],{
      repoRoot:root,dashboardDir:resolve(root,"apps/presentation/dashboard"),outputDir,
    });
    const coverage = JSON.parse(await readFile(resolve(outputDir,"browser-coverage.json"),"utf8"));
    assert.ok(coverage["apps/presentation/dashboard/src/example.ts"]);
    assert.match(await readFile(resolve(outputDir,"browser-lcov.info"),"utf8"),/DA:1,1/);
  } finally { await rm(root,{recursive:true,force:true}); }
});
