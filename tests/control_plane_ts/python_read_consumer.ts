import {spawnSync} from "node:child_process";
import {closeSync, mkdtempSync, openSync, rmSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";

// Keep the complete JSON on stdin, with a finite source independent of pipe EOF.
// Large synchronous pipe writes can strand the consumer in stdin.read() under
// the concurrent conformance suite before any product operation is reached.
export function runPythonReadConsumer(consumer: string, source: unknown) {
  const directory = mkdtempSync(join(tmpdir(), "loopx-python-read-"));
  try {
    const path = join(directory, "source.json");
    writeFileSync(path, JSON.stringify(source), {encoding: "utf8", mode: 0o600});
    const input = openSync(path, "r");
    try {
      return spawnSync(resolveTestPython(), ["-c", consumer], {
        encoding: "utf8", timeout: 90_000, stdio: [input, "pipe", "pipe"],
      });
    } finally {
      closeSync(input);
    }
  } finally {
    rmSync(directory, {recursive: true, force: true});
  }
}
