/** Complete native-child admission; host codecs supply facts, never lane decisions. */
import { createHash } from "node:crypto";
import type { JsonObject } from "../effect_program.ts";
import { requireJsonObject, requireStringArray, requireInteger, requireNonEmptyString } from "../runtime_decode.ts";
import { authorityUnicodeCompare } from "../coordination/authority_store_codec.ts";
import { pythonJson } from "../todos/read_codec.ts";
import { todoIsActionableOpen } from "../todos/summary_lanes.ts";
import { missingRequiredCapabilities } from "../agents/capability_gate.ts";
import {
  evaluateQuotaSchedulingWriteScopesOverlap,
  schedulingWriteScopeAllowed,
} from "../work_items/task_lease_acquire_decision.ts";
import { QUOTA_GOAL_BOUNDARY_ARTIFACT } from "./turn_envelope.ts";

interface Lane {
  item: JsonObject;
  id: string | null;
  domain: string | null;
  repository: string | null;
  effectiveRepository: string | null;
  scopes: string[];
}
const rows = (value: unknown): JsonObject[] => {
  if (!Array.isArray(value)) throw new TypeError("admission rows must be an array");
  return value.map((row) => requireJsonObject(row, "admission row"));
};
const nullable = (value: unknown): string | null =>
  value === null ? null : requireNonEmptyString(value, "admission token");
const sha256 = (value: string) => createHash("sha256").update(value).digest();
const READ_ONLY_ACTION_KINDS = ["analyze", "compare", "inspect", "review", "test", "validate"];
const actionable = (item: JsonObject) =>
  todoIsActionableOpen({
    done: item.done === true,
    status: String(item.status),
    hasResume: Boolean(item.resume_when),
    resumeReady: item.resume_ready === true,
    acceptanceBlocked: (item.goal_acceptance_guard as JsonObject | null)?.allowed === false,
  });
const blockedLane = (lane: Lane, reasons: string[]): JsonObject => ({
  todo_id: lane.id,
  task_domain: lane.domain,
  reason_codes: reasons,
});

export function admitAdaptiveChildren(value: unknown): JsonObject | null {
  const input = requireJsonObject(value, "adaptive child admission");
  const agent = requireNonEmptyString(input.agent_id, "agent_id");
  const available = requireStringArray(input.available_capabilities, "available_capabilities");
  const goalRepository = nullable(input.goal_repository);
  const goalScopes = requireStringArray(input.goal_write_scope, "goal_write_scope");
  const domains = input.allowed_domains === null ? null : requireStringArray(input.allowed_domains, "allowed_domains");
  const maxChildren = requireInteger(input.max_children, "max_children");
  const users = rows(input.user_items);
  const lanes: Lane[] = rows(input.items).map((item) => ({
    item,
    id: nullable(item.todo_id),
    domain: nullable(item.task_domain),
    repository: nullable(item.task_repository),
    effectiveRepository: nullable(item.task_repository) || goalRepository,
    scopes: requireStringArray(item.required_write_scopes, "required_write_scopes"),
  }));
  const candidates = lanes.filter((lane) => lane.item.done !== true && lane.item.task_class === "advancement_task");
  const dependency = (lane: Lane) =>
    users.some(
      (user) =>
        user.unblocks_todo_id === lane.item.dependency_todo_id &&
        user.done !== true &&
        !["done", "deferred"].includes(String(user.status).toLowerCase()),
    );
  const election = candidates.filter((lane) => actionable(lane.item) && !dependency(lane));
  if (election.length < 2) return null;
  const claims = new Set(
    election.map((lane) => nullable(lane.item.claimed_by)).filter((id): id is string => id !== null),
  );
  const assignmentKey = sha256(
    pythonJson({ mode: "adaptive", todo_ids: election.map((lane) => lane.id ?? "").sort(authorityUnicodeCompare) }),
  ).toString("hex");
  const peers = requireStringArray(input.registered_agents, "registered_agents");
  // Python selected an unsigned 64-bit prefix. Number would round away low bits.
  const index = peers.length ? Number(sha256(assignmentKey).readBigUInt64BE(0) % BigInt(peers.length)) : -1;
  const coordinator = claims.size === 1 ? [...claims][0] : (peers[index] ?? null);
  if (coordinator !== agent) return null;
  const owned = candidates.filter((lane) => !lane.item.claimed_by || lane.item.claimed_by === agent);
  if (owned.length < 2) return null;
  const admitted: Lane[] = [];
  const blocked: JsonObject[] = [];
  for (const lane of owned) {
    const item = lane.item;
    const reasons: string[] = [];
    if (requireStringArray(item.excluded_agents, "excluded_agents").includes(agent))
      reasons.push("coordinator_excluded");
    if (!actionable(item) || dependency(lane)) reasons.push("dependency_not_ready");
    if (domains !== null && (lane.domain === null || !domains.includes(lane.domain)))
      reasons.push("task_domain_not_allowed");
    if (
      missingRequiredCapabilities(
        requireStringArray(item.required_capabilities, "required_capabilities"),
        requireStringArray(item.target_capabilities, "target_capabilities"),
        available,
      ).length
    )
      reasons.push("capability_unavailable");
    if (item.repository_declared) {
      if (!lane.repository) reasons.push("task_repository_invalid");
      else if (!goalRepository || lane.repository !== goalRepository) reasons.push("task_repository_not_allowed");
    }
    const action = String(item.action_kind ?? "").toLowerCase();
    if (
      !READ_ONLY_ACTION_KINDS.some((prefix) => action === prefix || action.startsWith(`${prefix}_`)) &&
      !lane.scopes.length
    )
      reasons.push("write_scope_missing");
    if (
      lane.scopes.length &&
      (!goalScopes.length || lane.scopes.some((scope) => !schedulingWriteScopeAllowed(scope, goalScopes)))
    )
      reasons.push("write_scope_not_allowed");
    if (reasons.length) blocked.push(blockedLane(lane, reasons));
    else admitted.push(lane);
  }
  if (admitted.length < 2) return null;
  const primary = admitted[0];
  const selected: JsonObject[] = [];
  const writeLanes = [primary];
  for (let index = 1; index < admitted.length; index += 1) {
    const lane = admitted[index];
    if (selected.length >= maxChildren) {
      blocked.push(...admitted.slice(index).map((row) => blockedLane(row, ["capacity_deferred"])));
      break;
    }
    const conflict = writeLanes.find(
      (existing) =>
        existing.scopes.length &&
        lane.scopes.length &&
        existing.effectiveRepository === lane.effectiveRepository &&
        evaluateQuotaSchedulingWriteScopesOverlap({ left: existing.scopes, right: lane.scopes }).overlap,
    );
    if (conflict) {
      blocked.push({ ...blockedLane(lane, ["write_scope_conflict"]), conflicts_with_todo_id: conflict.id });
      continue;
    }
    const item = lane.item;
    const brief: JsonObject = {
      todo_id: lane.id,
      objective: item.objective ?? null,
      action_kind: item.action_kind,
      task_domain: lane.domain,
      required_capabilities: item.required_capabilities,
      task_repository: lane.repository,
      required_write_scopes: lane.scopes,
      workspace_isolation: lane.scopes.length ? "independent_git_worktree" : "not_required",
      continuation_policy: item.continuation_policy,
      target_key: item.target_key,
    };
    if (
      item.validation_command != null ||
      item.validation_command_argv != null ||
      item.completion_validation_required === true
    ) {
      brief.validation_declared = true;
      brief.validation_label = item.validation_label;
    }
    selected.push({
      todo_id: lane.id,
      task_domain: lane.domain,
      execution_kind: "ephemeral_child",
      child_brief: brief,
    });
    writeLanes.push(lane);
  }
  if (!selected.length) return null;
  return {
    schema_version: "task_orchestration_contract_v2",
    mode: "adaptive",
    coordinator_agent_id: coordinator,
    assignment_key: assignmentKey,
    activation_required: false,
    activation_allowed: true,
    strategy_owner: "task_coordinator",
    max_children: maxChildren,
    primary_todo_id: primary.id,
    child_brief_defaults: childBriefDefaults(input.parent_goal_id ?? null, available),
    eligible_child_lanes: selected,
    blocked_lanes: blocked,
    writeback_owner: "task_coordinator",
    coordinator_obligation:
      "choose whether admitted child lanes shorten the critical path; review returned evidence, " +
      "then write accepted state/todos and spend once",
  };
}

function childBriefDefaults(parentGoalId: JsonObject[string], available: string[]): JsonObject {
  return {
    schema_version: "subagent_control_plane_handoff_v0",
    parent_goal_id: parentGoalId,
    authority_artifact: QUOTA_GOAL_BOUNDARY_ARTIFACT,
    latest_state_ref: "quota_should_run.action_signature.source_hash",
    quota_gate_snapshot: "admitted_by_task_orchestration_contract",
    context_policy: {
      selection_owner: "task_coordinator",
      default: "fresh",
      allowed: [
        "fresh",
        ...(available.includes("subagent_context_fork") ? ["forked_snapshot"] : []),
        ...(available.includes("subagent_resume") ? ["resume"] : []),
      ],
    },
    expected_output: "public_safe_evidence",
    execution_policy: {
      timeout: "bounded_by_host_turn",
      cancel: "task_coordinator_or_host_timeout",
    },
    child_guard_policy: "prevention_first_v0",
    validation_policy: "child reports evidence; parent runs declared todo gate",
    acceptance: [
      "report completed scope and evidence",
      "report validation result and residual risk",
      "do not write LoopX state or spend quota",
    ],
    writeback_spend_contract:
      "child reports evidence only; task coordinator accepts evidence, writes state, and spends once",
  };
}
