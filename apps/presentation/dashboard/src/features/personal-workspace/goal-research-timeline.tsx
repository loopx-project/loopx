import {useLayoutEffect, useRef, useState, type CSSProperties} from "react";
import type {ExploreTimeline, TimelineFinding, TimelineNode, TimelinePhase} from "../../../../../../loopx/control_plane/presentation/explore_timeline.js";
import {exploreTimelinePhaseAt, groupExploreTimelinePoints, selectExploreTimelineDecisions} from "../../../../../../loopx/control_plane/presentation/explore_timeline.js";
import type {groupUnlinkedHistoryQuestions} from "../../../../../../loopx/control_plane/presentation/explore_history.js";
import {ContextHelp} from "./context-help";

/** Default whole-history reading: thirds, recording density, recorded routes,
 * unlinked questions and explicit decisions on one record-time axis.
 * Position means first record time only; it never encodes progress or cause. */
type Copy = Record<string, string>;
type Props = {
  timeline: ExploreTimeline; zh: boolean; selectedIds: readonly string[]; phase: number | null;
  selectedDay: string | null; dayGroups: ReturnType<typeof groupUnlinkedHistoryQuestions>["groups"]; onGroupSelect: (day: string) => void;
  onSelect: (id: string) => void; onSelectMany: (ids: string[]) => void; onPhase: (index: number | null) => void;
  timeLabel: (value: number) => string; dayLabel: (value: number) => string;
};

const LABELS = ["confirmed", "refuted", "tentative"] as const;
const tone = (label: string) => LABELS.includes(label as typeof LABELS[number]) ? `is-${label}` : "is-unlabeled";
const isOpen = (item: TimelineNode) => ["open", "exploring", "blocked"].includes(item.node.latest.status);

export function GoalResearchTimeline({timeline, zh, selectedIds, selectedDay, dayGroups, onGroupSelect, phase, onSelect, onSelectMany, onPhase, timeLabel, dayLabel}: Props) {
  const plot = useRef<HTMLDivElement>(null);
  const [plotWidth, setPlotWidth] = useState(0);
  useLayoutEffect(() => {
    const element = plot.current;
    if (!element) return;
    const measure = () => setPlotWidth(element.getBoundingClientRect().width);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [timeline.start, timeline.end]);
  const copy: Copy = zh ? {
    early: "前段", middle: "中段", late: "后段", items: "研究项", findings: "结论",
    decisions: "显式决策", none: "未记录显式决策", changed: "变化", stopped: "停止", retained: "保留",
    unstructured: "决策未结构化", unstructuredHint: "结论未按 writeback 结构记录，无法拆出依据与决策。",
    density: "记录密度", densityHint: "柱高为事件数；浅红为无记录空档", ticks: "转向 / 继续",
    routes: "研究路线", routeHint: "仅由显式关联或父子分组相连", singles: "未关联研究项",
    trail: "决策轨迹", more: "显示更多决策", all: "全部时段",
    span: "记录跨度", start: "起", end: "止", replan: "转向", continue: "继续", stop: "停止", wait: "等待", no_change: "无变化", ask_human: "请人决定",
    confirmed: "来源记为确认", refuted: "已反驳", tentative: "待检查", unlabeled: "无结论", phaseNote: "按记录时间三等分，仅为阅读分组，不是预声明的评估窗口。",
    noTime: "没有可用的记录时间，无法放上时间轴；下方记录仍可完整阅读。",
  } : {
    early: "Early", middle: "Middle", late: "Late", items: "Research items", findings: "Findings",
    decisions: "Explicit decisions", none: "No explicit decision recorded", changed: "Changed", stopped: "Stopped", retained: "Retained",
    unstructured: "Unstructured decisions", unstructuredHint: "Findings were not recorded in writeback sections; evidence and decisions cannot be separated.",
    density: "Recording density", densityHint: "Bar height is events; pale red is a recording gap", ticks: "Replan / continue",
    routes: "Research routes", routeHint: "Connected only by recorded relationships or parent grouping", singles: "Unlinked research items",
    trail: "Decision history", more: "Show more decisions", all: "Whole history",
    span: "Recorded span", start: "From", end: "To", replan: "Replan", continue: "Continue", stop: "Stop", wait: "Wait", no_change: "No change", ask_human: "Ask human",
    confirmed: "Recorded confirmation", refuted: "Refuted", tentative: "Tentative", unlabeled: "No finding", phaseNote: "Equal thirds of record time, for reading only; not a pre-declared evaluation window.",
    noTime: "No usable record time; the history cannot be placed on a time axis. All records remain readable below.",
  };
  const {start, end} = timeline;
  if (start === null || end === null) return <p className="research-history-note">{copy.noTime}</p>;
  const span = Math.max(1, end - start);
  const pct = (at: number) => `${((at - start) / span) * 100}%`;
  const inPhase = (at: number | null) => phase === null || exploreTimelinePhaseAt(timeline.phases, at) === phase;
  const label = (key: string) => copy[key] ?? key;
  const hours = (from: number, to: number) => { const value = (to - from) / 3_600_000; return value >= 48 ? `${(value / 24).toFixed(1)} ${zh ? "天" : "d"}` : `${value.toFixed(1)} ${zh ? "小时" : "h"}`; };
  const maxEvents = Math.max(1, ...timeline.bins.map(bin => bin.events));
  const hasRecordingGaps = timeline.bins.some(bin => !bin.events);
  const ticks: number[] = [];
  const midnight = new Date(start); midnight.setHours(24, 0, 0, 0);
  for (let at = midnight.getTime(); at <= end; at += 86_400_000) ticks.push(at);
  const stride = Math.max(1, Math.ceil(ticks.length / 10));
  const marker = (item: TimelineNode, style: CSSProperties, extra = "") => {
    const title = `${item.node.latest.title} · ${label(item.label || "unlabeled")}${item.first !== null ? ` · ${timeLabel(item.first)}` : ""}`;
    return <button key={item.node.id} type="button" title={title} aria-label={title} aria-pressed={selectedIds.includes(item.node.id)} data-research-nodes={item.node.id}
      className={`research-timeline-marker ${tone(item.label)}${isOpen(item) ? " is-open" : ""}${selectedIds.includes(item.node.id) ? " is-selected" : ""}${inPhase(item.first) ? "" : " is-dimmed"}${extra}`}
      style={style} onClick={() => onSelect(item.node.id)}/>;
  };
  const points = (items: readonly TimelineNode[], extra = "") => groupExploreTimelinePoints(items, start, end, plotWidth).map(group => {
    if (group.items.length === 1) return marker(group.items[0]!, {left: pct(group.at)}, extra);
    const ids = group.items.map(item => item.node.id);
    const selected = ids.some(id => selectedIds.includes(id));
    const title = zh ? `${ids.length} 项相邻研究 · ${timeLabel(group.items[0]!.first!)} — ${timeLabel(group.items.at(-1)!.first!)} · 展开全部记录与依据`
      : `${ids.length} nearby research items · ${timeLabel(group.items[0]!.first!)} — ${timeLabel(group.items.at(-1)!.first!)} · Read every record and source`;
    return <button key={ids[0]} type="button" title={title} aria-label={title} aria-pressed={selected} data-research-nodes={ids.join(" ")}
      className={`research-timeline-marker is-cluster${selected ? " is-selected" : ""}${group.items.some(item => inPhase(item.first)) ? "" : " is-dimmed"}`}
      style={{left: pct(group.at)}} onClick={() => onSelectMany(ids)}><span>{ids.length}</span></button>;
  });
  const trail = selectExploreTimelineDecisions(timeline, phase);
  const decisionOutcomes = [...new Set(timeline.decisions.map(item => item.sections!.decision!.outcome))];
  const groupedIds = new Set(dayGroups.flatMap(group => group.nodes.map(node => node.id)));
  const swarm = timeline.singles.filter(id => !groupedIds.has(id)).map(id => timeline.nodes.get(id)!).filter(item => item.first !== null);
  const unknownTime = [...timeline.nodes.values()].filter(item => item.first === null).length;
  const phaseCard = (item: TimelinePhase) => {
    const counts = Object.entries(item.decisions);
    return <button key={item.index} type="button" aria-pressed={phase === item.index} onClick={() => onPhase(phase === item.index ? null : item.index)}
      className={`research-phase${phase === item.index ? " is-active" : ""}`}>
      <span className="research-phase-head"><strong>{[copy.early, copy.middle, copy.late][item.index]}</strong><time>{timeLabel(item.start)} — {timeLabel(item.end)}</time></span>
      <span className="research-phase-counts"><b>{item.nodes}</b> {copy.items}<b>{item.findings}</b> {copy.findings}</span>
      <span className="research-phase-bar" aria-label={LABELS.map(key => `${label(key)} ${item.labels[key] ?? 0}`).join(" · ")}>
        {LABELS.map(key => item.labels[key] ? <span key={key} className={tone(key)} style={{flexGrow: item.labels[key]}}>{item.labels[key]}</span> : null)}
      </span>
      {timeline.decisions.length ? <span className="research-phase-decisions">{counts.length ? `${copy.decisions} · ${counts.map(([key, value]) => `${label(key)} ${value}`).join(" · ")}` : (timeline.structuredCount ? copy.none : copy.unstructured)}</span> : null}
    </button>;
  };
  return <div className="research-timeline">
    <div className="research-timeline-guide"><span>{zh ? "记录时间" : "Record time"}</span><ContextHelp label={zh ? "时间线读法" : "Reading the timeline"}>
      <p>{copy.phaseNote}</p><p>{copy.densityHint}{zh ? "。" : ". "}{zh ? "无记录空档不代表研究停止。" : "A recording gap does not establish that research stopped."}</p>
      {!timeline.structuredCount ? <p>{copy.unstructuredHint}</p> : null}
      <p>{copy.routeHint}{zh ? "。" : ". "}{zh ? "相邻点合拢，数字为研究项数，一次展开原始时间与依据。" : "Nearby points group; the number counts research items. Select once for original times and sources."}</p>
      <p>{zh ? "未关联问题按首次记录 UTC 日期收纳，不推断路线或因果。决策轨迹保留各轮显式决策，最新记录在前。" : "Unlinked questions group by first-record UTC date, without inferring a route or cause. Decision history retains each explicit decision, newest record first."}</p>
    </ContextHelp>{phase !== null ? <button type="button" className="research-link" onClick={() => onPhase(null)}>{copy.all}</button> : null}</div>
      <span className="research-phase-legend">{LABELS.map(key => <span key={key}><i className={tone(key)} aria-hidden="true"/>{label(key)}</span>)}</span>
    <div className="research-phases" role="group" aria-label={copy.phaseNote}>{timeline.phases.map(phaseCard)}</div>
    <div className="research-timeline-grid">
      <div className="research-row-label"><strong>{copy.density}</strong><small>
        <span><i className="research-bar-key" aria-hidden="true"/> {zh ? "柱高=事件数" : "Bar height = events"}</span>
        {hasRecordingGaps ? <span><i className="research-gap-key" aria-hidden="true"/> {zh ? "无记录" : "No records"}</span> : null}
        {decisionOutcomes.map(outcome => <span key={outcome}><i className={`research-tick-key is-${outcome}`}/> {label(outcome)}</span>)}
      </small></div>
      <div className="research-row-plot">
        <div className="research-density" ref={plot}>{timeline.bins.map((bin, index) => <span key={index} className={bin.events ? "" : "is-gap"} title={`${timeLabel(bin.start)} — ${timeLabel(bin.end)} · ${bin.events}`}
          style={{height: bin.events ? `${Math.max(8, (bin.events / maxEvents) * 100)}%` : "100%"}}/>)}</div>
        {timeline.decisions.length ? <div className="research-decision-ticks">{timeline.decisions.filter(item => item.at !== null).map(item => <span key={item.event.event_id}
          title={`${label(item.sections!.decision!.outcome)} · ${item.at === null ? "—" : timeLabel(item.at)}`}
          className={`is-${item.sections!.decision!.outcome}${inPhase(item.at) ? "" : " is-dimmed"}`} style={{left: pct(item.at!)}}/>)}</div> : null}
        {timeline.phases.slice(1).map(item => <span key={item.index} className="research-phase-divider" style={{left: pct(item.start)}}/>)}
        <div className="research-axis">{ticks.map((at, index) => index % stride ? null : <span key={at} style={{left: pct(at)}}>{dayLabel(at)}</span>)}</div>
      </div>
      {timeline.routes.length ? <div className="research-row-heading"><strong>{copy.routes}</strong></div> : null}
      {timeline.routes.map(route => {
        const members = route.nodeIds.map(id => timeline.nodes.get(id)!).filter(item => item.first !== null);
        if (!members.length) return null;
        const tally = LABELS.map(key => [key, members.filter(item => item.label === key).length] as const).filter(([, value]) => value);
        const first = members[0]!, last = members.at(-1)!;
        const position = new Map(members.map(item => [item.node.id, item.first ?? start]));
        const x = (id: string) => ((position.get(id)! - start) / span) * 1000;
        const arcs = [
          ...[...timeline.nodes.values()].filter(item => item.routeId === route.id && position.has(item.node.id) && item.node.latest.parent_id && position.has(item.node.latest.parent_id))
            .map(item => ({key: `parent:${item.node.id}`, from: item.node.latest.parent_id, to: item.node.id, kind: "is-grouping", title: ""})),
          ...timeline.edges.filter(edge => position.has(edge.from_node) && position.has(edge.to_node))
            .map(edge => ({key: edge.event_id, from: edge.from_node, to: edge.to_node, kind: edge.edge_type === "refutes" ? "is-refutation" : "", title: `${edge.edge_type}: ${edge.summary}`})),
        ];
        return <div className="research-route" key={route.id} role="group" aria-label={`${members.length} · ${first.node.latest.title}`}>
          <div className="research-row-label"><strong>{members.length} {zh ? "项研究" : "research items"}</strong>
            <small>{dayLabel(route.first!)} – {dayLabel(route.last!)} · {copy.span} {hours(route.first!, route.last!)}</small>
            <small>{tally.map(([key, value]) => `${label(key)} ${value}`).join(" · ") || copy.unlabeled}</small></div>
          <div className="research-row-plot">
            <p className="research-route-caption"><span>{copy.start}</span>{first.node.latest.title}{members.length > 1 ? <><span>{copy.end}</span><b className={tone(last.label)}>{last.node.latest.title}</b></> : null}</p>
            <div className="research-route-track">
              <svg viewBox="0 0 1000 44" preserveAspectRatio="none" aria-hidden="true" className="research-history-edges">
                <line x1={x(first.node.id)} x2={x(last.node.id)} y1="32" y2="32" className="research-route-line"/>
                {arcs.map(arc => { const a = Math.min(x(arc.from), x(arc.to)), b = Math.max(x(arc.from), x(arc.to)), h = Math.min(24, 8 + (b - a) / 8);
                  return <g key={arc.key}><path vectorEffect="non-scaling-stroke" className={arc.kind} d={`M${a},26 C${a},${26 - h} ${b},${26 - h} ${b},26`}>{arc.title ? <title>{arc.title}</title> : null}</path></g>; })}
              </svg>
              {points(members)}
            </div>
          </div>
        </div>;
      })}
      {dayGroups.length ? <>
        <div className="research-row-label"><strong>{copy.singles} · {groupedIds.size}</strong></div>
        <div className="research-unlinked-days">{dayGroups.map(group => <button type="button" key={group.day} aria-pressed={selectedDay === group.day}
          className={`${selectedDay === group.day ? "is-selected" : ""}${group.nodes.some(node => inPhase(timeline.nodes.get(node.id)!.first)) ? "" : " is-dimmed"}`}
          onClick={() => onGroupSelect(group.day)}><time>{group.day || (zh ? "时间未知" : "Unknown time")}</time><strong>{group.nodes.length} {zh ? "个问题" : "questions"}</strong></button>)}</div>
      </> : null}
      {swarm.length ? <>
        <div className="research-row-label"><strong>{copy.singles} · {swarm.length}</strong></div>
        <div className="research-row-plot"><div className="research-swarm">
          {points(swarm, " is-small")}
        </div></div>
      </> : null}
    </div>
    {unknownTime ? <div className="research-history-note"><p>{zh ? "缺少记录时间的问题未放上时间轴" : "Questions without a record time are not placed on the axis"} · {unknownTime}</p>
      {[...timeline.nodes.values()].filter(item => item.first === null).map(item => <button key={item.node.id} type="button" className="research-link" onClick={() => onSelect(item.node.id)}>{item.node.latest.title}</button>)}
    </div> : null}
    <section className="research-trail" aria-label={copy.trail}>
      <h3>{copy.trail}</h3>
      {!timeline.decisions.length ? <p className="research-history-note">{timeline.structuredCount ? copy.none : copy.unstructured}</p>
        : <TrailList items={trail} copy={copy} label={label} timeLabel={timeLabel} selectedIds={selectedIds} onSelect={onSelect}/>}
    </section>
  </div>;
}

function TrailList({items, copy, label, timeLabel, selectedIds, onSelect}: {items: TimelineFinding[]; copy: Copy; label: (key: string) => string; timeLabel: (value: number) => string; selectedIds: readonly string[]; onSelect: (id: string) => void}) {
  const [limit, setLimit] = useState(3);
  return <>
    <ol className="research-trail-list">{items.slice(0, limit).map(item => {
      const decision = item.sections!.decision!;
      return <li key={item.event.event_id}>
        <button type="button" aria-pressed={selectedIds.includes(item.event.node_id)} className={selectedIds.includes(item.event.node_id) ? "is-selected" : ""} onClick={() => onSelect(item.event.node_id)}>
          <span className="research-trail-meta"><b className={`research-outcome is-${decision.outcome}`}>{label(decision.outcome)}</b><time>{item.at !== null ? timeLabel(item.at) : ""}</time><span>{label(item.event.status)}</span></span>
          <strong>{item.event.title}</strong>
          <span><em>{copy.changed}</em>{decision.changed.join("；") || "—"}</span>
        </button></li>;
    })}</ol>
    {items.length > limit ? <button type="button" className="research-link" onClick={() => setLimit(value => value + 6)}>{copy.more} · {items.length - limit}</button> : null}
  </>;
}
