import {readFile, mkdir, copyFile} from "node:fs/promises";
import {createRequire} from "node:module";
import {resolve} from "node:path";
const require = createRequire(import.meta.url);
const {createCoverageMap} = require("istanbul-lib-coverage");
const {createContext} = require("istanbul-lib-report");
const reports = require("istanbul-reports");
const [input, output] = process.argv.slice(2);
if (!input || !output) throw new Error("Expected input and output coverage directories");
const coverage = createCoverageMap({});
const selected = new Set();
let catalogCount;
for (let index = 1; index <= 3; index += 1) {
  const shard = resolve(input, `dashboard-coverage-${index}`);
  const result = JSON.parse(await readFile(resolve(shard, "acceptance-results.json"), "utf8"));
  if (result.shard !== `${index}/3` || !Number.isSafeInteger(result.catalog_count) ||
      result.catalog_count < 3 || (catalogCount !== undefined && catalogCount !== result.catalog_count)) {
    throw new Error("Dashboard shard receipt does not match the planned catalog");
  }
  catalogCount = result.catalog_count;
  for (const id of result.selected) {
    if (selected.has(id) || result.scenarios[id]?.status !== "PASS") {
      throw new Error(`Incomplete or overlapping Dashboard acceptance: ${id}`);
    }
    selected.add(id);
  }
  coverage.merge(JSON.parse(await readFile(resolve(shard, "browser-coverage.json"), "utf8")));
}
if (selected.size !== catalogCount) throw new Error("Dashboard acceptance omitted catalog scenarios");
if (coverage.files().length === 0) throw new Error("No Dashboard browser coverage");
await mkdir(output, {recursive:true});
await copyFile(resolve(input, "dashboard-coverage-1/lcov.info"), resolve(output, "lcov.info"));
reports.create("lcovonly", {file:"browser-lcov.info"}).execute(createContext({dir:output, coverageMap:coverage}));
console.log(`Merged ${selected.size} accepted Dashboard scenarios`);
