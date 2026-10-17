import {requestJson} from "./chat";

/** Native activation/recall packets. Profile and raw hits are never cached. */
export type DecisionContextActivation = Readonly<{
  ok: boolean;
  status: string;
  reason_code?: string | null;
  available: boolean;
  enabled: boolean;
  context_provider_configured: boolean;
}>;

export type DecisionContextRecall = Readonly<{
  ok: boolean;
  status: string;
  reason_code?: string | null;
  provider?: string;
  provider_version?: string | null;
  observed_at?: string;
  results: ReadonlyArray<Readonly<{
    provider_ref: string;
    summary: string;
    content: string;
    score: number | null;
  }>>;
}>;

type ContextTarget = Readonly<{
  goal_id: string;
  agent_id: string;
  profile: Record<string, unknown> | null;
}>;

export function inspectDecisionContext(target: ContextTarget, signal: AbortSignal) {
  return requestJson<DecisionContextActivation>("/api/chat/decision-context", {
    method: "POST", body: JSON.stringify({...target, action: "inspect"}), signal,
  });
}

export function recallDecisionContext(
  request: ContextTarget & Readonly<{query: string; context_scope_ref: string; max_results: number}>,
  signal: AbortSignal,
) {
  return requestJson<DecisionContextRecall>("/api/chat/decision-context", {
    method: "POST", body: JSON.stringify({...request, action: "recall"}), signal,
  });
}
