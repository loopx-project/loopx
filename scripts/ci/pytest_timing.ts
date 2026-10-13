/** Read CI timing observations; never supplies a test gate or reusable cache. */
import { readFileSync, statSync } from "node:fs";
import { pathToFileURL } from "node:url";

const prefix = "LOOPX_PYTEST_TIMING ";
type Phase = "setup" | "call" | "teardown"; // pytest TestReport vocabulary, local adapter
type Outcome = "passed" | "failed" | "skipped";
type Observation = { seconds: number | null; outcome: Outcome };
type Case = { display_nodeid: string } & Partial<Record<Phase, Observation>>;

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

export function summarizeTimingLog(log: string) {
  const cases = new Map<string, Case>();
  let started = false, ended = false;
  let selected: number | null = null, exitCode: number | null = null;
  let elapsed = 0, records = 0;
  for (const original of log.split(/\r?\n/)) {
    const line = original.replace(/^\uFEFF/, "").replace(/\x1b\[[0-9;]*m/g, "")
      .replace(/^\d{4}-\d{2}-\d{2}T\S+Z\s+/, "");
    if (!line.startsWith(prefix)) continue;
    if (++records > 200_000) throw new Error("Timing log exceeds the observation limit.");
    const row = JSON.parse(line.slice(prefix.length));
    if (!row || Array.isArray(row) || typeof row !== "object" ||
        !finite(row.elapsed_seconds) || row.elapsed_seconds < elapsed || ended) {
      throw new Error("Invalid timing record order or elapsed duration.");
    }
    elapsed = row.elapsed_seconds;
    if (row.event === "start") {
      if (started || records !== 1 || row.version !== 1) throw new Error("Invalid timing start.");
      started = true;
    } else if (!started) {
      throw new Error("Timing record has no start.");
    } else if (row.event === "collection") {
      if (selected !== null || cases.size || !Number.isSafeInteger(row.selected) || row.selected < 0) {
        throw new Error("Invalid selected collection count.");
      }
      selected = row.selected;
    } else if (row.event === "phase") {
      if (selected === null || typeof row.test_id !== "string" || !/^[a-f0-9]{64}$/.test(row.test_id) ||
          typeof row.display_nodeid !== "string" || !row.display_nodeid ||
          row.display_nodeid.length > 512 || !["setup", "call", "teardown"].includes(row.phase) ||
          !["passed", "failed", "skipped"].includes(row.outcome) ||
          (row.seconds !== null && !finite(row.seconds))) throw new Error("Invalid pytest phase.");
      const phase = row.phase as Phase;
      const entry: Case = cases.get(row.test_id) ?? { display_nodeid: row.display_nodeid };
      if (entry.display_nodeid !== row.display_nodeid || entry[phase] || (phase !== "setup" && !entry.setup) ||
          (phase === "call" && entry.setup?.outcome !== "passed") || entry.teardown) {
        throw new Error("Duplicate or out-of-order pytest phase.");
      }
      entry[phase] = { seconds: row.seconds, outcome: row.outcome as Outcome };
      cases.set(row.test_id, entry);
      if (cases.size > selected) throw new Error("Reported cases exceed the selected collection.");
    } else if (row.event === "end") {
      if (!Number.isInteger(row.exit_code) || row.exit_code < 0 || row.exit_code > 5) {
        throw new Error("Invalid pytest exit code.");
      }
      exitCode = row.exit_code;
      ended = true;
    } else {
      throw new Error(`Unusable timing event: ${String(row.event)}`);
    }
  }
  if (!started) throw new Error("No CI timing observations found; this is not a zero-cost run.");
  let measured = 0, invalid = 0, finished = 0;
  const phaseSeconds = { setup: 0, call: 0, teardown: 0 };
  const longest = [];
  for (const [testId, entry] of cases) {
    let seconds = 0;
    for (const phase of ["setup", "call", "teardown"] as const) {
      const value = entry[phase]?.seconds;
      if (value === null) invalid++;
      else if (value !== undefined) { seconds += value; phaseSeconds[phase] += value; }
    }
    measured += seconds;
    const caseFinished = !!(entry.teardown && (entry.call || entry.setup?.outcome !== "passed"));
    if (caseFinished) finished++;
    longest.push({ test_id: testId, display_nodeid: entry.display_nodeid,
      measured_seconds: seconds, finished: caseFinished });
  }
  longest.sort((a, b) => b.measured_seconds - a.measured_seconds || a.test_id.localeCompare(b.test_id));
  return {
    execution_complete: ended && (exitCode === 0 || exitCode === 1) &&
      selected !== null && selected > 0 && finished === selected && invalid === 0,
    pytest_exit_code: exitCode,
    selected_cases: selected,
    observed_cases: cases.size,
    finished_cases: finished,
    invalid_duration_count: invalid,
    observed_elapsed_seconds: elapsed,
    measured_worker_seconds: measured,
    phase_seconds: phaseSeconds,
    longest_cases: longest.slice(0, 25),
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.argv.length !== 3) throw new Error("Usage: node scripts/ci/pytest_timing.ts JOB_LOG");
    if (statSync(process.argv[2]).size > 32 * 1024 * 1024) throw new Error("Job log exceeds 32 MiB.");
    process.stdout.write(JSON.stringify(summarizeTimingLog(readFileSync(process.argv[2], "utf8")), null, 2) + "\n");
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : error}\n`);
    process.exitCode = 1;
  }
}
