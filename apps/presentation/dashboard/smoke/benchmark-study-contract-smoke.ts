import fixture from "../public/benchmark-study.example.json" with { type: "json" };
import {
  parseBenchmarkStudyDashboard,
  resolveBenchmarkStudyDashboardUrl,
  selectBenchmarkStudyRun,
} from "../src/data/benchmark-study.js";

const packet = parseBenchmarkStudyDashboard(fixture);
const first = packet.runs[0]!;
const second = { ...first, case_id: "another-case" };
if (selectBenchmarkStudyRun([first, second], first.run_id, second.case_id) !== second) throw new Error("run selection lost its case");
if (selectBenchmarkStudyRun([first, second], first.run_id, "") !== undefined) throw new Error("ambiguous legacy link selected a run");
if (selectBenchmarkStudyRun([first], first.run_id, "") !== first) throw new Error("unique legacy link stopped resolving");
if (selectBenchmarkStudyRun([first], first.run_id, "missing-case") !== undefined) throw new Error("stale case link selected another case");
if (selectBenchmarkStudyRun([first], "missing-run", "") !== undefined) throw new Error("stale run link selected another run");
if (selectBenchmarkStudyRun([first, first], first.run_id, first.case_id) !== undefined) throw new Error("duplicate full identity selected a run");
if (selectBenchmarkStudyRun([first], "", "") !== first) throw new Error("unselected runs view lost its default");
if (packet.status !== "provisional") throw new Error("fixture must expose provisional coverage");
if (packet.campaign.intended_cell_denominator !== 8) throw new Error("campaign denominator drifted");
if (packet.campaign.selected_score_countable_cell_count !== 6) throw new Error("countable numerator drifted");
if (packet.arms.some((arm) => arm.intended_case_count !== 2)) throw new Error("arm denominator missing");
if (packet.cases[1]?.complete_declared_design !== false) throw new Error("case provisional state missing");
if (!packet.runs.every((run) => typeof run.countability.score_countable === "boolean")) throw new Error("run countability missing");
if (packet.contrasts.loopx_plain?.primary_metric_directions.flat !== 1) throw new Error("matched direction drifted from run metrics");
if (packet.contrasts.goal_hint?.binary_metric_transitions.reward?.same !== 2) throw new Error("binary transition drifted from run metrics");
if (packet.cases[1]?.largest_eligible_primary_contrast?.metric_deltas.feature?.delta !== 2) throw new Error("case-level eligible contrast missing");
if (packet.cases[0]?.arms.some((cell) => cell.score_countable && !cell.metrics.preservation)) throw new Error("countable cell guardrail missing");

const resolved = resolveBenchmarkStudyDashboardUrl("/study.json", "http://127.0.0.1:5173/benchmarks/study");
if (resolved !== "http://127.0.0.1:5173/study.json") throw new Error("local readback URL drifted");
const packaged = resolveBenchmarkStudyDashboardUrl("/chat/benchmark-study.example.json", "http://127.0.0.1:5173/chat/benchmarks/study");
if (packaged !== "http://127.0.0.1:5173/chat/benchmark-study.example.json") throw new Error("packaged-base readback URL drifted");
try {
  resolveBenchmarkStudyDashboardUrl("file:///private/study.json", "http://127.0.0.1:5173/");
  throw new Error("file source must be rejected");
} catch (error) {
  if (error instanceof Error && error.message === "file source must be rejected") throw error;
}
try {
  resolveBenchmarkStudyDashboardUrl("https://example.com/study.json", "http://127.0.0.1:5173/");
  throw new Error("cross-origin source must be rejected");
} catch (error) {
  if (error instanceof Error && error.message === "cross-origin source must be rejected") throw error;
}

console.log("benchmark study dashboard contract smoke passed");
