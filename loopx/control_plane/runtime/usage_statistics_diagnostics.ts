/** Versioned, content-free diagnostics. This is observation, never work authority. */
import { counterKey, CONTEXTS, DURATIONS, FEATURES, MAX_COUNT, MAX_ROWS, object } from "./usage_statistics_contract.ts";
import type { Counter } from "./usage_statistics_contract.ts";

export const DIAGNOSTIC_SCHEMA = "loopx_usage_diagnostics_v1";
export { CONTEXTS } from "./usage_statistics_contract.ts";
export const DIAGNOSTIC_FEATURES = [...FEATURES, "heartbeat", "state", "agent", "memory", "capability", "maintenance"] as const;
export type DiagnosticFeature = typeof DIAGNOSTIC_FEATURES[number];
/** Fixed parser command names, not argv values or user-supplied extension names. */
export function diagnosticFeature(command: string): DiagnosticFeature {
  if ((DIAGNOSTIC_FEATURES as readonly string[]).includes(command)) return command as DiagnosticFeature;
  const families: Record<string, DiagnosticFeature> = {
    "heartbeat-prompt": "heartbeat", "refresh-state": "state", "checkpoint-context": "state",
    "agent-context": "agent", "agent-capabilities": "agent", "agent-directory": "agent", "manager-inbox": "agent",
    "reward-memory": "memory", "agent-turn-recall": "memory", "semantic-preference": "memory",
    extension: "capability", "workflow-skills": "capability", "project-skill": "capability",
    doctor: "maintenance", update: "maintenance", "migrate-local-state": "maintenance",
    "serve-status": "chat", dashboard: "chat",
  };
  return Object.hasOwn(families, command) ? families[command] : "other";
}
export const DIAGNOSTIC_OPERATIONS = ["default", "plan", "run-once", "status", "should-run", "spend-slot", "monitor-poll", "list", "add", "claim", "update", "complete", "register", "resolve", "bind-session", "unbind-session", "merge-readiness", "check-result", "result-return"] as const;
export const REASONS = ["none", "not_ready", "invalid_input", "permission", "not_found", "timeout", "connection", "interrupted", "command_failed"] as const;
export const SIGNALS = ["none", "project_registered", "managed_turn_committed", "todo_completed", "todo_validated", "result_returned"] as const;
const FEATURE_OPERATIONS: Record<string, readonly string[]> = {
  turn: ["plan", "run-once", "status"], quota: ["status", "plan", "should-run", "spend-slot", "monitor-poll"],
  todo: ["list", "add", "claim", "update", "complete"], project: ["register", "resolve", "bind-session", "unbind-session"],
  "pr-review": ["merge-readiness", "check-result"], other: ["result-return"],
};
const SIGNAL_SOURCES: Record<string, readonly [string, string]> = {
  project_registered: ["project", "register"], managed_turn_committed: ["turn", "run-once"],
  todo_completed: ["todo", "complete"], todo_validated: ["todo", "complete"], result_returned: ["other", "result-return"],
};
export type Diagnostic = Omit<Counter, "outcome" | "error" | "feature"> & {
  feature: DiagnosticFeature;
  outcome: "ok" | "blocked" | "failed" | "cancelled"; error: typeof REASONS[number];
  operation: typeof DIAGNOSTIC_OPERATIONS[number]; signal: typeof SIGNALS[number];
  version: string; activity_day: string; context: typeof CONTEXTS[number];
};
export type DiagnosticAggregate = { schema: typeof DIAGNOSTIC_SCHEMA; counters: Diagnostic[] };
export function usageContext(value: unknown): Diagnostic["context"] {
  return (CONTEXTS as readonly unknown[]).includes(value) ? value as Diagnostic["context"] : "unknown";
}
export function diagnosticKey(value: Diagnostic): string {
  return [counterKey(value as Counter), value.operation, value.signal, value.version, value.activity_day, value.context].join(":");
}
export function validDiagnostic(value: unknown): value is Diagnostic {
  if (!object(value) || Object.keys(value).sort().join() !== "activity_day,context,count,duration,error,feature,operation,outcome,signal,version") return false;
  if (!(DIAGNOSTIC_FEATURES as readonly unknown[]).includes(value.feature) || !(DURATIONS as readonly unknown[]).includes(value.duration)
    || !(DIAGNOSTIC_OPERATIONS as readonly unknown[]).includes(value.operation) || !(SIGNALS as readonly unknown[]).includes(value.signal)
    || !(CONTEXTS as readonly unknown[]).includes(value.context) || !(REASONS as readonly unknown[]).includes(value.error)
    || typeof value.version !== "string" || !/^\d{1,3}\.\d{1,3}\.\d{1,4}$/.test(value.version)
    || typeof value.activity_day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value.activity_day)
    || !Number.isFinite(Date.parse(value.activity_day)) || new Date(value.activity_day).toISOString().slice(0, 10) !== value.activity_day
    || !Number.isInteger(value.count) || Number(value.count) < 1 || Number(value.count) > MAX_COUNT) return false;
  if (value.signal !== "none" && value.outcome !== "ok") return false;
  if (value.operation !== "default" && !FEATURE_OPERATIONS[String(value.feature)]?.includes(String(value.operation))) return false;
  if (value.signal !== "none" && SIGNAL_SOURCES[String(value.signal)]?.join(":") !== `${value.feature}:${value.operation}`) return false;
  if (value.outcome === "blocked" && (value.feature !== "pr-review" || value.operation !== "merge-readiness")) return false;
  return value.outcome === "ok" ? value.error === "none"
    : value.outcome === "blocked" ? value.error === "not_ready"
    : value.outcome === "cancelled" ? value.error === "interrupted"
    : value.outcome === "failed" && !["none", "not_ready", "interrupted"].includes(String(value.error));
}
export function validDiagnostics(value: unknown): value is DiagnosticAggregate {
  return object(value) && Object.keys(value).sort().join() === "counters,schema" && value.schema === DIAGNOSTIC_SCHEMA
    && Array.isArray(value.counters) && value.counters.length > 0 && value.counters.length <= MAX_ROWS
    && value.counters.every(validDiagnostic) && new Set(value.counters.map(diagnosticKey)).size === value.counters.length;
}
/** Only fixed metadata crosses the Python/TS bridge. Never parse error prose. */
export function resultDiagnostic(feature: string, operation: unknown, facts: unknown, code: number, failure: unknown): Pick<Diagnostic, "operation" | "outcome" | "error" | "signal"> {
  const op = typeof operation === "string" && FEATURE_OPERATIONS[feature]?.includes(operation) ? operation as Diagnostic["operation"] : "default";
  const f = object(facts) ? facts : {};
  let outcome: Diagnostic["outcome"] = code === 0 ? "ok" : "failed";
  let error: Diagnostic["error"] = code === 0 ? "none" : "command_failed";
  if (failure === "interrupted") { outcome = "cancelled"; error = "interrupted"; }
  else if (["invalid_input", "permission", "not_found", "timeout", "connection"].includes(String(failure))) {
    outcome = "failed"; error = failure as Diagnostic["error"];
  } else if (op === "merge-readiness" && f.ok === true && f.ready === false) {
    outcome = "blocked"; error = "not_ready";
  } else if (f.ok === false) { outcome = "failed"; error = "command_failed"; }
  let signal: Diagnostic["signal"] = "none";
  if (outcome === "ok" && f.ok === true && f.dry_run !== true) {
    if (feature === "project" && op === "register" && f.changed === true) signal = "project_registered";
    if (feature === "todo" && op === "complete" && f.completed === true && f.changed === true) signal = f.validation_passed === true ? "todo_validated" : "todo_completed";
    if (feature === "turn" && op === "run-once" && f.turn_committed === true) signal = "managed_turn_committed";
    if (op === "result-return" && f.reply_verified === true && f.changed === true) signal = "result_returned";
  }
  return { operation: op, outcome, error, signal };
}
