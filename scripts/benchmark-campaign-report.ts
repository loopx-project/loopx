/** Read-only campaign report over caller-selected, minimized benchmark projections.
 * Benchmark toolkit owns run/countability authority. This developer helper neither
 * selects attempts nor normalizes native scores, adjudicates faults or launches work.
 */
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, renameSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { pathToFileURL } from 'node:url';

type Status = 'planned' | 'running' | 'completed' | 'cancelled' | 'runner_invalid';
type Fault = 'product' | 'infrastructure' | 'scoring' | 'operator_cancelled' | 'unknown';
type Rerun = Fault | 'comparison_change' | 'integrity_review';
type Score = {
  value: number; normalization_id: string; evidence_ref: string;
  origin: 'official' | 'authorized_failure_zero'; countable: boolean;
};
type Selection = {
  arm_id: string; case_id: string; run_id: string; status: Status;
  score: Score | null; protocol_id: string; exclude_reason: string | null;
  rerun_reason: Rerun | null;
};
type Incident = {
  arm_id: string; case_id: string; run_id: string; fault: Fault;
  initial_attempt: boolean; evidence_ref: string;
};
export type CampaignInput = {
  schema_version: 'benchmark_campaign_report_input_v1';
  observed_at: string; title: string; case_ids: string[];
  arms: { arm_id: string; label: string }[];
  selection: Selection[]; incidents: Incident[]; notes: string[];
};

const statuses: Status[] = ['planned', 'running', 'completed', 'cancelled', 'runner_invalid'];
const faults: Fault[] = ['product', 'infrastructure', 'scoring', 'operator_cancelled', 'unknown'];
const reruns: Rerun[] = [...faults, 'comparison_change', 'integrity_review'];
const requireThat = (condition: unknown, message: string): void => {
  if (!condition) throw new Error(message);
};
const text = (value: unknown): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= 1000 && !value.includes('\0');
const token = (value: unknown): value is string =>
  typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,249}$/.test(value);
const distinct = (values: string[]) => new Set(values).size === values.length;
const terminal = (s: Status) => !['planned', 'running'].includes(s);
const key = (r: { arm_id: string; case_id: string }) => `${r.arm_id}\0${r.case_id}`;
const mean = (v: number[]) => v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
const digest = (v: unknown) => createHash('sha256').update(JSON.stringify(v)).digest('hex');

export function validateInput(raw: unknown): CampaignInput {
  requireThat(raw && typeof raw === 'object', 'input must be an object');
  const d = raw as CampaignInput;
  requireThat(d.schema_version === 'benchmark_campaign_report_input_v1', 'schema mismatch');
  requireThat(text(d.title) && text(d.observed_at) &&
    /(Z|[+-]\d\d:\d\d)$/.test(d.observed_at) && Number.isFinite(Date.parse(d.observed_at)), 'title/time invalid');
  requireThat(Array.isArray(d.case_ids) && d.case_ids.length > 0 && d.case_ids.every(token) && distinct(d.case_ids), 'case ids invalid/duplicate');
  requireThat(Array.isArray(d.arms) && d.arms.length > 0 && d.arms.every(a => token(a.arm_id) && text(a.label)) && distinct(d.arms.map(a => a.arm_id)), 'arms invalid/duplicate');
  const armIds = new Set(d.arms.map(a => a.arm_id));
  const caseIds = new Set(d.case_ids);
  requireThat(Array.isArray(d.selection) && d.selection.length === d.arms.length * d.case_ids.length, 'selection must cover every arm/case exactly once');
  requireThat(distinct(d.selection.map(key)) && distinct(d.selection.map(r => r.run_id)), 'duplicate selection/run id');
  for (const r of d.selection) {
    requireThat(armIds.has(r.arm_id) && caseIds.has(r.case_id) && token(r.run_id), 'unknown selection identity');
    requireThat(statuses.includes(r.status) && token(r.protocol_id), 'invalid status/protocol');
    requireThat(r.exclude_reason === null || text(r.exclude_reason), 'exclude_reason invalid');
    requireThat(r.rerun_reason === null || reruns.includes(r.rerun_reason), 'rerun_reason invalid');
    if (r.score !== null) {
      const s = r.score;
      requireThat(s && typeof s.value === 'number' && Number.isFinite(s.value) && s.value >= 0 && s.value <= 100, 'score must be finite normalized 0..100 or null');
      requireThat(token(s.normalization_id) && text(s.evidence_ref) && typeof s.countable === 'boolean', 'score provenance required');
      requireThat(s.origin === 'official' || (s.origin === 'authorized_failure_zero' && s.value === 0), 'score origin invalid');
      requireThat(!s.countable || (r.status === 'completed' && s.origin === 'official' && r.exclude_reason === null), 'countable score contradicts status/origin/exclusion');
    }
  }
  requireThat(Array.isArray(d.incidents) && distinct(d.incidents.map(i => i.run_id)), 'incident run ids must be unique');
  for (const i of d.incidents) {
    requireThat(armIds.has(i.arm_id) && caseIds.has(i.case_id) && token(i.run_id) && faults.includes(i.fault) &&
      typeof i.initial_attempt === 'boolean' && text(i.evidence_ref), 'invalid incident');
    const selected = d.selection.find(r => r.run_id === i.run_id);
    requireThat(!selected || key(selected) === key(i), 'incident/selection identity mismatch');
  }
  requireThat(Array.isArray(d.notes) && d.notes.every(text), 'notes invalid');
  return d;
}

export function buildReport(raw: unknown) {
  const d = validateInput(raw);
  const selected = new Map(d.selection.map(r => [key(r), r]));
  const summary = d.arms.map(a => {
    const rows = d.selection.filter(r => r.arm_id === a.arm_id);
    const incidents = d.incidents.filter(i => i.arm_id === a.arm_id);
    const counts = (kind: Fault, initial = false) => new Set(incidents.filter(i => i.fault === kind && (!initial || i.initial_attempt)).map(i => i.case_id)).size;
    const pending = Object.fromEntries(reruns.map(kind => [kind, rows.filter(r => r.rerun_reason === kind).length]));
    return { ...a, total: rows.length, ended: rows.filter(r => terminal(r.status)).length,
      running: rows.filter(r => r.status === 'running').length, queued: rows.filter(r => r.status === 'planned').length,
      ended_with_score: rows.filter(r => terminal(r.status) && r.score !== null).length,
      ended_without_score: rows.filter(r => terminal(r.status) && r.score === null).length,
      score_countable: rows.filter(r => r.score?.countable).length,
      product_failures_initial: counts('product', true), product_failures_ever: counts('product'),
      infrastructure_failures_ever: counts('infrastructure'), scoring_failures_ever: counts('scoring'),
      cancelled_cases_ever: counts('operator_cancelled'), unknown_failures_ever: counts('unknown'), pending_reruns: pending };
  });
  const armIds = d.arms.map(a => a.arm_id);
  const groups: string[][] = [];
  for (let i = 0; i < armIds.length; i++) for (let j = i + 1; j < armIds.length; j++) groups.push([armIds[i], armIds[j]]);
  if (armIds.length > 2) groups.push(armIds);
  const comparisons = groups.map(arms => {
    const exclusions: Record<string, string[]> = {};
    const cases = [...d.case_ids].sort().filter(c => {
      const rows = arms.map(a => selected.get(key({ arm_id: a, case_id: c }))!);
      const reasons = rows.flatMap(r => !terminal(r.status) ? [`${r.arm_id}:not_terminal`] :
        r.exclude_reason ? [`${r.arm_id}:${r.exclude_reason}`] : r.score === null ? [`${r.arm_id}:no_score`] :
        r.status === 'cancelled' ? [`${r.arm_id}:${r.status}`] : []);
      if (rows.every(r => r.score) && new Set(rows.map(r => r.score!.normalization_id)).size !== 1) reasons.push('normalization_mismatch');
      if (reasons.length) exclusions[c] = reasons;
      return !reasons.length;
    });
    const scores = (a: string, cs: string[]) => cs.map(c => selected.get(key({arm_id:a,case_id:c}))!.score!.value);
    const qualifiedCases = cases.filter(c => arms.every(a => selected.get(key({arm_id:a,case_id:c}))!.score!.countable));
    const means = Object.fromEntries(arms.map(a => [a, mean(scores(a, cases))]));
    const deltas = arms.length === 2 && cases.length ? cases.map(c => scores(arms[1], [c])[0] - scores(arms[0], [c])[0]) : [];
    return { arms, case_ids: cases, n: cases.length, case_set_sha256: digest(cases), means,
      delta_second_minus_first: arms.length === 2 && cases.length ? means[arms[1]]! - means[arms[0]]! : null,
      second_wins: deltas.filter(x => x > 1e-9).length, ties: deltas.filter(x => Math.abs(x) <= 1e-9).length,
      first_wins: deltas.filter(x => x < -1e-9).length, qualified_common_n: qualifiedCases.length,
      qualified_case_ids: qualifiedCases, exclusions,
      interpretation: 'descriptive_common_case_scores_not_a_causal_or_matched_integrity_claim' };
  });
  return { schema_version: 'benchmark_campaign_report_v1', input_sha256: digest(d),
    observed_at: d.observed_at, title: d.title, summary, comparisons, selection: d.selection,
    incidents: d.incidents, notes: d.notes };
}

type Report = ReturnType<typeof buildReport>;
const cell = (v: unknown) => String(v ?? '—').replaceAll('|', '\\|').replace(/[\r\n]+/g, ' ').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const number = (v: number | null) => v === null ? '—' : v.toFixed(2);
function table(headers: string[], rows: unknown[][]) {
  return ['| ' + headers.map(cell).join(' | ') + ' |', '| ' + headers.map(() => '---').join(' | ') + ' |',
    ...rows.map(r => '| ' + r.map(cell).join(' | ') + ' |')].join('\n');
}
export function renderMarkdown(r: Report): string {
  const labels = new Map(r.summary.map(a => [a.arm_id, a.label]));
  return `# ${cell(r.title)}\n\n更新时间：${cell(r.observed_at)}\n\n` +
    '每组按题目计数；补跑替换当前进度，历史失败另记，不重复累加。\n\n' +
    table(['实验组', '已结束', '正在跑', '待跑', '合计', '已结束且有分', '已结束但无分'], r.summary.map(a => [a.label,a.ended,a.running,a.queued,a.total,a.ended_with_score,a.ended_without_score])) +
    '\n\n历史故障按每组独立题目去重；不同原因可能发生在同一题。下表的待补跑数量包含正在补跑。\n\n' +
    table(['实验组','首次 Loop 故障','历史 Loop 故障','历史基础设施故障','历史调度取消','基础设施待补跑','评分待补跑','取消待补跑','其他待重做'], r.summary.map(a => [a.label,a.product_failures_initial,a.product_failures_ever,a.infrastructure_failures_ever,a.cancelled_cases_ever,a.pending_reruns.infrastructure,a.pending_reruns.scoring,a.pending_reruns.operator_cancelled,(a.pending_reruns.comparison_change ?? 0)+(a.pending_reruns.integrity_review ?? 0)+(a.pending_reruns.product ?? 0)+(a.pending_reruns.unknown ?? 0)])) +
    '\n\n均分使用相同题目交集、相同归一化规则，尺度 0–100。未出分不填 0；授权的故障判零单独保留来源。以下为描述性比较；源版本、协议、资源差异和完整性复核仍需独立审查。\n\n' +
    table(['对比范围','共同题数',...r.summary.map(a => a.label),'第二组−第一组','共同题中双方/各方可计数'],r.comparisons.map(c => [c.arms.map(a => labels.get(a)).join(' / '),c.n,...r.summary.map(a => number(c.means[a.arm_id] ?? null)),number(c.delta_second_minus_first),c.qualified_common_n])) +
    '\n\n' + r.notes.map(n => '- ' + cell(n)).join('\n') + '\n\n## 共同题目与排除记录\n\n' +
    r.comparisons.map(c => `- ${c.arms.map(a => labels.get(a)).join(' / ')}：${c.case_ids.join('、') || '无'}。`).join('\n') +
    '\n\n逐题状态、每项分数来源、排除原因及历史故障均保留在同名 JSON；CSV 每行是一道题的一个组。\n';
}
export function renderCsv(r: Report): string {
  // Neutralize spreadsheet formulas in caller-controlled labels and ids.
  const q = (v: unknown) => { let s = String(v ?? ''); if (/^\s*[=+\-@]/.test(s)) s = "'" + s; return '"' + s.replaceAll('"','""') + '"'; };
  return [['arm_id','case_id','run_id','status','score_0_100','score_origin','normalization_id','countable','rerun_reason','exclude_reason','evidence_ref'],
    ...r.selection.map(s => [s.arm_id,s.case_id,s.run_id,s.status,s.score?.value,s.score?.origin,s.score?.normalization_id,s.score?.countable,s.rerun_reason,s.exclude_reason,s.score?.evidence_ref])].map(row => row.map(q).join(',')).join('\n')+'\n';
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const [input, prefix, ...extra] = process.argv.slice(2);
    requireThat(input && prefix && !extra.length, 'Usage: node --experimental-strip-types scripts/benchmark-campaign-report.ts INPUT.json OUTPUT_PREFIX');
    const report = buildReport(JSON.parse(readFileSync(input, 'utf8')));
    mkdirSync(dirname(resolve(prefix)), { recursive: true });
    const files = {json: JSON.stringify(report,null,2)+'\n', md:renderMarkdown(report), csv:renderCsv(report)};
    for (const [ext, body] of Object.entries(files)) {
      const path = `${prefix}.${ext}`; const temporary = `${path}.${process.pid}.tmp`;
      writeFileSync(temporary,body,{mode:0o600}); renameSync(temporary,path);
    }
    process.stdout.write(JSON.stringify({ok:true,input_sha256:report.input_sha256,files:Object.keys(files).map(ext => `${prefix}.${ext}`)})+'\n');
  } catch (error) { process.stderr.write(String(error)+'\n'); process.exitCode=1; }
}
