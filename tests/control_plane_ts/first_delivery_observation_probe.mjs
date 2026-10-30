// Test-process instrumentation only: count actual reads without changing facts.
import fs from "node:fs";
import fsp from "node:fs/promises";
import {syncBuiltinESMExports} from "node:module";
const sink = process.env.LOOPX_OBSERVATION_TEST_READS;
const goal = process.env.LOOPX_OBSERVATION_TEST_GOAL;
function record(path) {
  const name = String(path).replaceAll("\\", "/");
  if (!sink || !goal || !name.includes(`/goals/${goal}/`)) return;
  if (name.endsWith("/runs/index.jsonl")) fs.appendFileSync(sink, "index\n");
  if (name.endsWith("/rollout-event-log.jsonl")) fs.appendFileSync(sink, "rollout\n");
}
const syncRead = fs.readFileSync, asyncRead = fsp.readFile;
fs.readFileSync = (...args) => {record(args[0]); return syncRead(...args);};
fsp.readFile = (...args) => {record(args[0]); return asyncRead(...args);};
syncBuiltinESMExports();
