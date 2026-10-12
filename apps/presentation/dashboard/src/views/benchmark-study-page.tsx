import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  Database,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";

import { benchmarkStudyRoute } from "../router";
import {
  parseBenchmarkStudyDashboard,
  resolveBenchmarkStudyDashboardUrl,
  selectBenchmarkStudyRun,
  type BenchmarkStudyArm,
  type BenchmarkStudyCase,
  type BenchmarkStudyDashboard,
  type BenchmarkStudyRun,
  type BenchmarkStudyView,
} from "../data/benchmark-study";
import "./benchmark-study.css";

const views: Array<{ id: BenchmarkStudyView; label: string }> = [
  { id: "campaign", label: "Campaign" },
  { id: "arms", label: "Arms" },
  { id: "cases", label: "Cases" },
  { id: "runs", label: "Runs" },
];

function percent(value: number | null | undefined) {
  return value == null ? "—" : `${(value * 100).toFixed(value >= 0.995 ? 0 : 1)}%`;
}

function compactNumber(value: number | null | undefined) {
  if (value == null) return "—";
  return new Intl.NumberFormat("en", { maximumFractionDigits: 2, notation: "compact" }).format(value);
}

function durationLabel(value: number | null | undefined) {
  if (value == null) return "—";
  const minutes = value / 60000;
  return minutes >= 120 ? `${(minutes / 60).toFixed(1)} h` : `${compactNumber(minutes)} min`;
}

function countList(values: Record<string, number>) {
  const entries = Object.entries(values);
  return entries.length ? entries.map(([name, count]) => `${name} (${count})`).join(", ") : "—";
}

function metricValue(metric: { value: number; total?: number; unit?: string } | undefined) {
  if (!metric) return "—";
  const value = metric.total == null ? compactNumber(metric.value) : `${compactNumber(metric.value)}/${compactNumber(metric.total)}`;
  return metric.unit ? `${value} ${metric.unit}` : value;
}

function metricAggregate(arm: BenchmarkStudyArm, metricName: string) {
  const metric = arm.metrics[metricName];
  if (!metric || metric.case_denominator === 0) return "—";
  if (metric.suite_micro_rate != null) {
    return `${percent(metric.suite_micro_rate)} · ${compactNumber(metric.suite_micro_numerator)}/${compactNumber(metric.suite_micro_denominator)}`;
  }
  return `${compactNumber(metric.value_mean)} mean`;
}

function largestContrastLabel(item: BenchmarkStudyCase, primaryMetric: string) {
  const contrast = item.largest_eligible_primary_contrast;
  const metric = contrast?.metric_deltas[primaryMetric];
  if (!contrast || !metric || metric.comparison_unavailable_reason) return null;
  const value = metric.delta_rate == null ? metric.delta : metric.delta_rate * 100;
  if (value == null) return null;
  const prefix = value > 0 ? "+" : "";
  return {
    direction: metric.direction,
    text: `${contrast.candidate_arm_id}: ${prefix}${compactNumber(value)}${metric.delta_rate == null ? "" : " pp"}`,
  };
}

function StateBadge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "success" | "warning" | "info" }) {
  return <span className={`benchmark-state benchmark-state-${tone}`}>{children}</span>;
}

function CampaignView({ packet, primaryMetric }: { packet: BenchmarkStudyDashboard; primaryMetric: string }) {
  return (
    <div className="benchmark-view-stack">
      <section aria-labelledby="arm-summary-title">
        <div className="benchmark-section-heading">
          <div>
            <p className="benchmark-kicker">ARM SUMMARY</p>
            <h2 id="arm-summary-title">Comparable outcomes, denominator first</h2>
          </div>
          <p>Only one score-countable run per declared case × arm cell is selected.</p>
        </div>
        <div className="benchmark-arm-grid">
          {packet.arms.map((arm) => {
            const reward = Object.values(arm.binary_outcomes)[0];
            return (
              <article className="benchmark-arm-card" key={arm.arm_id}>
                <div className="benchmark-card-heading">
                  <div>
                    <span className="benchmark-mono">{arm.arm_role}</span>
                    <h3>{arm.arm_id}</h3>
                  </div>
                  <StateBadge tone={arm.coverage_rate === 1 ? "success" : "warning"}>
                    {arm.coverage_rate === 1 ? "Complete" : "Provisional"}
                  </StateBadge>
                </div>
                <dl className="benchmark-stat-list">
                  <div><dt>Primary · {primaryMetric}</dt><dd>{metricAggregate(arm, primaryMetric)}</dd></div>
                  <div><dt>Score-countable coverage</dt><dd>{arm.selected_score_countable_case_count}/{arm.intended_case_count}</dd></div>
                  <div><dt>Binary success</dt><dd>{reward ? `${reward.success_count}/${reward.case_denominator}` : "Not declared"}</dd></div>
                </dl>
              </article>
            );
          })}
        </div>
      </section>

      <section aria-labelledby="contrast-title">
        <div className="benchmark-section-heading">
          <div>
            <p className="benchmark-kicker">MATCHED CONTRASTS</p>
            <h2 id="contrast-title">Direction counts on eligible pairs</h2>
          </div>
          <p>Raw run volume is never used as a comparison denominator.</p>
        </div>
        <div className="benchmark-table-shell">
          <table>
            <thead><tr><th>Candidate arm</th><th>Matched denominator</th><th>Improved</th><th>Flat</th><th>Regressed</th><th>Binary transitions</th></tr></thead>
            <tbody>
              {Object.entries(packet.contrasts).map(([armId, contrast]) => (
                <tr key={armId}>
                  <td><strong>{armId}</strong></td>
                  <td>{contrast.matched_pair_denominator}</td>
                  <td className="benchmark-positive">{contrast.primary_metric_directions.improved}</td>
                  <td>{contrast.primary_metric_directions.flat}</td>
                  <td className="benchmark-negative">{contrast.primary_metric_directions.regressed}</td>
                  <td>{Object.entries(contrast.binary_metric_transitions).map(([metric, transitions]) => `${metric}: 0→1 ${transitions["0_to_1"]}, 1→0 ${transitions["1_to_0"]}, same ${transitions.same}`).join(" · ") || "—"}</td>
                </tr>
              ))}
              {Object.keys(packet.contrasts).length === 0 && <tr><td colSpan={6}>No matched comparisons are countable yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="runtime-health-title">
        <div className="benchmark-section-heading">
          <div>
            <p className="benchmark-kicker">RUNTIME HEALTH</p>
            <h2 id="runtime-health-title">Qualified observations, without execution authority</h2>
          </div>
          <p>{packet.campaign.runtime_observation_count} public-safe runtime observations.</p>
        </div>
        <div className="benchmark-runtime-list">
          {Object.entries(packet.campaign.runtime_classification_counts).map(([classification, count]) => (
            <div key={classification}><span>{classification}</span><strong>{count}</strong></div>
          ))}
          {packet.campaign.runtime_observation_count === 0 && <p className="benchmark-muted">No runtime observations uploaded.</p>}
        </div>
      </section>
    </div>
  );
}

function ArmsView({ packet }: { packet: BenchmarkStudyDashboard }) {
  return (
    <div className="benchmark-detail-grid">
      {packet.arms.map((arm) => (
        <article className="benchmark-detail-card" key={arm.arm_id}>
          <div className="benchmark-card-heading">
            <div><span className="benchmark-mono">{arm.arm_role}</span><h2>{arm.arm_id}</h2></div>
            <StateBadge tone={arm.coverage_rate === 1 ? "success" : "warning"}>{arm.selected_score_countable_case_count}/{arm.intended_case_count} countable</StateBadge>
          </div>
          <div className="benchmark-factor-row">
            {Object.entries(arm.factor_assignments).map(([factor, level]) => <span key={factor}>{factor}: <strong>{level}</strong></span>)}
          </div>
          <dl className="benchmark-stat-list">
            {packet.design.metric_catalog.map((metric) => (
              <div key={metric.metric_name}><dt>{metric.role} · {metric.metric_name}</dt><dd>{metricAggregate(arm, metric.metric_name)}</dd></div>
            ))}
            <div><dt>Runs terminal / observed</dt><dd>{arm.terminal_run_count}/{arm.run_count}</dd></div>
            <div><dt>Median duration</dt><dd>{durationLabel(arm.effort.duration_ms?.median)}</dd></div>
            <div><dt>Protocols</dt><dd>{countList(arm.protocol_counts)}</dd></div>
            <div><dt>Runner revisions</dt><dd>{countList(arm.runner_revision_counts)}</dd></div>
            <div><dt>Orchestrator runtimes</dt><dd>{Object.keys(arm.orchestrator_runtime_counts).length || 0} distinct</dd></div>
            <div><dt>Failure classes</dt><dd>{countList(arm.failure_class_counts)}</dd></div>
          </dl>
        </article>
      ))}
    </div>
  );
}

function CasesView({ packet, primaryMetric, onOpenRun }: { packet: BenchmarkStudyDashboard; primaryMetric: string; onOpenRun: (runId: string, caseId: string) => void }) {
  return (
    <div className="benchmark-table-shell benchmark-wide-table">
      <table>
        <thead><tr><th>Case</th><th>Design status</th><th>Largest eligible delta</th>{packet.arms.map((arm) => <th key={arm.arm_id}>{arm.arm_id}</th>)}</tr></thead>
        <tbody>
          {packet.cases.map((item) => {
            const largestContrast = largestContrastLabel(item, primaryMetric);
            return (
              <tr key={item.case_id}>
                <td><strong>{item.case_id}</strong></td>
                <td><StateBadge tone={item.complete_declared_design ? "success" : "warning"}>{item.complete_declared_design ? "Complete" : "Provisional"}</StateBadge></td>
                <td className={largestContrast?.direction === "improved" ? "benchmark-positive" : largestContrast?.direction === "regressed" ? "benchmark-negative" : undefined}>
                  {largestContrast?.text ?? "—"}
                </td>
                {item.arms.map((cell) => (
                  <td key={cell.arm_id}>
                    {cell.selected_run_id && cell.score_countable ? (
                      <button className="benchmark-cell-link" onClick={() => onOpenRun(cell.selected_run_id!, item.case_id)} type="button">
                        <span>{primaryMetric}: {metricValue(cell.metrics[primaryMetric])}</span>
                        {packet.design.metric_catalog.filter((metric) => metric.metric_name !== primaryMetric).map((metric) => (
                          <small className="benchmark-cell-metric" key={metric.metric_name}>{metric.metric_name}: {metricValue(cell.metrics[metric.metric_name])}</small>
                        ))}
                        <small>Countable · {durationLabel(cell.effort.duration_ms)} <ArrowRight aria-hidden="true" size={12} /></small>
                        {cell.insight && <small>{cell.insight.failure_class} · {cell.insight.confidence}</small>}
                      </button>
                    ) : <span className="benchmark-muted">Not countable</span>}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RunDetail({ run, packet }: { run: BenchmarkStudyRun; packet: BenchmarkStudyDashboard }) {
  return (
    <aside aria-label={`Run detail for ${run.run_id}`} className="benchmark-run-detail">
      <div className="benchmark-card-heading">
        <div><span className="benchmark-mono">RUN DETAIL</span><h2>{run.run_id}</h2></div>
        <StateBadge tone={run.countability.score_countable ? "success" : "warning"}>{run.countability.score_countable ? "Score-countable" : "Diagnostic only"}</StateBadge>
      </div>
      <dl className="benchmark-stat-list benchmark-run-facts">
        <div><dt>Case / arm</dt><dd>{run.case_id} · {run.arm_id}</dd></div>
        <div><dt>Lifecycle</dt><dd>{run.status} · {run.observed_at}</dd></div>
        <div><dt>Protocol</dt><dd>{run.protocol_id}</dd></div>
        <div><dt>Qualification</dt><dd>Integrity {run.countability.integrity_qualified ? "qualified" : "not qualified"} · result {run.countability.official_result_present ? "present" : "missing"}</dd></div>
        <div><dt>Treatment fidelity</dt><dd>{run.treatment_fidelity}</dd></div>
        <div><dt>Effort</dt><dd>{durationLabel(run.effort.duration_ms)} · {compactNumber(run.effort.agent_steps)} steps · {compactNumber(run.effort.token_count)} tokens</dd></div>
        <div><dt>Runner revision</dt><dd>{run.runner_revision ?? "—"}</dd></div>
        <div><dt>Upload provenance</dt><dd>{run.upload_provenance.producer_id} · {run.upload_provenance.source_revision}</dd></div>
      </dl>
      <div className="benchmark-run-metrics">
        {packet.design.metric_catalog.map((metric) => <div key={metric.metric_name}><span>{metric.metric_name}</span><strong>{metricValue(run.metrics[metric.metric_name])}</strong></div>)}
      </div>
      {run.redacted_insight && (
        <div className="benchmark-insight">
          <span className="benchmark-mono">REDACTED CASE INSIGHT · {run.redacted_insight.confidence}</span>
          <p>{run.redacted_insight.causal_summary}</p>
          <small>Implication: {run.redacted_insight.implication}</small><br />
          <small>Next probe: {run.redacted_insight.next_probe}</small>
          {!!run.redacted_insight.evidence_refs?.length && <><br /><small>Evidence: {run.redacted_insight.evidence_refs.join(", ")}</small></>}
        </div>
      )}
    </aside>
  );
}

function RunsView({ packet, selectedRunId, selectedCaseId, onSelectRun }: { packet: BenchmarkStudyDashboard; selectedRunId: string; selectedCaseId: string; onSelectRun: (runId: string, caseId: string) => void }) {
  const selected = selectBenchmarkStudyRun(packet.runs, selectedRunId, selectedCaseId);
  return (
    <div className="benchmark-runs-layout">
      <div className="benchmark-table-shell">
        <table>
          <thead><tr><th>Run</th><th>Case</th><th>Arm</th><th>Status</th><th>Countability</th></tr></thead>
          <tbody>{packet.runs.map((run) => (
            <tr aria-selected={run === selected} key={JSON.stringify([run.case_id, run.run_id])}>
              <td><button className="benchmark-run-link" onClick={() => onSelectRun(run.run_id, run.case_id)} type="button">{run.run_id}</button></td>
              <td>{run.case_id}</td><td>{run.arm_id}</td><td>{run.status}</td>
              <td>{run.countability.score_countable ? "Score-countable" : "Diagnostic only"}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      {selected ? <RunDetail packet={packet} run={selected} /> : <p role="status">Select a run from the table to view its details.</p>}
    </div>
  );
}

export function BenchmarkStudyPage() {
  const search = benchmarkStudyRoute.useSearch();
  const navigate = benchmarkStudyRoute.useNavigate();
  const [packet, setPacket] = useState<BenchmarkStudyDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const source = useMemo(() => {
    const configuredSource = search.dashboardUrl || `${import.meta.env.BASE_URL}benchmark-study.example.json`;
    try { return { url: resolveBenchmarkStudyDashboardUrl(configuredSource, window.location.href), error: null }; }
    catch (nextError) { return { url: "", error: nextError instanceof Error ? nextError.message : "Invalid dashboard source" }; }
  }, [search.dashboardUrl]);

  useEffect(() => {
    let active = true;
    setPacket(null);
    setError(null);
    if (source.error) {
      setError(source.error);
      return () => { active = false; };
    }
    fetch(source.url, { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status} while loading the dashboard packet`);
        return parseBenchmarkStudyDashboard(await response.json());
      })
      .then((nextPacket) => { if (active) setPacket(nextPacket); })
      .catch((nextError: unknown) => { if (active) setError(nextError instanceof Error ? nextError.message : "Unable to load dashboard packet"); });
    return () => { active = false; };
  }, [reloadKey, source]);

  useEffect(() => {
    if (packet) document.title = `${packet.design.labels.title ?? packet.study_id} · LoopX Benchmark`;
  }, [packet]);

  if (error) {
    return <main className="benchmark-page benchmark-loading"><CircleAlert aria-hidden="true" /><h1>Dashboard packet unavailable</h1><p>{error}</p><button onClick={() => setReloadKey((value) => value + 1)} type="button"><RefreshCw aria-hidden="true" size={16} /> Retry readback</button></main>;
  }
  if (!packet) return <main aria-busy="true" className="benchmark-page benchmark-loading"><Activity aria-hidden="true" /><h1>Reading benchmark study</h1><p>Validating the public-safe dashboard packet…</p></main>;

  const primaryMetric = packet.design.metric_catalog.find((metric) => metric.role === "primary")?.metric_name ?? "primary";
  const setView = (view: BenchmarkStudyView, runId = search.runId, caseId = search.caseId) => navigate({ search: (current) => ({ ...current, view, runId, caseId }) });
  const sourcePath = new URL(source.url).pathname;

  return (
    <main className="benchmark-page">
      <header className="benchmark-hero">
        <div className="benchmark-hero-topline">
          <a className="benchmark-wordmark" href={import.meta.env.BASE_URL}>LoopX</a>
          <div className="benchmark-readonly"><ShieldCheck aria-hidden="true" size={15} /> Derived read-only projection</div>
        </div>
        <div className="benchmark-hero-grid">
          <div>
            <p className="benchmark-kicker">BENCHMARK STUDY / {packet.benchmark_id}</p>
            <div className="benchmark-title-row"><h1>{packet.design.labels.title ?? packet.study_id}</h1><StateBadge tone={packet.status === "complete" ? "success" : "warning"}>{packet.status}</StateBadge></div>
            <p className="benchmark-lead">One declared study, explicit denominators, and the same countability rules from campaign summary to exact run.</p>
          </div>
          <dl className="benchmark-identity">
            <div><dt>Study</dt><dd>{packet.study_id}</dd></div>
            <div><dt>Protocol</dt><dd>{packet.design.protocol_id}</dd></div>
            <div><dt>Case set</dt><dd>{packet.design.case_set.case_set_id}</dd></div>
          </dl>
        </div>
        <div className="benchmark-kpi-grid">
          <article><Database aria-hidden="true" /><span>Score-countable cells</span><strong>{packet.campaign.selected_score_countable_cell_count}<small> / {packet.campaign.intended_cell_denominator}</small></strong><p>{percent(packet.campaign.selected_score_countable_coverage_rate)} declared coverage</p></article>
          <article><CheckCircle2 aria-hidden="true" /><span>Complete designs</span><strong>{packet.campaign.complete_declared_design_case_count}<small> / {packet.campaign.intended_case_count}</small></strong><p>Cases with every declared arm</p></article>
          <article><ArrowRight aria-hidden="true" /><span>Matched comparisons</span><strong>{packet.campaign.matched_pair_countable_count}</strong><p>Eligible pairs only</p></article>
          <article><Activity aria-hidden="true" /><span>In flight</span><strong>{packet.campaign.in_flight_run_count}</strong><p>{packet.status === "provisional" ? "Coverage remains provisional" : "Declared coverage complete"}</p></article>
        </div>
      </header>

      <nav aria-label="Benchmark dashboard views" className="benchmark-tabs">
        {views.map((view) => <button aria-current={search.view === view.id ? "page" : undefined} key={view.id} onClick={() => setView(view.id)} type="button">{view.label}</button>)}
        <button className="benchmark-refresh" onClick={() => setReloadKey((value) => value + 1)} type="button"><RefreshCw aria-hidden="true" size={14} /> Refresh local readback</button>
      </nav>

      <div className="benchmark-content">
        {search.view === "campaign" && <CampaignView packet={packet} primaryMetric={primaryMetric} />}
        {search.view === "arms" && <ArmsView packet={packet} />}
        {search.view === "cases" && <CasesView onOpenRun={(runId, caseId) => setView("runs", runId, caseId)} packet={packet} primaryMetric={primaryMetric} />}
        {search.view === "runs" && <RunsView onSelectRun={(runId, caseId) => setView("runs", runId, caseId)} packet={packet} selectedRunId={search.runId} selectedCaseId={search.caseId} />}
      </div>

      <footer className="benchmark-footer">
        <div><ShieldCheck aria-hidden="true" size={16} /><span>Scores: {packet.authority.score_source}</span><span>Dashboard cannot launch, grade, or mutate runs.</span></div>
        <code title={sourcePath}>local{sourcePath}</code>
      </footer>
    </main>
  );
}
