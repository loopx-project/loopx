/** Reproducible, explicit public-source experiment for issue #4926. */
import {execFileSync} from "node:child_process";
import {mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath, pathToFileURL} from "node:url";
import {parseArgs} from "node:util";
import {assess, contextInput, digest, exportSnapshot, loadQueries, object, verifyCitations} from "./evidence.ts";
import {directSearch, prepareRune, RUNE_REVISION, RuneSession} from "./providers.ts";

const HERE = dirname(fileURLToPath(import.meta.url));

export async function main(argv: string[]): Promise<void> {
  const {values} = parseArgs({args: argv, options: {
    repository: {type: "string"}, revision: {type: "string"}, out: {type: "string"},
    queries: {type: "string", default: join(HERE, "queries.json")},
    "rune-source": {type: "string"}, "prepare-rune": {type: "boolean", default: false},
    "loopx-python": {type: "string"},
  }});
  if (!values.repository || !values.revision || !values.out || (values["prepare-rune"] && values["rune-source"]))
    throw new Error("usage: --repository REPO --revision FULL_SHA --out NEW_DIRECTORY [--prepare-rune | --rune-source SOURCE] [--loopx-python PYTHON]");
  const queryBytes = readFileSync(values.queries!), queries = loadQueries(JSON.parse(queryBytes.toString("utf8")));
  const output = resolve(values.out);
  mkdirSync(output); // Never overwrite a previous observation or selectively rerun failed rows.
  const temporary = mkdtempSync(join(tmpdir(), "loopx-repository-context-"));
  const snapshot = join(temporary, "source"); mkdirSync(snapshot);
  const report: Record<string, unknown> = {
    source_revision: values.revision, query_sha256: digest(queryBytes), node_version: process.version,
    platform: process.platform, rune_revision: RUNE_REVISION,
    scope: "file discovery and citation currency; no repair-outcome or model-benefit qualification",
    order: "direct search then Rune per query; one shared immutable export; no cache flushing",
    result_file_limit: 8, model_calls: 0, live_goal_state_read: false,
    provider_preparation_included: values["prepare-rune"], provider_preparation_ms: null,
    provider_preparation_commands_planned: values["prepare-rune"] ? 3 : 0,
    snapshot_commands_planned: 3, snapshot_ms: null, rune_startup_ms: null, rune_file_count: null, rows: [],
  };
  const rows: Record<string, unknown>[] = [], started = performance.now();
  let rune: RuneSession | undefined, providerUnavailable = !values["prepare-rune"] && !values["rune-source"];
  let failed = false;
  try {
    const snapshotStart = performance.now();
    const source = exportSnapshot(resolve(values.repository), values.revision, snapshot);
    report.snapshot_ms = performance.now() - snapshotStart;
    report.snapshot_archive_bytes = source.archive_bytes;
    report.snapshot_source_file_count = source.source_hashes.size;
    let runeSource = values["rune-source"];
    if (values["prepare-rune"]) {
      runeSource = join(temporary, "rune"); const start = performance.now();
      try { prepareRune(runeSource); }
      catch { providerUnavailable = true; failed = true; report.provider_preparation_error = "provider_setup_unavailable"; }
      report.provider_preparation_ms = performance.now() - start;
    }
    if (runeSource && !providerUnavailable) {
      const start = performance.now();
      try {
        rune = await RuneSession.start(resolve(runeSource), snapshot);
        report.rune_startup_ms = rune.startup_ms; report.rune_file_count = rune.file_count;
      } catch { providerUnavailable = true; failed = true; report.rune_startup_error = "provider_unavailable"; }
      report.rune_startup_attempt_ms = performance.now() - start;
    }
    for (const query of queries) {
      for (const provider of ["direct-search", "rune"]) {
        const start = performance.now();
        let row: Record<string, unknown>;
        try {
          if (provider === "rune" && !rune) {
            row = {query_id: query.id, provider, status: providerUnavailable && (values["prepare-rune"] || values["rune-source"]) ? "unavailable" : "disabled"};
          } else {
            const result = provider === "rune" ? await rune!.search(query) : {candidates: directSearch(snapshot, query), observation: {}};
            const raw = result.candidates;
            const checked = verifyCitations(snapshot, raw, 8, source.source_hashes);
            const context = contextInput(values.revision, checked.evidence);
            const contextName = `${query.id}.${provider}.context.json`;
            writeFileSync(join(output, contextName), JSON.stringify(context, null, 2) + "\n");
            row = {query_id: query.id, provider, status: "completed", elapsed_ms: performance.now() - start,
              ...checked, ...assess(checked.evidence, query.relevant_files), context_file: contextName,
              provider_observation: result.observation,
              search_requests: 1, context_bytes: Buffer.byteLength(JSON.stringify(context)), native_plan: "not_requested"};
            if (values["loopx-python"]) {
              const nativeStart = performance.now();
              try {
                const bytes = execFileSync(values["loopx-python"], ["-m", "loopx.cli", "--format", "json", "issue-fix", "workflow-plan",
                  "--url", query.issue, "--repository-context-json", join(output, contextName)],
                {cwd: snapshot, encoding: "utf8", timeout: 30_000, maxBuffer: 2 * 1024 * 1024});
                const plan = object(JSON.parse(bytes));
                const contextReadback = object(plan.repository_context);
                if (plan.ok !== true || plan.external_writes_performed !== false || plan.todo_write_performed !== false ||
                    contextReadback.repository_revision !== values.revision) throw new Error("native_plan_invalid");
                row.native_plan = "accepted_read_only";
                row.native_context_revision = contextReadback.repository_revision;
                row.native_context_status = contextReadback.context_status;
              } catch { failed = true; row.native_plan = "unavailable"; }
              row.native_plan_ms = performance.now() - nativeStart;
            }
          }
        } catch {
          failed = true; row = {query_id: query.id, provider, status: "unavailable", elapsed_ms: performance.now() - start,
            reason_code: "provider_or_native_readback_failed"};
        }
        rows.push(row);
        report.rows = rows;
        writeFileSync(join(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
      }
    }
  } catch {
    failed = true; report.run_error = "source_snapshot_unavailable";
  } finally {
    try { if (rune) await rune.close(); }
    catch { failed = true; report.teardown_error = "provider_close_failed"; }
    rmSync(temporary, {recursive: true, force: true});
    report.total_ms = performance.now() - started;
    report.failed = failed;
    report.rows = rows;
    writeFileSync(join(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
  }
  console.log(JSON.stringify({report: join(output, "report.json"), failed}));
  if (failed) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).catch(() => { console.error("repository_context_pilot_failed; inspect the retained report or command arguments"); process.exitCode = 1; });
}
