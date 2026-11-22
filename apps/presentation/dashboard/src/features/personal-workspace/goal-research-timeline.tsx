import {exploreFindingCopy} from "./explore-finding-copy";
import {ChevronRight, CircleHelp} from "lucide-react";
import {useLayoutEffect, useRef, useState, type CSSProperties} from "react";
import type {ExploreTimeline, TimelineNode, TimelinePhase} from "../../../../../../loopx/control_plane/presentation/explore_timeline.js";
import {exploreTimelinePhaseAt, exploreTimelineTickOffsets, groupExploreTimelinePoints, groupExploreTimelineDecisions} from "../../../../../../loopx/control_plane/presentation/explore_timeline.js";
import type {groupUnlinkedHistoryQuestions} from "../../../../../../loopx/control_plane/presentation/explore_history.js";
import {ContextHelp} from "./context-help";

/** Default whole-history reading: thirds, recording density, recorded routes,
 * unlinked questions and explicit decisions on one record-time axis.
 * Position means first record time only; it never encodes progress or cause. */
type Copy = Record<string, string>;
type Props = {
  timeline: ExploreTimeline; complete: boolean; zh: boolean; selectedIds: readonly string[]; selectedEventId?: string; phase: number | null;
  selectedDay: string | null; dayGroups: ReturnType<typeof groupUnlinkedHistoryQuestions>["groups"]; onGroupSelect: (day: string, button: HTMLButtonElement) => void;
  onSelect: (id: string, finding?: string, event?: string) => void; onSelectMany: (ids: string[], button: HTMLButtonElement) => void; onPhase: (index: number | null) => void;
  timeLabel: (value: number, precise?: boolean) => string; dayLabel: (value: number) => string;
};

const LABELS = ["confirmed", "refuted", "tentative"] as const;
const tone = (label: string) => LABELS.includes(label as typeof LABELS[number]) ? `is-${label}` : "is-unlabeled";
const isOpen = (item: TimelineNode) => ["open", "exploring", "blocked"].includes(item.node.latest.status);

function elapsedLabel(offset: number, zh: boolean) {
  if (offset > 0 && offset < 1_000) return `${offset}${zh ? " 毫秒" : "ms"}`;
  let milliseconds = offset;
  const units: [number, string][] = zh
    ? [[86_400_000, " 天"], [3_600_000, " 小时"], [60_000, " 分"]]
    : [[86_400_000, "d"], [3_600_000, "h"], [60_000, "m"]];
  const parts = [];
  for (const [size, unit] of units) {
    const count = Math.floor(milliseconds / size);
    if (count) parts.push(`${count}${unit}`);
    milliseconds %= size;
  }
  if (milliseconds) parts.push(`${milliseconds / 1_000}${zh ? " 秒" : "s"}`);
  return parts.join(" ") || (zh ? "0 秒" : "0s");
}

export function GoalResearchTimeline({timeline, complete, zh, selectedIds, selectedEventId, selectedDay, dayGroups, onGroupSelect, phase, onSelect, onSelectMany, onPhase, timeLabel, dayLabel}: Props) {
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
  const findingCopy = exploreFindingCopy(zh);
  const copy: Copy = zh ? {
    early: "前段", middle: "中段", late: "后段", item: "个有记录的研究项", items: "个有记录的研究项", question: "个问题", questions: "个问题",
    decisions: "显式决策", none: "未记录显式决策", changed: "变化", stopped: "停止", retained: "保留",
    unstructured: "决策未结构化", unstructuredHint: "结论未按 writeback 结构记录，无法拆出依据与决策。",
    density: "记录密度", densityHint: "柱高为事件数；浅红为无记录空档", axis: "自首条记录起", gap: "无记录",
    partialAxis: "自最早的已读记录起", partialDensityHint: "柱高为已读事件数", partialGap: "无已读记录",
    routes: "研究路线", routeHint: "仅由显式关联或父子分组相连", singles: "未关联研究项",
    trail: "决策轨迹", more: "显示更早记录", moreQuestions: "显示更多问题", record: "条决策记录", records: "条决策记录", unknownTime: "时间未知", all: "全部时段",
    span: "记录跨度", start: "起点", end: "末端", replan: "转向", continue: "继续", stop: "停止", wait: "等待", no_change: "无变化", ask_human: "请人决定",
    ...findingCopy.labels, unlabeled: "无结论", phaseNote: "按记录时间三等分，仅为阅读分组，不是预声明的评估窗口。",
    noTime: "没有可用的记录时间，无法放上时间轴；下方记录仍可完整阅读。",
    whole: "全程",
  } : {
    early: "Early", middle: "Middle", late: "Late", item: "research item with records", items: "research items with records", question: "question", questions: "questions",
    decisions: "Explicit decisions", none: "No explicit decision recorded", changed: "Changed", stopped: "Stopped", retained: "Retained",
    unstructured: "Unstructured decisions", unstructuredHint: "Findings were not recorded in writeback sections; evidence and decisions cannot be separated.",
    density: "Recording density", densityHint: "Bar height is events; pale red is a recording gap", axis: "Since first record", gap: "No records",
    partialAxis: "Since earliest loaded record", partialDensityHint: "Bar height counts loaded events", partialGap: "No loaded records",
    routes: "Research routes", routeHint: "Connected only by recorded relationships or parent grouping", singles: "Unlinked research items",
    trail: "Decision history", more: "Show earlier records", moreQuestions: "Show more questions", record: "decision record", records: "decision records", unknownTime: "Unknown time", all: "Whole history",
    span: "Recorded span", start: "Start", end: "End", replan: "Replan", continue: "Continue", stop: "Stop", wait: "Wait", no_change: "No change", ask_human: "Ask human",
    ...findingCopy.labels, unlabeled: "No finding", phaseNote: "Equal thirds of record time, for reading only; not a pre-declared evaluation window.",
    noTime: "No usable record time; the history cannot be placed on a time axis. All records remain readable below.",
    whole: "Whole run",
  };
  const {start, end} = timeline;
  if (start === null || end === null) return <ContextHelp label={zh ? "记录时间未知" : "Record time unknown"} icon={CircleHelp}><p className="research-history-note">{copy.noTime}</p></ContextHelp>;
  const axisLabel = complete ? copy.axis : copy.partialAxis;
  const elapsed = end - start;
  const span = Math.max(1, elapsed);
  const pct = (at: number) => `${((at - start) / span) * 100}%`;
  const inPhase = (at: number | null) => phase === null || exploreTimelinePhaseAt(timeline.phases, at) === phase;
  const nodeInPhase = (item: TimelineNode) => phase === null || item.recordedPhases.has(phase);
  const label = (key: string) => copy[key] ?? key;
  const hours = (from: number, to: number) => { const value = (to - from) / 3_600_000; return value >= 48 ? `${(value / 24).toFixed(1)} ${zh ? "天" : "d"}` : `${value.toFixed(1)} ${zh ? "小时" : "h"}`; };
  const maxEvents = Math.max(1, ...timeline.bins.map(bin => bin.events));
  const hasRecordingGaps = timeline.bins.some(bin => !bin.events);
  const ticks = exploreTimelineTickOffsets(elapsed, plotWidth);
  const marker = (item: TimelineNode, style: CSSProperties, extra = "") => {
    const title = `${item.node.latest.title} · ${label(item.label || "unlabeled")}${item.first !== null ? ` · ${timeLabel(item.first)}` : ""}`;
    return <button key={item.node.id} type="button" title={title} aria-label={title} aria-pressed={selectedIds.includes(item.node.id)} data-research-nodes={item.node.id}
      className={`research-timeline-marker ${tone(item.label)}${isOpen(item) ? " is-open" : ""}${selectedIds.includes(item.node.id) ? " is-selected" : ""}${nodeInPhase(item) ? "" : " is-dimmed"}${extra}`}
      style={style} onClick={() => onSelect(item.node.id)}/>;
  };
  const points = (items: readonly TimelineNode[], extra = "") => groupExploreTimelinePoints(items, start, end, plotWidth).map(group => {
    if (group.items.length === 1) return marker(group.items[0]!, {left: pct(group.at)}, extra);
    const ids = group.items.map(item => item.node.id);
    const selected = ids.some(id => selectedIds.includes(id));
    const title = zh ? `${ids.length} 项相邻研究 · ${timeLabel(group.items[0]!.first!)} — ${timeLabel(group.items.at(-1)!.first!)} · 展开全部记录与依据`
      : `${ids.length} nearby research items · ${timeLabel(group.items[0]!.first!)} — ${timeLabel(group.items.at(-1)!.first!)} · Read every record and source`;
    return <button key={ids[0]} type="button" title={title} aria-label={title} aria-pressed={selected} data-research-nodes={ids.join(" ")}
      className={`research-timeline-marker is-cluster${selected ? " is-selected" : ""}${group.items.some(nodeInPhase) ? "" : " is-dimmed"}`}
      style={{left: pct(group.at)}} onClick={event => onSelectMany(ids, event.currentTarget)}><span>{ids.length}</span></button>;
  });
  const trail = groupExploreTimelineDecisions(timeline, phase);
  const timedDecisions = timeline.decisions.filter(item => item.at !== null);
  const decisionCounts = new Map<string, number>();
  for (const item of timedDecisions) {
    const outcome = item.sections!.decision!.outcome;
    decisionCounts.set(outcome, (decisionCounts.get(outcome) ?? 0) + 1);
  }
  const undatedDecisions = timeline.decisions.length - timedDecisions.length;
  const groupedIds = new Set(dayGroups.flatMap(group => group.nodes.map(node => node.id)));
  const swarm = timeline.singles.filter(id => !groupedIds.has(id)).map(id => timeline.nodes.get(id)!).filter(item => item.first !== null);
  const unknownTime = [...timeline.nodes.values()].filter(item => item.first === null).length;
  const routeParents = new Map<string, TimelineNode[]>();
  const routeEdges = new Map<string, (typeof timeline.edges)[number][]>();
  const manyRoutes = timeline.routes.length > 1;
  if (manyRoutes) {
    // Routes join known edge endpoints; preserve source order in the indexes.
    for (const item of timeline.nodes.values()) {
      if (item.routeId === null || !item.node.latest.parent_id) continue;
      const parents = routeParents.get(item.routeId) ?? [];
      parents.push(item);
      routeParents.set(item.routeId, parents);
    }
    for (const edge of timeline.edges) {
      const routeId = timeline.nodes.get(edge.from_node)?.routeId;
      if (routeId == null || routeId !== timeline.nodes.get(edge.to_node)?.routeId) continue;
      const edges = routeEdges.get(routeId) ?? [];
      edges.push(edge);
      routeEdges.set(routeId, edges);
    }
  }
  const phaseNote = timeline.phases.length === 1
    ? (zh ? "全部记录归入同一阅读分组，仅为阅读组织，不是预声明的评估窗口。" : "All records share one reading group, for reading only; not a pre-declared evaluation window.")
    : copy.phaseNote;
  const phaseName = (item: TimelinePhase) => timeline.phases.length === 1 ? copy.whole : [copy.early, copy.middle, copy.late][item.index];
  const phaseDescription = (item: TimelinePhase) => {
    const counts = Object.entries(item.decisions);
    return [
      `${timeLabel(item.start)} — ${timeLabel(item.end)}`,
      `${item.nodes} ${item.nodes === 1 ? copy.item : copy.items} · ${findingCopy.count(item.findings)}`,
      LABELS.map(key => `${label(key)} ${item.labels[key] ?? 0}`).join(" · "),
      timeline.decisions.length ? (counts.length ? `${copy.decisions} · ${counts.map(([key, value]) => `${label(key)} ${value}`).join(" · ")}` : (timeline.structuredCount ? copy.none : copy.unstructured)) : null,
    ].filter(Boolean).join(" · ");
  };
  const phaseCard = (item: TimelinePhase) => {
    return <button key={item.index} type="button" aria-pressed={phase === item.index} onClick={() => onPhase(phase === item.index ? null : item.index)}
      aria-label={`${phaseName(item)} · ${phaseDescription(item)}`} title={`${phaseName(item)} · ${phaseDescription(item)}`}
      className={`research-phase${phase === item.index ? " is-active" : ""}`}>
      <span className="research-phase-head"><strong>{phaseName(item)}</strong><span>{findingCopy.count(item.findings)}</span></span>
      <span className="research-phase-bar" aria-label={LABELS.map(key => `${label(key)} ${item.labels[key] ?? 0}`).join(" · ")}>
        {LABELS.map(key => item.labels[key] ? <span key={key} className={tone(key)} style={{flexGrow: item.labels[key]}}>{item.labels[key]}</span> : null)}
      </span>
    </button>;
  };
  return <div className="research-timeline">
    <div className="research-timeline-summary">
      {complete ? <div className={`research-phases${timeline.phases.length === 1 ? " is-single" : ""}`} role="group" aria-label={phaseNote}>{timeline.phases.map(phaseCard)}</div> : null}
      <div className="research-timeline-key">
        <div className="research-phase-legend">{LABELS.map(key => <span key={key}><i className={tone(key)} aria-hidden="true"/>{label(key)}</span>)}</div>
        <ContextHelp label={zh ? "时间线读法" : "Reading the timeline"}>
      {complete ? <p>{phaseNote}</p> : null}
      <p className="research-density-help"><i className="research-bar-key" aria-hidden/>{complete ? copy.densityHint : copy.partialDensityHint}{zh ? "。" : ". "}{zh ? "无记录空档不代表研究停止。" : "A recording gap does not establish that research stopped."}</p>
      <p>{zh ? "悬停标记可查看记录时间。决策数量覆盖已读取的记录，时段选择只高亮对应的标记。" : "Hover over a mark for its record time. Counts cover the loaded records. Selecting a period highlights its marks."}</p>
      <p>{findingCopy.meaning}</p>
      {!timeline.structuredCount ? <p>{copy.unstructuredHint}</p> : null}
      <p>{copy.routeHint}{zh ? "。" : ". "}{zh ? "相邻点合拢，数字为研究项数，一次展开原始时间与依据。" : "Nearby points group; the number counts research items. Select once for original times and sources."}</p>
      {complete ? <p>{zh ? "选中时段后，高亮其中有记录的研究项。各项位置仍按首次记录时间。" : "Selecting a period highlights items with records in it. Each item stays at its first recorded time."}</p> : null}
      <p>{zh ? "未关联问题按首次记录 UTC 日期收纳，不推断路线或因果。决策轨迹按研究问题归组，组内保留各轮显式决策，最近记录在前；同名问题不会自动合并。" : "Unlinked questions group by first-record UTC date, without inferring a route or cause. Decision history groups explicit decisions by question, newest record first; matching titles do not merge questions."}</p>
      {complete ? <dl className="research-phase-details">{timeline.phases.map(item => <div key={item.index}><dt>{phaseName(item)}</dt><dd>{phaseDescription(item)}</dd></div>)}</dl> : null}
        </ContextHelp>{phase !== null ? <button type="button" className="research-link" onClick={() => onPhase(null)}>{copy.all}</button> : null}
      </div>
    </div>
    <div className="research-timeline-grid">
      <div className="research-row-label">
        <strong>{copy.density}</strong><small>{axisLabel}</small>
        {hasRecordingGaps ? <small><i className="research-gap-key" aria-hidden="true"/>{complete ? copy.gap : copy.partialGap}</small> : null}
        {timeline.decisions.length ? <small className="research-decision-key" role="group" aria-label={copy.decisions}>
          {[...decisionCounts].map(([outcome, count]) => <span key={outcome}><i className={`research-tick-key is-${outcome}`} aria-hidden="true"/> {label(outcome)} {count}</span>)}
          {undatedDecisions ? <span>{copy.unknownTime} {undatedDecisions}</span> : null}
        </small> : null}
      </div>
      <div className="research-row-plot">
        <div className="research-density" ref={plot}>{timeline.bins.map((bin, index) => <span key={index} className={bin.events ? "" : "is-gap"} title={`${timeLabel(bin.start)} — ${timeLabel(bin.end)} · ${bin.events}`}
          style={{height: bin.events ? `${Math.max(8, (bin.events / maxEvents) * 100)}%` : "100%"}}/>)}</div>
        {timedDecisions.length ? <div className="research-decision-ticks" aria-hidden="true">{timedDecisions.map(item => <span key={item.event.event_id}
          title={`${label(item.sections!.decision!.outcome)} · ${timeLabel(item.at!, true)}`}
          className={`is-${item.sections!.decision!.outcome}${inPhase(item.at) ? "" : " is-dimmed"}`} style={{left: pct(item.at!)}}/>)}</div> : null}
        {complete ? timeline.phases.slice(1).map(item => <span key={item.index} className="research-phase-divider" style={{left: pct(item.start)}}/>) : null}
        <div className="research-axis" role="group" aria-label={axisLabel}>{ticks.map(offset => <time
          key={offset} dateTime={`PT${offset / 1_000}S`} title={timeLabel(start + offset, true)}
          className={offset === 0 ? "is-start" : offset === elapsed ? "is-end" : undefined}
          style={{left: pct(start + offset)}}>{elapsedLabel(offset, zh)}</time>)}</div>
      </div>
      {timeline.routes.length ? <div className="research-row-heading"><strong>{copy.routes}</strong></div> : null}
      {timeline.routes.map(route => {
        const members = route.nodeIds.map(id => timeline.nodes.get(id)!).filter(item => item.first !== null);
        if (!members.length) return null;
        const tally = LABELS.map(key => [key, members.filter(item => item.label === key).length] as const).filter(([, value]) => value);
        const first = members[0]!, last = members.at(-1)!;
        const position = new Map(members.map(item => [item.node.id, item.first ?? start]));
        const x = (id: string) => ((position.get(id)! - start) / span) * 1000;
        const parents = manyRoutes ? routeParents.get(route.id) ?? [] : [...timeline.nodes.values()];
        const edges = manyRoutes ? routeEdges.get(route.id) ?? [] : timeline.edges;
        const arcs = [
          ...parents.filter(item => item.routeId === route.id && position.has(item.node.id) && item.node.latest.parent_id && position.has(item.node.latest.parent_id))
            .map(item => ({key: `parent:${item.node.id}`, from: item.node.latest.parent_id, to: item.node.id, kind: "is-grouping", title: ""})),
          ...edges.filter(edge => position.has(edge.from_node) && position.has(edge.to_node))
            .map(edge => ({key: edge.event_id, from: edge.from_node, to: edge.to_node, kind: edge.edge_type === "refutes" ? "is-refutation" : "", title: `${edge.edge_type}: ${edge.summary}`})),
        ];
        return <div className="research-route" key={route.id} role="group" aria-label={`${members.length} · ${first.node.latest.title}`}>
          <div className="research-row-label"><strong>{members.length} {zh ? "项研究" : members.length === 1 ? "research item" : "research items"}</strong>
            <small>{dayLabel(route.first!)} – {dayLabel(route.last!)} · {copy.span} {hours(route.first!, route.last!)}</small>
            <small>{tally.map(([key, value]) => `${label(key)} ${value}`).join(" · ") || copy.unlabeled}</small></div>
          <div className="research-row-plot">
            <div className="research-route-caption">
              {[first, ...(members.length > 1 ? [last] : [])].map((item, index) => <button key={item.node.id} type="button"
                data-research-return={`${route.id}:${index}`} aria-pressed={selectedIds.includes(item.node.id)}
                onClick={event => {
                  // WebKit pointer/AX activation may leave focus on the canvas.
                  // Give the reader the actual opener before it unmounts.
                  event.currentTarget.focus({preventScroll: true});
                  onSelect(item.node.id);
                }}>
                <span>{index ? copy.end : copy.start}</span><strong className={index ? tone(item.label) : undefined}>{item.node.latest.title}</strong>
              </button>)}
            </div>
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
        <div className="research-unlinked-days">{dayGroups.map(group => <button type="button" key={group.day} data-research-day={group.day} aria-pressed={selectedDay === group.day}
          className={`${selectedDay === group.day ? "is-selected" : ""}${group.nodes.some(node => nodeInPhase(timeline.nodes.get(node.id)!)) ? "" : " is-dimmed"}`}
          onClick={event => onGroupSelect(group.day, event.currentTarget)}><time>{group.day ? `${group.day} UTC` : (zh ? "时间未知" : "Unknown time")}</time><strong>{group.nodes.length} {group.nodes.length === 1 ? copy.question : copy.questions}</strong></button>)}</div>
      </> : null}
      {swarm.length ? <>
        <div className="research-row-label"><strong>{copy.singles} · {swarm.length}</strong></div>
        <div className="research-row-plot"><div className="research-swarm">
          {points(swarm, " is-small")}
        </div></div>
      </> : null}
    </div>
    {unknownTime ? <div className="research-history-note"><ContextHelp label={zh ? "记录时间缺失" : "Missing record time"} icon={CircleHelp}><p>{zh ? "缺少记录时间的问题未放上时间轴" : "Questions without a record time are not placed on the axis"} · {unknownTime}</p></ContextHelp>
      {[...timeline.nodes.values()].filter(item => item.first === null).map(item => <button key={item.node.id} type="button" className="research-link" onClick={() => onSelect(item.node.id)}>{item.node.latest.title}</button>)}
    </div> : null}
    {timeline.decisions.length ? <section className="research-trail" aria-label={copy.trail}>
      <h3>{copy.trail}</h3>
      <TrailList groups={trail} copy={copy} label={label} timeLabel={timeLabel} selectedEventId={selectedEventId} onSelect={onSelect}/>
    </section> : <details className="research-trail research-trail-empty">
      <summary>{zh ? "暂无结构化决策记录" : "No structured decision records"}</summary>
      <p className="research-history-note">{timeline.structuredCount ? copy.none : copy.unstructured}</p>
    </details>}
  </div>;
}

function TrailList({groups, copy, label, timeLabel, selectedEventId, onSelect}: {
  groups: ReturnType<typeof groupExploreTimelineDecisions>; copy: Copy; label: (key: string) => string;
  timeLabel: (value: number) => string; selectedEventId?: string; onSelect: Props["onSelect"];
}) {
  const [limit, setLimit] = useState(3);
  const selectedIndex = groups.findIndex(group => group.items.some(item => item.event.event_id === selectedEventId));
  // A bookmarked finding also restores its place in the folded history.
  // Keep that range open when the reader closes, without expanding every row.
  useLayoutEffect(() => {setLimit(value => Math.max(value, selectedIndex + 1));}, [selectedIndex]);
  const visible = Math.max(limit, selectedIndex + 1);
  return <>
    <ol className="research-trail-groups">{groups.slice(0, visible).map(group => <TrailQuestion key={group.id}
      group={group} copy={copy} label={label} timeLabel={timeLabel} selectedEventId={selectedEventId} onSelect={onSelect}/>)}</ol>
    {groups.length > visible ? <button type="button" className="research-link" onClick={() => setLimit(value => value + 6)}>{copy.moreQuestions} · {groups.length - visible}</button> : null}
  </>;
}

function TrailQuestion({group, copy, label, timeLabel, selectedEventId, onSelect}: {
  group: ReturnType<typeof groupExploreTimelineDecisions>[number]; copy: Copy; label: (key: string) => string;
  timeLabel: (value: number) => string; selectedEventId?: string; onSelect: Props["onSelect"];
}) {
  const [limit, setLimit] = useState(3);
  const selectedIndex = group.items.findIndex(item => item.event.event_id === selectedEventId);
  useLayoutEffect(() => {setLimit(value => Math.max(value, selectedIndex + 1));}, [selectedIndex]);
  const visible = Math.max(limit, selectedIndex + 1);
  const knownTimes = group.items.flatMap(item => item.at === null ? [] : [item.at]);
  const exceptions = LABELS.filter(status => status !== "confirmed")
    .map(status => ({status, count: group.items.filter(item => item.event.status === status).length})).filter(item => item.count);
  return <li className="research-trail-question">
    <header>
      <h4>{group.title}</h4>
      <div className="research-trail-meta"><span>{group.items.length} {group.items.length === 1 ? copy.record : copy.records}</span>
        {knownTimes.length ? <span>{timeLabel(knownTimes.at(-1)!)}{knownTimes.length > 1 ? ` — ${timeLabel(knownTimes[0]!)}` : ""}</span> : null}
        {knownTimes.length < group.items.length ? <span>{copy.unknownTime} · {group.items.length - knownTimes.length}</span> : null}
        {exceptions.map(({status, count}) => <span className={tone(status)} key={status}>{label(status)} {count}</span>)}
      </div>
    </header>
    <ol className="research-trail-list">{group.items.slice(0, visible).map(item => {
      const decision = item.sections!.decision!;
      // Preview source text, never a generated title. Complete lists live in
      // the same exact-finding reader used by every other history selection.
      const previewKey = decision.changed.length ? "changed" : decision.stopped.length ? "stopped" : "retained";
      return <li key={item.event.event_id}>
        <button type="button" data-research-decision={item.event.event_id}
          aria-label={`${label(decision.outcome)} · ${item.at === null ? copy.unknownTime : timeLabel(item.at)} · ${label(item.event.status)} · ${item.event.title}`}
          className={selectedEventId === item.event.event_id ? "is-selected" : ""}
          onClick={event => {event.currentTarget.focus({preventScroll: true}); onSelect(item.event.node_id, item.event.result_id, item.event.event_id);}}>
          <span className="research-trail-main">
            <span className="research-trail-meta"><b className={`research-outcome is-${decision.outcome}`}>{label(decision.outcome)}</b>
              <time dateTime={item.at === null ? undefined : item.event.recorded_at}>{item.at === null ? copy.unknownTime : timeLabel(item.at)}</time>
              <span>{label(item.event.status)}</span>
            </span>
            {decision[previewKey].length ? <span className="research-trail-change"><em>{label(previewKey)}</em>{decision[previewKey].join("；")}</span> : null}
            <span className="research-trail-meta">{(["changed", "stopped", "retained"] as const).map(key => decision[key].length
              ? <span key={key}>{label(key)} {decision[key].length}</span> : null)}</span>
          </span>
          <ChevronRight size={16} aria-hidden/>
        </button>
      </li>;
    })}</ol>
    {group.items.length > visible ? <button type="button" className="research-link" onClick={() => setLimit(value => value + 6)}>{copy.more} · {group.items.length - visible}</button> : null}
  </li>;
}
