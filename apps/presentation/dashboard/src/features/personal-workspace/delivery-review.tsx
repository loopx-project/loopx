import { useId, useMemo, useRef, useState } from "react";
import { ArrowRight, CircleAlert, Download, ExternalLink, RefreshCw, Search } from "lucide-react";
import { deliveryReviewMarkdown, filterReviewNodes, reviewCoverageIncomplete, reviewNodeColumn, type ReviewFocus, type ReviewGraph, type ReviewNode } from "../../data/delivery-review";
import type { WorkspaceDrawerSelection, WorkspaceGoal, WorkspaceModel, WorkspaceTimelineItem } from "./personal-workspace-model";
import { GoalAcceptanceObservationCard } from "./goal-acceptance-observation-card";
import { GoalAcceptanceContractSection } from "./goal-acceptance-contract";
import { GoalWorkMapView } from "./goal-work-map";
import type { GoalWorkMapNode } from "../../data/goal-work-map";
import { deliveryReviewCopy } from "./delivery-review-copy";
import { useWorkspaceI18n } from "./i18n";
import {useDeliveryReview, type DeliveryReviewRead} from "./delivery-review-state";
import "./delivery-review.css";

type Copy = typeof deliveryReviewCopy.en;

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
  onSelect: (selection: WorkspaceDrawerSelection | null) => void; active: boolean;
};

export function DeliveryReview(props: DeliveryReviewProps) {
  const review = useDeliveryReview(props.goal, props.userTodos, props.active);
  return <DeliveryReviewContent {...props} review={review} />;
}

export function DeliveryReviewContent({ goal, items, userTodos, onSelect, review, hideWorkMap = false }: DeliveryReviewProps & {
  review: DeliveryReviewRead; hideWorkMap?: boolean;
}) {
  const { locale } = useWorkspaceI18n();
  const copy = deliveryReviewCopy[locale];
  const {snapshot, stale, usable} = review;
  const state = {kind: review.kind};
  const [query, setQuery] = useState("");
  const [focus, setFocus] = useState<ReviewFocus>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // The work map is the spatial overview; the bounded chain defaults to its list.
  const [mapView, setMapView] = useState(false);
  const [feedback, setFeedback] = useState("");
  const detailRef = useRef<HTMLElement>(null);
  const scopedAttention = userTodos.filter(todo => todo.goalId === goal.goalId);
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
      <div><button type="button" disabled={state.kind === "loading"} onClick={() => review.refresh()}><RefreshCw size={15} />{copy.refresh}</button><button type="button" disabled={!usable} onClick={download}><Download size={15} />{copy.export}</button></div>
    </header>
    {feedback ? <p role="status">{feedback}</p> : null}
    {!snapshot ? state.kind === "error" ? <p role="alert" className="delivery-notice is-error"><CircleAlert aria-hidden size={15} />{copy.error}</p>
      : <div role="status" className="delivery-loading" aria-label={copy.loading}><i /><i /><i /></div> : <>
      {state.kind === "error" ? <p role="alert" className="delivery-notice is-error"><CircleAlert aria-hidden size={15} />{copy.refreshError}</p> : null}
      <p className="delivery-snapshot-time">{copy.observed} · <time dateTime={snapshot.observed_at}>{new Date(snapshot.observed_at).toLocaleString(locale)}</time>
        {state.kind === "loading" || (stale && state.kind !== "error") ? <span role="status"> · {copy.updating}</span> : null}</p>
      {stale && state.kind === "error" ? <p role="alert" className="delivery-notice is-error"><CircleAlert aria-hidden size={15} />{copy.changed}</p> : null}
      {!hideWorkMap ? <DeliveryWorkMap goal={goal} userTodos={userTodos} review={review} onSelect={selection => {if (selection) onSelect(selection);}} /> : null}
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

/** Same map and exact-request fallback, now usable as an Overview work lens. */
export function DeliveryWorkMap({goal, userTodos, review, onSelect, selectedTodoId}: {
  goal: WorkspaceGoal; userTodos: WorkspaceModel["userTodos"]; review: DeliveryReviewRead;
  onSelect: (selection: WorkspaceDrawerSelection | null) => void; selectedTodoId?: string | null;
}) {
  const {locale} = useWorkspaceI18n();
  const copy = deliveryReviewCopy[locale];
  const [localSelection, setLocalSelection] = useState<string | null>(null);
  const scopedAttention = userTodos.filter(todo => todo.goalId === goal.goalId);
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
  const map = review.snapshot?.goal_map;
  if (!map) return <p role="status">{review.kind === "loading" ? copy.loading : copy.workMap.unavailable}</p>;
  const selected = selectedTodoId === undefined ? localSelection
    : selectedTodoId ? map.nodes.find(node => node.refs.todo_ids?.includes(selectedTodoId))?.node_id ?? null
      : (() => {const node = map.nodes.find(node => node.node_id === localSelection);
          return node && (node.kind === "gate" || !node.refs.todo_ids?.length) ? localSelection : null;}) ();
  return <>
    {!review.usable ? <p role="status">{review.kind === "error" ? copy.refreshError : copy.updating}</p> : null}
    <GoalWorkMapView map={map} copy={copy.workMap} selectedId={selected}
      onSelect={id => {
        setLocalSelection(id);
        if (id === null && selectedTodoId) onSelect(null);
        if (selectedTodoId !== undefined && review.usable) {
          const node = map.nodes.find(node => node.node_id === id);
          const source = node ? mapSource(node) : null;
          if (source) onSelect(source);
        }
      }} canOpen={node => review.usable && mapSource(node) !== null}
      onOpen={node => {const source = mapSource(node); if (review.usable && source) onSelect(source);}} />
  </>;
}
