import { useEffect, useRef, useState } from 'react';
import type { AgentInfo, Api } from '../api';
import { copyText } from '../clipboard';
import { compactLabel, durationLabel, numberLabel, type AgentDiagnostic, type UsageReport, type UsageSample } from '../observability';
import { Popover } from './Popover';
import { TokenHeatmap } from './TokenHeatmap';
import { TpmChart } from './TpmChart';
import { UsageCost } from './UsageCost';
import { IconCheck, IconCopy, IconRefresh, IconChevron } from './icons';
import { formatDateTime, t } from '../i18n';

const time = (s: string) => formatDateTime(s, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const cost = (value: UsageSample['cost']) => value ? `${value.estimated ? `${t('obs.estimated')} ` : ''}${value.currency} ${value.amount.toFixed(4)}` : t('common.unknown');
const scope = { request: t('obs.scope.request'), turn: t('obs.scope.turn'), update: t('obs.scope.update') };
const status: Record<string, string> = { running: t('status.running'), done: t('step.done'), error: t('status.error'), cancelled: t('team.stopped') };

function Select({ label, value, options, onPick }: { label: string; value: string; options: Array<{ id: string; label: string }>; onPick: (id: string) => void }) {
  return <Popover label={label} triggerClassName="text-btn obs-select" triggerLabel={label} trigger={<>{options.find(o => o.id === value)?.label ?? label}<IconChevron size={12} open /></>}>
    {close => <><div className="pop-title">{label}</div>{options.map(option => <button key={option.id} type="button" role="menuitemradio" aria-checked={option.id === value} className="pop-item compact" onClick={() => { onPick(option.id); close(); }}>
      <span className="pop-item-text"><span className="pop-item-label">{option.label}</span></span>
      {option.id === value && <span className="pop-item-check"><IconCheck size={16} /></span>}
    </button>)}</>}
  </Popover>;
}

/** Big counts read as 1.03M; hovering shows the exact digits. */
function Count({ values }: { values: Array<number | null | undefined> }) {
  return <strong title={values.map(numberLabel).join(' / ')}>{values.map(compactLabel).join(' / ')}</strong>;
}

function Sample({ sample }: { sample: UsageSample }) {
  return <div className="obs-sample">
    <div className="obs-sample-head"><strong>{scope[sample.scope]}{sample.timing === 'estimated' ? ` · ${t('chart.estimated')}` : ''}</strong><span>{sample.model ?? t('obs.modelUnknown')}{sample.provider ? ` · ${sample.provider}` : ''}</span></div>
    <dl className="obs-details">
      {[
        [t('obs.input'), numberLabel(sample.input)], [t('obs.output'), numberLabel(sample.output)], [t('obs.totalTokens'), numberLabel(sample.total)],
        [t('obs.cacheRead'), numberLabel(sample.cacheRead)], [t('obs.cacheWrite'), numberLabel(sample.cacheWrite)], [t('obs.reasoning'), numberLabel(sample.reasoning)],
        [t('obs.cost'), cost(sample.cost)], [t('obs.duration'), durationLabel(sample.durationMs)], [t('obs.ttft'), durationLabel(sample.ttftMs)],
        [t('obs.account'), sample.account ?? t('obs.notReported')],
      ].map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}
    </dl>
    {sample.error && <p className="obs-error">{sample.error}</p>}
  </div>;
}

function UsageView({ api, agents }: { api: Api; agents: AgentInfo[] }) {
  const [hours, setHours] = useState('24');
  const [agent, setAgent] = useState('');
  const [metric, setMetric] = useState<'total' | 'output'>('total');
  const [report, setReport] = useState<UsageReport | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let alive = true, pending = false;
    setReport(null); setError('');
    const read = async () => {
      if (pending) return;
      pending = true;
      try { const data = await api.usageReport(Number(hours), agent); if (alive) { setReport(data); setError(''); } }
      catch { if (alive) setError(t('obs.loadFailed')); }
      finally { pending = false; }
    };
    void read();
    const timer = setInterval(() => { if (!document.hidden) void read(); }, 10000);
    return () => { alive = false; clearInterval(timer); };
  }, [api, hours, agent, refresh]);
  return <>
    <div className="obs-toolbar">
      <Select label={t('activity.range')} value={hours} options={[{ id: '1', label: t('obs.lastHour') }, { id: '24', label: t('obs.last24h') }, { id: '168', label: t('obs.last7d') }]} onPick={setHours} />
      <Select label={t('obs.instance')} value={agent} options={[{ id: '', label: t('obs.allInstances') }, ...agents.map(a => ({ id: a.id, label: a.label }))]} onPick={setAgent} />
      <Select label={t('obs.metric')} value={metric} options={[{ id: 'total', label: t('obs.metricTotal') }, { id: 'output', label: t('obs.metricOutput') }]} onPick={id => setMetric(id as 'total' | 'output')} />
      <button className="icon-btn" aria-label={t('obs.refresh')} onClick={() => setRefresh(n => n + 1)}><IconRefresh size={16} /></button>
    </div>
    {error && <p role="alert" className="obs-error">{error}</p>}
    {!report && !error && <p className="panel-empty">{t('obs.loading')}</p>}
    {report && <>
      <div className="obs-metrics">
        <div className="obs-metric obs-primary"><span>{metric === 'output' ? t('chart.outputTpm') : 'TPM'} · {t('obs.last60s')}</span><Count values={[metric === 'output' ? report.outputTpm : report.tpm]} /><small>{t('obs.perMinute')} · <span className="obs-token-unit">{t('obs.units')}</span></small></div>
        <div className="obs-metric"><span>{t('obs.totalTokens')}</span><Count values={[report.totals.total]} /><small>{t('obs.samples', report.measuredSamples)}</small></div>
        <div className="obs-metric"><span>{t('obs.inputOutput')}</span><Count values={[report.totals.input, report.totals.output]} /><small>{t('obs.inputIncludesCache')}</small></div>
        <div className="obs-metric"><span>{t('obs.cacheReadWrite')}</span><Count values={[report.totals.cacheRead, report.totals.cacheWrite]} /><small>{t('obs.unknownExcluded')}</small></div>
        {report.costSummary ? <UsageCost summary={report.costSummary} api={api} hours={Number(hours)} agent={agent} />
          : <div className="obs-metric"><span>{t('obs.reportedCost')}</span><strong className="obs-cost">{Object.entries(report.costs).map(([currency, v]) => cost({ currency, ...v })).join(' · ') || t('common.unknown')}</strong><small>{t('obs.costOnly')}</small></div>}
        <div className="obs-metric"><span>{t('obs.turnsErrors')}</span><strong>{report.turns} / {report.errors}</strong><small>{t('obs.turnHint')}</small></div>
      </div>
      {report.series && <TpmChart series={report.series} agents={agents} metric={metric} empty={metric === 'output' ? !report.outputMeasuredSamples : !report.measuredSamples} />}
      <TokenHeatmap api={api} agent={agent} metric={metric} refresh={refresh} />
      <p className="obs-note">{t('obs.note')}</p>
      <div className="obs-list-title"><h3>{t('obs.recent')}</h3><span>{t('quota.updatedAt', time(report.checkedAt))} · {t('obs.max100')}</span></div>
      {!report.rows.length && <p className="panel-empty">{t('obs.noRows')}</p>}
      {report.rows.map(row => <details className="obs-turn" key={`${row.threadId}:${row.turnId}`}>
        <summary><div><strong>{row.title}</strong><span>{agents.find(a => a.id === row.agent)?.label ?? row.agent} · {time(row.startedAt)}</span></div><span className={row.status === 'error' ? 'obs-error' : ''}>{status[row.status] ?? row.status}</span></summary>
        <div className="obs-turn-body">
          <p className="obs-note">{t('obs.rowModel', row.configuredModel ?? t('obs.nativeDefault'), row.linkedAccount ?? t('obs.unmarked'))}<br />{t('obs.firstOutput', durationLabel(row.firstOutputMs))}</p>
          {row.error && <p className="obs-error">{row.error}</p>}
          {row.samples.length ? row.samples.map(s => <Sample key={s.id} sample={s} />) : <p className="obs-note">{t('obs.noSamples')}</p>}
        </div>
      </details>)}
    </>}
  </>;
}

const states: Record<string, string> = { ready: t('diag.ready'), cli_only: t('diag.cliOnly'), missing: t('diag.missing'), empty: t('diag.empty'), authentication: t('diag.authentication'), unsupported: t('diag.unsupported'), timeout: t('diag.timeout'), failed: t('diag.failed') };
const capabilities: Array<[string, string]> = [['resume', t('diag.cap.resume')], ['approvals', t('diag.cap.approvals')], ['images', t('diag.cap.images')], ['history', t('diag.cap.history')], ['fork', t('diag.cap.fork')], ['compact', t('diag.cap.compact')], ['quota', t('diag.cap.quota')], ['coordination', t('diag.cap.coordination')], ['goal', t('diag.cap.goal')]];

function DiagnosticRow({ api, agent, onResult }: { api: Api; agent: AgentInfo; onResult?: (data: AgentDiagnostic) => void }) {
  const [data, setData] = useState<AgentDiagnostic | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [copied, setCopied] = useState(false);
  const result = useRef(onResult); result.current = onResult;
  useEffect(() => {
    let alive = true;
    setLoading(true); setError(''); setCopied(false);
    void api.agentDiagnostic(agent.id, refresh > 0).then(value => { if (alive) { setData(value); result.current?.(value); } }).catch(() => {
      if (alive) setError(t('diag.loadFailed'));
    }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [api, agent.id, refresh]);
  return <article className="obs-diagnostic">
    <header><div><h3>{agent.label}</h3><span>{loading ? t('diag.checking') : data ? states[data.state] ?? data.state : t('diag.notRead')}</span></div>
      <div className="obs-actions"><button className="icon-btn" aria-label={t('diag.refresh', agent.label)} disabled={loading} onClick={() => setRefresh(n => n + 1)}><IconRefresh size={16} /></button>
        <button className="icon-btn" aria-label={t('diag.copy', agent.label)} disabled={!data} onClick={async () => setCopied(await copyText(JSON.stringify(data, null, 2)))}>{copied ? <IconCheck size={16} /> : <IconCopy size={16} />}</button></div>
    </header>
    {error && <p className="obs-error" role="alert">{error}</p>}
    {data && <>
      <p>{data.message}</p>
      <dl className="obs-details"><div><dt>{t('diag.version')}</dt><dd>{data.version ?? t('diag.versionUnknown')}</dd></div><div><dt>{t('diag.protocol')}</dt><dd>{data.protocol ?? t('diag.protocolUnknown')}</dd></div><div><dt>{t('diag.models')}</dt><dd>{data.capabilities.modelCatalog === false ? t('diag.unsupportedCap') : data.models.length}</dd></div><div><dt>{t('diag.checkedAt')}</dt><dd>{time(data.checkedAt)}</dd></div></dl>
      <div className="obs-capabilities">{capabilities.map(([key, label]) => <span key={key} className={(data.capabilities as unknown as Record<string, unknown>)[key] ? 'supported' : ''}>{label} · {(data.capabilities as unknown as Record<string, unknown>)[key] ? t('diag.supported') : t('diag.unsupportedCap')}</span>)}</div>
      {data.models.length > 0 && <details><summary>{t('diag.modelList')}</summary><div className="obs-models">{data.models.map(m => <span key={m.id}>{m.label}</span>)}</div></details>}
    </>}
  </article>;
}

export function ObservabilityPage({ api, agents, initial = 'usage', focusAgent, onResult }: { api: Api; agents: AgentInfo[]; initial?: 'usage' | 'diagnostics'; focusAgent?: string; onResult?: (data: AgentDiagnostic) => void }) {
  const [tab, setTab] = useState(initial);
  const ordered = [...agents].sort((a, b) => Number(b.id === focusAgent) - Number(a.id === focusAgent));
  return <section className="obs-page">
    <h1>{t('obs.title')}</h1>
    <nav className="obs-tabs" aria-label={t('obs.tabs')}>{([['usage', t('obs.tab.usage')], ['diagnostics', t('obs.tab.diagnostics')]] as const).map(([id, label]) => <button key={id} className={tab === id ? 'selected' : ''} aria-pressed={tab === id} onClick={() => setTab(id)}>{label}</button>)}</nav>
    <div className="obs-body">{tab === 'usage' ? <UsageView api={api} agents={agents} /> : <>
      <p className="obs-note">{t('diag.note')}</p>
      {ordered.map(agent => <DiagnosticRow key={agent.id} api={api} agent={agent} onResult={onResult} />)}
    </>}</div>
  </section>;
}
