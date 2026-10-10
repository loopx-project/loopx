import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ArrowRight, Download, ExternalLink, RefreshCw, Search } from "lucide-react";
import { deliveryReviewMarkdown, fetchDeliveryReview, filterReviewNodes, reviewCoverageIncomplete, reviewNodeColumn, type DeliveryReviewSnapshot, type ReviewFocus, type ReviewGraph, type ReviewNode } from "../../data/delivery-review";
import type { WorkspaceDrawerSelection, WorkspaceGoal, WorkspaceModel, WorkspaceTimelineItem } from "./personal-workspace-model";
import { GoalAcceptanceObservationCard } from "./goal-acceptance-observation-card";
import { GoalAcceptanceContractSection } from "./goal-acceptance-contract";
import { GoalWorkMapView } from "./goal-work-map";
import type { GoalWorkMapNode } from "../../data/goal-work-map";
import { deliveryReviewCopy } from "./delivery-review-copy";
import { useWorkspaceI18n } from "./i18n";
import "./delivery-review.css";

type Copy = typeof deliveryReviewCopy.en;
type ReadState = { kind: "loading" | "error"; snapshot?: DeliveryReviewSnapshot; sourceKey?: string }
  | { kind: "ready"; snapshot: DeliveryReviewSnapshot; sourceKey: string };

function ReviewMap({ graph, nodes, selected, onSelect, copy }: {
  graph: ReviewGraph; nodes: ReviewNode[]; selected: string | null; onSelect: (id: string) => void; copy: Copy;
}) {
  const marker = useId().replace(/:/g, "");
  const columns = [0, 1, 2].map(column => nodes.filter(node => reviewNodeColumn(node) === column));
  const positions = new Map(columns.flatMap((column, x) => column.map((node, y) => [node.node_id, { x: x * 320 + 12, y: y * 124 + 48 }] as const)));
  const height = Math.max(1, ...columns.map(column => column.length)) * 124 + 48;
  return <div className="delivery-map-scroll" role="region" aria-label={copy.map} tabIndex={0}>
    <div className="delivery-map" style={{ height }}>
      {[copy.conditions, copy.work, copy.context].map((title, index) => <strong className="delivery-map-heading" style={{ left: index * 320 + 12 }} key={title}>{title}</strong>)}
      <svg aria-hidden="true" width="960" height={height}>
        <defs><marker id={marker} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>
        {graph.edges.map(edge => {
          const from = positions.get(edge.from_node_id), to = positions.get(edge.to_node_id);
          if (!from || !to) return null;
          const forward = from.x < to.x;
          const sx = from.x + (forward || from.x === to.x ? 280 : 0), tx = to.x + (forward ? 0 : 280);
          const sy = from.y + 46, ty = to.y + 46;
          const bend = from.x === to.x ? sx + 28 : (sx + tx) / 2;
          return <path key={edge.edge_id} className={selected && (edge.from_node_id === selected || edge.to_node_id === selected) ? "is-related" : ""} d={`M ${sx} ${sy} C ${bend} ${sy}, ${bend} ${ty}, ${tx} ${ty}`} markerEnd={`url(#${marker})`} />;
        })}
      </svg>
      {nodes.map(node => {
        const position = positions.get(node.node_id)!;
        return <button key={node.node_id} className="delivery-map-node" style={{ left: position.x, top: position.y }} aria-pressed={selected === node.node_id} onClick={() => onSelect(node.node_id)} type="button">
          <span>{copy.kind[node.kind]}<em data-state={node.state}>{copy.state[node.state]}</em></span>
          <strong title={node.title}>{node.title}</strong>
          <small>{node.owner_agent ?? copy.unavailable}</small>
        </button>;
      })}
    </div>
  </div>;
}

type DeliveryReviewProps = {
  goal: WorkspaceGoal; items: WorkspaceTimelineItem[]; userTodos: WorkspaceModel["userTodos"];
  onSelect: (selection: WorkspaceDrawerSelection) => void; active: boolean;
};

export function DeliveryReview({ goal, items, userTodos, onSelect, active }: DeliveryReviewProps) {
  const { locale } = useWorkspaceI18n();
  const copy = deliveryReviewCopy[locale];
  const [state, setState] = useState<ReadState>({ kind: "loading" });
  const [refresh, setRefresh] = useState(0);
  const [query, setQuery] = useState("");
  const [focus, setFocus] = useState<ReviewFocus>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // The work map is the spatial overview; the bounded chain defaults to its list.
  const [mapView, setMapView] = useState(false);
  const [mapSelectedId, setMapSelectedId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  const detailRef = useRef<HTMLElement>(null);
  const scopedAttention = userTodos.filter(todo => todo.goalId === goal.goalId);
  // Polling can invalidate a snapshot without re-running the cold graph query.
  const sourceKey = JSON.stringify([goal.agentTodos, goal.acceptanceObservation, scopedAttention]);
  const currentSource = useRef(sourceKey);
  currentSource.current = sourceKey;
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const sourceAtStart = currentSource.current;
    // Retain the prior view during a refresh so reading position does not collapse.
    setState(current => ({ ...current, kind: "loading" }));
    setFeedback("");
    void fetchDeliveryReview(goal.goalId, controller.signal).then(snapshot => {
      if (!controller.signal.aborted) setState({ kind: "ready", snapshot, sourceKey: sourceAtStart });
    }).catch(() => { if (!controller.signal.aborted) setState(current => ({ ...current, kind: "error" })); });
    return () => controller.abort();
  }, [goal.goalId, refresh, active]);
  const snapshot = state.snapshot?.goal_id === goal.goalId ? state.snapshot : null;
  const stale = Boolean(snapshot) && state.sourceKey !== sourceKey;
  useEffect(() => {
    // Re-read once workspace facts settle instead of leaving a stale snapshot.
    if (!active || state.kind !== "ready" || !stale) return;
    const timer = window.setTimeout(() => setRefresh(value => value + 1), 600);
    return () => window.clearTimeout(timer);
  }, [active, state.kind, stale, sourceKey]);
  const usable = Boolean(snapshot) && state.kind === "ready" && !stale;
  const graph = snapshot?.graph;
  const selected = graph?.nodes.find(node => node.node_id === selectedId);
  const effectiveFocus = focus === "related" && !selected ? "all" : focus;
  const nodes = useMemo(() => graph ? filterReviewNodes(graph, query, effectiveFocus, selectedId) : [], [graph, query, effectiveFocus, selectedId]);
  const relations = graph?.edges.filter(edge => edge.from_node_id === selectedId || edge.to_node_id === selectedId) ?? [];
  const nodeById = new Map(graph?.nodes.map(node => [node.node_id, node]));
  const reset = () => { setQuery(""); setFocus("all"); };
  const selectNode = (id: string) => {
    setSelectedId(id);
    setFeedback("");
    window.requestAnimationFrame(() => detailRef.current?.scrollIntoView({ block: "nearest" }));
  };

  function linkedSources(node: ReviewNode): WorkspaceDrawerSelection[] {
    const todoIds = new Set(node.refs.todo_ids ?? []), gateIds = new Set(node.refs.gate_ids ?? []), runIds = new Set(node.refs.run_ids ?? []);
    return [
      ...goal.agentTodos.filter(todo => todoIds.has(todo.todoId)).map(todo => ({ kind: "todo" as const, item: { ...todo, goalId: goal.goalId, goalTitle: goal.title, ownerLabel: todo.claimedBy } })),
      ...scopedAttention.filter(todo => gateIds.has(todo.todoId) || todoIds.has(todo.todoId)).map(item => ({ kind: "attention" as const, item })),
      ...items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "run" }> => item.kind === "run" && item.run.goalId === goal.goalId && runIds.has(item.run.runId)).map(item => ({ kind: "run" as const, item: item.run })),
    ];
  }
  const sources = selected ? linkedSources(selected) : [];
  function mapSource(node: GoalWorkMapNode): WorkspaceDrawerSelection | null {
    const id = node.refs.todo_ids?.[0];
    if (!id) return null;
    if (node.kind === "gate") {
      const item = scopedAttention.find(todo => todo.todoId === id);
      return item ? { kind: "attention", item } : null;
    }
    // The status window is bounded; its absence cannot revoke exact request reading.
    const todo = goal.agentTodos.find(item => item.todoId === id) ?? {
      todoId: id, sourceTodoId: id, detailMode: "request_only" as const,
      text: node.title, done: node.state === "done", claimedBy: node.owner_agent ?? null,
      status: node.state === "waiting" ? "deferred" : node.state === "blocked" || node.state === "done" ? node.state : "open",
      taskDomain: node.task_domain ?? null, taskClass: node.kind === "monitor" ? "continuous_monitor" : null,
    };
    return { kind: "todo", item: { ...todo, goalId: goal.goalId, goalTitle: goal.title, ownerLabel: todo.claimedBy } };
  }
  function download() {
    if (!snapshot || !usable) return;
    let url: string | undefined;
    try {
      url = URL.createObjectURL(new Blob([deliveryReviewMarkdown(snapshot, copy)], { type: "text/markdown;charset=utf-8" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "loopx-delivery-review.md";
      anchor.click();
      setFeedback(copy.exported);
    } catch { setFeedback(copy.exportFailed); }
    finally { if (url) window.setTimeout(() => URL.revokeObjectURL(url!), 1000); }
  }
  return <section className="delivery-review" aria-label={copy.title}>
    <header className="delivery-review-toolbar">
      <h2>{copy.title}</h2>
      <div><button type="button" disabled={state.kind === "loading"} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={15} />{copy.refresh}</button><button type="button" disabled={!usable} onClick={download}><Download size={15} />{copy.export}</button></div>
    </header>
    {feedback ? <p role="status">{feedback}</p> : null}
    {!snapshot ? <p role={state.kind === "error" ? "alert" : "status"} className="delivery-notice">{state.kind === "error" ? copy.error : copy.loading}</p> : <>
      {state.kind === "error" ? <p role="alert" className="delivery-notice">{copy.refreshError}</p> : null}
      <p className="delivery-snapshot-time">{copy.observed} · <time dateTime={snapshot.observed_at}>{new Date(snapshot.observed_at).toLocaleString(locale)}</time>
        {state.kind === "loading" || (stale && state.kind !== "error") ? <span role="status"> · {copy.updating}</span> : null}</p>
      {stale && state.kind === "error" ? <p role="alert" className="delivery-notice">{copy.changed}</p> : null}
      {snapshot.goal_map ? <GoalWorkMapView map={snapshot.goal_map} copy={copy.workMap} selectedId={mapSelectedId} onSelect={setMapSelectedId}
        canOpen={node => usable && mapSource(node) !== null} onOpen={node => { const source = mapSource(node); if (source) onSelect(source); }} />
        : snapshot.goal_map === null ? <p className="delivery-notice">{copy.workMap.unavailable}</p> : null}
      <p className="delivery-boundary">{copy.scope} {copy.acceptanceBoundary}</p>
      {graph ? <>
        {reviewCoverageIncomplete(graph) ? <details className="delivery-notice"><summary>{copy.incomplete}</summary><dl>
          <div><dt>{copy.omittedGates}</dt><dd>{graph.limits.user_gate_truncated_count}</dd></div>
          <div><dt>{copy.missing}</dt><dd>{graph.limits.missing_predecessor_count ?? copy.unavailable}</dd></div>
          <div><dt>{copy.clipped}</dt><dd>{graph.limits.predecessor_truncated === undefined ? copy.unavailable : graph.limits.predecessor_truncated ? copy.yes : copy.no}</dd></div>
          <div><dt>{copy.sourceClipped}</dt><dd>{graph.limits.source_truncated === undefined ? copy.unavailable : graph.limits.source_truncated ? copy.yes : copy.no}</dd></div>
        </dl></details> : null}
        <section className="delivery-chain" aria-label={copy.chain}>
          <header className="delivery-chain-toolbar"><h3>{copy.chain}</h3><span>{copy.visible} {nodes.length}/{graph.nodes.length}</span>
            <div role="group" aria-label={copy.view}><button type="button" aria-pressed={mapView} onClick={() => setMapView(true)}>{copy.map}</button><button type="button" aria-pressed={!mapView} onClick={() => setMapView(false)}>{copy.list}</button></div>
          </header>
          <div className="delivery-filters"><label><Search size={16} /><input aria-label={copy.search} placeholder={copy.search} value={query} onChange={event => setQuery(event.target.value)} /></label>
            <select aria-label={copy.filter} value={effectiveFocus} onChange={event => setFocus(event.target.value as ReviewFocus)}>
              <option value="all">{copy.all}</option><option value="conditions">{copy.conditions}</option><option value="evidence">{copy.evidence}</option><option value="related" disabled={!selected}>{copy.related}</option>
            </select><button type="button" onClick={reset}>{copy.reset}</button>
          </div>
          {!nodes.length ? <p className="delivery-empty" role="status">{copy.empty}</p> : mapView ? <ReviewMap graph={graph} nodes={nodes} selected={selectedId} onSelect={selectNode} copy={copy} /> : <ul className="delivery-node-list">{nodes.map(node => <li key={node.node_id}><button type="button" aria-pressed={selectedId === node.node_id} onClick={() => selectNode(node.node_id)}><span>{copy.kind[node.kind]}</span><strong>{node.title}</strong><small>{node.owner_agent ?? copy.unavailable}</small><em data-state={node.state}>{copy.state[node.state]}</em></button></li>)}</ul>}
        </section>
        <section className="delivery-node-detail" aria-label={copy.details} ref={detailRef}>
          {!selected ? <p>{copy.select}</p> : <>
            <header><span>{copy.kind[selected.kind]} · {copy.state[selected.state]}</span><h3>{selected.title}</h3>{selected.owner_agent ? <p>{selected.owner_agent}</p> : null}</header>
            {selected.from_agent || selected.to_agent ? <p>{selected.from_agent ?? copy.unavailable} → {selected.to_agent ?? copy.unavailable}</p> : null}
            <div className="delivery-source-actions">{sources.length ? sources.map((selection, index) => <button type="button" disabled={!usable} key={`${selection.kind}:${index}`} onClick={() => onSelect(selection)}><ExternalLink size={15} />{selection.kind === "todo" ? copy.openTask : selection.kind === "attention" ? copy.openGate : copy.openRun}</button>) : <p>{copy.sourceUnavailable}</p>}</div>
            <h4>{copy.relations}</h4>
            {relations.length ? <ul className="delivery-relations">{relations.map(edge => <li key={edge.edge_id}>
              <div><button type="button" onClick={() => selectNode(edge.from_node_id)}>{nodeById.get(edge.from_node_id)!.title}</button><span><ArrowRight size={13} />{copy.relation[edge.relation]}<ArrowRight size={13} /></span><button type="button" onClick={() => selectNode(edge.to_node_id)}>{nodeById.get(edge.to_node_id)!.title}</button></div><p>{edge.reason}</p>
            </li>)}</ul> : <p>{copy.noRelations}</p>}
            <details><summary>{copy.refs}</summary><code>{selected.node_id}</code>{Object.entries(selected.refs).map(([kind, refs]) => <p key={kind}><strong>{kind}</strong> {refs.join(", ")}</p>)}</details>
          </>}
        </section>
      </> : <p className="delivery-notice">{copy.noGraph}</p>}

    </>}
    <GoalAcceptanceContractSection goalId={goal.goalId} contract={snapshot?.acceptance?.goal_acceptance_contract} copy={copy.contract} current={usable} />
    <GoalAcceptanceObservationCard goal={snapshot ? { ...goal, acceptanceObservation: snapshot.acceptance } : goal} />
  </section>;
}
