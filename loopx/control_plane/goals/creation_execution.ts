/** Owner-confirmed Goal creation adapts to the existing original-Task queue.
 * This request is not Task admission, checker adoption or a host write grant.
 * Historical creations without explicit start_execution retain their behavior.
 */
import {createHash} from "node:crypto";
import type {JsonObject} from "../runtime_decode.ts";
import {jsonObject, requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {goalCreationExecutionReview} from "./creation_execution_review.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";

function verifyOriginalCreationSource(proposal: JsonObject): void {
  const request: JsonObject = {};
  for (const key of ["action_kind", "summary", "normalized_parameters", "context", "expected_state_fingerprint",
    "permission_classification", "validation_evidence", "available_transitions"]) {
    requireThat(Object.hasOwn(proposal, key), "original creation request source is incomplete");
    request[key] = proposal[key];
  }
  for (const key of ["canonical_update_basis", "workspace_binding"]) {
    if (Object.hasOwn(proposal, key)) request[key] = proposal[key];
  }
  requireThat(typeof proposal.request_digest === "string" && proposal.request_digest.length > 0
    && canonicalAuthoritySha256(request) === proposal.request_digest,
  "original creation request source changed");
}

function requireThat(condition: unknown, message: string): asserts condition {
  if (!condition) throw new EffectRuntimeRequestError(message);
}

/** Prepare only after this same creation has retained its Goal, original Todo
 * and Session. The action owner persists the returned value in its checkpoint.
 */
export function prepareGoalCreationExecutionRequest(params: JsonObject): JsonObject | null {
  const proposal = requireJsonObject(params.proposal, "original Goal creation");
  const parameters = requireJsonObject(proposal.normalized_parameters, "original creation parameters");
  const review = goalCreationExecutionReview(parameters);
  if (!review) return null;
  verifyOriginalCreationSource(proposal);
  const session = requireJsonObject(params.session, "original creation Session");
  const goal = requireJsonObject(params.goal, "original registered Goal");
  const selected = requireJsonObject(params.current_original_task, "current original Task selection");
  const steps = requireJsonObject(requireJsonObject(proposal.checkpoint, "original creation checkpoint").steps,
    "original creation steps");
  const workspace = requireJsonObject(steps.workspace_validated, "original workspace checkpoint");
  const bound = requireJsonObject(steps.goal_bootstrapped, "original Goal checkpoint");
  const agent = requireJsonObject(steps.agent_bound, "original Agent checkpoint");
  const todos = requireJsonObject(steps.todos_created, "original Task checkpoint");
  const opened = requireJsonObject(steps.first_session_opened, "original Session checkpoint");
  const proposalId = proposal.proposal_id, todoId = selected.todo_id;
  requireThat(proposal.action_kind === "goal.create" && typeof proposalId === "string"
    && proposalId.length > 0 && proposalId.length <= 80
    && typeof proposal.expected_state_fingerprint === "string" && proposal.expected_state_fingerprint.length > 0
    && ["applying", "applied"].includes(String(proposal.status))
    && Object.hasOwn(goal, "goal_instance_id") && (goal.goal_instance_id === null
      || typeof goal.goal_instance_id === "string" && goal.goal_instance_id.length > 0)
    && goal.goal_id === parameters.goal_id && goal.creation_operation_id === proposalId
    && Array.isArray(goal.registered_agents) && goal.registered_agents.includes(parameters.agent_id)
    && typeof goal.workspace_path === "string" && goal.workspace_path.length > 0
    && workspace.outcome === "workspace_validated"
    && workspace.workspace_digest === createHash("sha256").update(JSON.stringify(goal.workspace_path)).digest("hex")
    && bound.outcome === "goal_bootstrapped" && bound.goal_id === goal.goal_id
    && agent.outcome === "agent_bound" && agent.goal_id === goal.goal_id && agent.agent_id === parameters.agent_id
    && selected.reason === null && typeof todoId === "string" && todoId.length > 0
    && todos.outcome === "todos_created" && Array.isArray(todos.todo_ids)
    && todos.todo_ids.length === 1 && todos.todo_ids[0] === todoId
    && Array.isArray(parameters.initial_todos) && parameters.initial_todos.length === 1
    && session.goal_id === goal.goal_id && (session.goal_instance_id ?? null) === goal.goal_instance_id
    && session.managed_agent_id === parameters.agent_id && session.agent_id === "codex"
    && session.executor_endpoint_id === "codex" && session.session_mode === "managed_runtime"
    && session.upstream_mode === "chat" && session.managed_workspace_path === goal.workspace_path
    && typeof session.session_id === "string" && session.session_id.length > 0
    && session.channel_id === `agent.${parameters.agent_id}`
    && opened.outcome === "first_session_opened" && opened.session_id === session.session_id
    && opened.managed_agent_id === session.managed_agent_id && opened.goal_instance_id === goal.goal_instance_id
    && typeof session.upstream_thread_id === "string" && session.upstream_thread_id.length > 0
    && opened.upstream_thread_id === session.upstream_thread_id,
  "original Task request requires the same retained creation, workspace, Goal lifetime, Agent, Task and Session");
  return {creation_operation_id: proposalId, operation_id: `goal-create-${proposalId}`,
    goal_id: goal.goal_id, goal_instance_id: goal.goal_instance_id, agent_id: parameters.agent_id,
    session_id: session.session_id, todo_id: todoId, permission: review.permission, write_scope: review.writeScope};
}

/** Read the explicit committed request; absence is never inferred from an old
 * button label or Chat prose. The executor separately rechecks live admission.
 */
export function projectGoalCreationExecutionRequest(params: JsonObject): JsonObject | null {
  const proposal = requireJsonObject(params.proposal, "original Goal creation");
  const parameters = requireJsonObject(proposal.normalized_parameters, "original creation parameters");
  if (parameters.start_execution === undefined || parameters.start_execution === false) return null;
  if (proposal.status !== "applied") return null;
  const request = prepareGoalCreationExecutionRequest(params);
  requireThat(request !== null, "original Task execution request is unavailable");
  const steps = requireJsonObject(requireJsonObject(proposal.checkpoint, "original creation checkpoint").steps,
    "original creation steps");
  const checkpoint = requireJsonObject(steps.original_task_requested, "original Task request checkpoint");
  const receipt = requireJsonObject(proposal.receipt, "original creation receipt");
  const resources = requireJsonObject(receipt.resource_ids, "original creation resources");
  const retained = jsonObject(receipt.original_task_request);
  const equal = (value: JsonObject | null): boolean => value !== null
    && Object.keys(value).length === Object.keys(request).length
    && Object.entries(request).every(([key, expected]) => JSON.stringify(value[key]) === JSON.stringify(expected));
  const {outcome, source, ...checkpointRequest} = checkpoint;
  const originalSource = requireJsonObject(source, "original confirmed creation source");
  const committedSteps = requireJsonObject(receipt.step_receipts, "committed creation checkpoints");
  requireThat(outcome === "original_task_requested" && equal(checkpointRequest) && equal(retained)
    && Object.keys(originalSource).length === 3
    && originalSource.request_digest === proposal.request_digest
    && originalSource.expected_state_fingerprint === proposal.expected_state_fingerprint
    && originalSource.upstream_thread_id === requireJsonObject(params.session, "original creation Session").upstream_thread_id
    && canonicalAuthoritySha256(committedSteps.original_task_requested) === canonicalAuthoritySha256(checkpoint)
    && receipt.outcome === "goal_created" && receipt.projection_verified === true
    && resources.goal_id === request.goal_id && resources.goal_instance_id === request.goal_instance_id
    && resources.agent_id === request.agent_id && resources.managed_agent_id === request.agent_id
    && resources.session_id === request.session_id && resources.creation_operation_id === request.creation_operation_id
    && Array.isArray(resources.todo_ids) && resources.todo_ids.length === 1 && resources.todo_ids[0] === request.todo_id,
  "original Task request checkpoint and committed creation receipt must match exactly");
  return request;
}
