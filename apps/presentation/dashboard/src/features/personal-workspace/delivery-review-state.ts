import {useEffect, useRef, useState} from "react";
import {fetchDeliveryReview, type DeliveryReviewSnapshot} from "../../data/delivery-review";
import type {WorkspaceGoal, WorkspaceModel} from "./personal-workspace-model";

type ReadState = {kind: "loading" | "error"; snapshot?: DeliveryReviewSnapshot; sourceKey?: string; scopeKey?: string}
  | {kind: "ready"; snapshot: DeliveryReviewSnapshot; sourceKey: string; scopeKey: string};

/** One read shared by the work lens and delivery evidence; no independent poller. */
export function useDeliveryReview(goal: WorkspaceGoal | null, userTodos: WorkspaceModel["userTodos"], active: boolean, scopeKey = "local") {
  const [state, setState] = useState<ReadState>({kind: "loading"});
  const [revision, setRevision] = useState(0);
  const scopedAttention = userTodos.filter(todo => todo.goalId === goal?.goalId);
  const sourceKey = JSON.stringify([goal?.agentTodos, goal?.acceptanceObservation, scopedAttention]);
  const currentSource = useRef(sourceKey);
  currentSource.current = sourceKey;
  useEffect(() => {
    if (!active || !goal) return;
    const controller = new AbortController();
    const sourceAtStart = currentSource.current;
    setState(current => ({...current, kind: "loading"}));
    void fetchDeliveryReview(goal.goalId, controller.signal).then(snapshot => {
      if (!controller.signal.aborted) setState({kind: "ready", snapshot, sourceKey: sourceAtStart, scopeKey});
    }).catch(() => {if (!controller.signal.aborted) setState(current => ({...current, kind: "error"}));});
    return () => controller.abort();
  }, [goal?.goalId, revision, active, scopeKey]);
  const snapshot = goal && state.scopeKey === scopeKey && state.snapshot?.goal_id === goal.goalId ? state.snapshot : null;
  const stale = Boolean(snapshot) && state.sourceKey !== sourceKey;
  useEffect(() => {
    if (!active || state.kind !== "ready" || !stale) return;
    const timer = window.setTimeout(() => setRevision(value => value + 1), 600);
    return () => window.clearTimeout(timer);
  }, [active, state.kind, stale, sourceKey]);
  return {snapshot, kind: state.kind, stale, usable: Boolean(snapshot) && state.kind === "ready" && !stale,
    refresh: () => setRevision(value => value + 1)};
}
export type DeliveryReviewRead = ReturnType<typeof useDeliveryReview>;
