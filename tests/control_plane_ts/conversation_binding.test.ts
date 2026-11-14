import assert from "node:assert/strict";
import test from "node:test";
import {createHash} from "node:crypto";
import {planConversationBinding, resolveBoundConversation, planBoundConversationRequest,
  stewardCommand, authorizeStewardCreation, resolveConversationAgentTarget,
  listLocalBoundSessions} from "../../loopx/control_plane/collaboration/conversation_binding.ts";
import {resolveConversationScope, projectConversationIdentity, stewardConversationIdentity} from "../../loopx/control_plane/collaboration/conversation_scope.ts";
import {stewardManagedBasis} from "../../loopx/control_plane/collaboration/steward_managed_mode.ts";

const project = {kind: "project_workspace", project_ref: "a".repeat(24), workspace_path: "/authorized/notes",
  audience: "local_owner", grant: "workspace_read"};
const row = {schema_version: "loopx_chat_conversation_binding_v0", binding_id: "b".repeat(24),
  transport_ref: "notes-app", provider_ref: "c".repeat(24), operator_ref: "d".repeat(24),
  context_kind: "project", project_ref: project.project_ref, executor_endpoint_id: "codex",
  grant: "workspace_read", enabled: true};
const observation = {transport_ref: row.transport_ref, provider_ref: row.provider_ref,
  operator_ref: row.operator_ref, verified: true};

test("legacy context-free partial answers qualify one correction without replaying business", () => {
  const request = "f".repeat(24), client = `external-${request}-recovery-22-review-1`;
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const reserved = {version: 22, history_review: true, read_only_review: true, answer_review: true,
    failed_turn_id: null, native_observation: null, client_turn_id: client};
  const response = {message: "Partial interpretation", proposals: [], gate: {kind: "evidence_gap"}};
  const previous = {turn_id: "original-answer", upstream_turn_id: "native-original", session_id: "original", client_turn_id: client,
    status: "completed", message: "Those four", response};
  const packet = {request: {request_ref: request, binding_id: row.binding_id, source,
      session_id: "original", turn_id: previous.turn_id, message: previous.message, command: null,
      status: "accepted", capture_origin: "provider_history", delivery_verified: true, context_recovery: reserved},
    binding: {...row, context_kind: "steward", grant: "portfolio_read"},
    current_session: {session_id: "original", agent_id: "codex", status: "ready", active_turn_id: null,
      steward_context: {...source, binding_id: row.binding_id, provider_ref: row.provider_ref, operator_ref: row.operator_ref},
      created_at: "2026-01-01T00:00:00Z"},
    history_answer_review: {version: 22, source_verified: true, queue_empty: true, adapter_replaceable: true,
      stop_observed: false, source_created_at: "2026-01-01T00:01:00Z", observed_at: "2026-01-01T00:02:00Z",
      previous_turn: previous}};
  const plan = planBoundConversationRequest(packet);
  assert.equal(plan.operation, "admit_history_answer");
  assert.equal(plan.client_turn_id, `external-${request}-recovery-23-review-1`);
  assert.equal(plan.version, 23);
  assert.equal(plan.context_correction, true);
  assert.equal(plan.previous_answer_turn_id, previous.turn_id);
  const discovery = planBoundConversationRequest({...packet,
    history_answer_review: {...packet.history_answer_review, discovery_only: true}});
  assert.deepEqual(discovery, {pending: true});
  assert.deepEqual(planBoundConversationRequest({...packet,
    history_answer_review: {...packet.history_answer_review, discovery_only: true,
      previous_turn: {...previous, response: {...response, gate: null}}}}), {pending: false});
  const inFlight = {...packet, request: {...packet.request, delivery_verified: false,
    context_recovery_history: [reserved], context_recovery: {...reserved, version: 23, context_version: 1,
      context_correction: true, previous_delivery_verified: true, previous_answer_turn_id: previous.turn_id,
      client_turn_id: `external-${request}-recovery-23-review-1`}},
    current_session: {...packet.current_session, status: "busy", active_turn_id: "reserved-child"}};
  assert.deepEqual(planBoundConversationRequest({...inFlight,
    history_answer_review: {...packet.history_answer_review, discovery_only: true}}), {pending: true});
  assert.equal(planBoundConversationRequest(inFlight).operation, "hold_history_answer");
  for (const changed of [
    {...packet, request: {...packet.request, delivery_verified: false}},
    {...packet, request: {...packet.request, capture_origin: "live"}},
    {...packet, request: {...packet.request, session_id: "foreign"}},
    {...packet, request: {...packet.request, context_recovery: {...reserved, context_version: 1}}},
    {...packet, request: {...packet.request, context_recovery: {...reserved, read_only_review: false}}},
    {...packet, history_answer_review: {...packet.history_answer_review, previous_turn: {...previous, status: "failed"}}},
    {...packet, history_answer_review: {...packet.history_answer_review, previous_turn: {...previous, status: "running"}}},
    {...packet, history_answer_review: {...packet.history_answer_review, previous_turn: {...previous, upstream_turn_id: ""}}},
    {...packet, history_answer_review: {...packet.history_answer_review, previous_turn: {...previous, session_id: "foreign"}}},
    {...packet, history_answer_review: {...packet.history_answer_review, previous_turn: {...previous, response: {...response, gate: null}}}},
    {...packet, history_answer_review: {...packet.history_answer_review, previous_turn: {...previous, response: {...response, context_handoff: [{brief: "write"}]}}}},
    {...packet, history_answer_review: {...packet.history_answer_review, stop_observed: true}},
    {...packet, history_answer_review: {...packet.history_answer_review, adapter_replaceable: false}},
    {...packet, history_answer_review: {...packet.history_answer_review, queue_empty: false}},
  ]) assert.equal(planBoundConversationRequest(changed).operation, "hold_history_answer");
});

test("uncaptured provider history can reserve only one same-owner read-only answer", () => {
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const binding = {...row, context_kind: "steward", grant: "portfolio_read"};
  const context = {...source, binding_id: row.binding_id, provider_ref: row.provider_ref, operator_ref: row.operator_ref};
  const packet = {request: {request_ref: "f".repeat(24), binding_id: row.binding_id, source,
      message: "Explain the existing result", command: null, status: "prepared"}, binding,
    current_session: {session_id: "original", agent_id: "codex", status: "ready", active_turn_id: null,
      steward_context: context, created_at: "2026-01-01T00:00:00Z"},
    history_answer_review: {version: 22, source_verified: true, queue_empty: true, adapter_replaceable: true, stop_observed: false,
      source_created_at: "2026-01-01T00:01:00Z", observed_at: "2026-01-01T00:02:00Z"}};
  const plan = planBoundConversationRequest(packet);
  assert.equal(plan.operation, "admit_history_answer");
  assert.equal(plan.client_turn_id, `external-${"f".repeat(24)}-recovery-22-review-1`);
  assert.equal(plan.work_attempt, undefined);
  for (const mutate of [
    (p: typeof packet) => {p.binding.context_kind = "project";},
    (p: typeof packet) => {p.binding.grant = "workspace_write";},
    (p: typeof packet) => {p.request.source.sender_ref = "a".repeat(24);},
    (p: typeof packet) => {p.current_session.steward_context.source_ref = "a".repeat(24);},
    (p: typeof packet) => {p.current_session.agent_id = "antigravity";},
    (p: typeof packet) => {p.history_answer_review.stop_observed = true;},
    (p: typeof packet) => {p.history_answer_review.queue_empty = false;},
    (p: typeof packet) => {p.history_answer_review.adapter_replaceable = false;},
    (p: typeof packet) => {p.history_answer_review.source_verified = false;},
    (p: typeof packet) => {p.history_answer_review.source_created_at = "2025-12-31T23:59:00Z";},
    (p: typeof packet) => {p.request.message = "/stop";},
  ]) {
    const negative = structuredClone(packet);
    mutate(negative);
    assert.equal(planBoundConversationRequest(negative).operation, "hold_history_answer");
  }
});
const current = {schema_version: "loopx_chat_conversation_bindings_v0", revision: 0, bindings: []};
const request = {current, expected_revision: 0, operation: "configure", binding: row, observation,
  available_projects: [project]};

test("a completed project partial result admits one restricted review while retaining business", () => {
  const selected = {...row, grant: "workspace_write"};
  const available = {...project, grant: "workspace_write"};
  const configured = planConversationBinding({...request, binding: selected, available_projects: [available]}).state;
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
    observation, available_projects: [available], ...source}).context;
  const ref = "f".repeat(24), message = "Verify the requested update";
  const work = {remaining: "Confirm the original update", dependency: "Source read failed", effects_settled: true};
  const session = {session_id: "original", goal_id: null, agent_id: "codex", upstream_mode: "chat",
    status: "ready", active_turn_id: null, upstream_thread_id: "native", project_context: context,
    channel_id: `project.external.${row.binding_id}.${source.source_ref}`};
  const completed = {session_id: "original", turn_id: "partial", client_turn_id: `external-${ref}`,
    message, origin: "lark", status: "completed", upstream_turn_id: "native-partial",
    completed_at: "2026-01-01T00:00:00Z", response: {schema_version: "loopx_chat_agent_response_v0",
      message: "Only part was confirmed", proposals: [], gate: null, protected_action: null, unfinished_work: work}};
  const proof = {thread_id: "native", turn_id: "native-partial", read_only_review: true,
    completed_result_review: true, unfinished_work: work, visible_response: "Original partial report",
    native_inventory_sha256: "1".repeat(64)};
  const packet = {request: {request_ref: ref, binding_id: row.binding_id, source, session_id: "original",
    turn_id: "partial", message, origin: "lark", command: null, status: "accepted", delivery_verified: true},
    binding: selected, current_session: session, context_recovery: {version: 22, failed_turn: completed,
      observed_at: "2026-01-01T00:10:00Z", source_verified: true, steering_observed: false,
      stop_observed: false, attachments_present: false, queued_input_present: false,
      later_equivalent_input_present: false, live_work_owner: false, native_observation: proof}};
  const plan = planBoundConversationRequest(packet);
  assert.equal(plan.recovery_eligible, true);
  assert.equal(plan.read_only_review, true);
  assert.equal(plan.work_attempt, undefined);
  assert.equal(plan.answer_review, undefined, "A project never borrows the steward runtime");
  assert.equal(plan.client_turn_id, `external-${ref}-recovery-22-review-1`);
  for (const owner of [true, undefined]) {
    const held = planBoundConversationRequest({...packet,
      context_recovery: {...packet.context_recovery, live_work_owner: owner}});
    assert.equal(held.recovery_eligible, false, "A live or unknown writer cannot be replaced with a result review");
    assert.equal(held.recovery_candidate, false);
  }
  assert.equal(planBoundConversationRequest({...packet, context_recovery: {
    ...packet.context_recovery, later_equivalent_input_present: true}}).recovery_eligible, true,
    "A later equivalent request holds execution, not its old answer-only review");
  for (const change of [{source_verified: false}, {stop_observed: true}, {steering_observed: true},
    {attachments_present: true}, {queued_input_present: true}, {observed_at: "2026-01-01T00:00:59Z"},
    ...[{thread_id: "foreign"}, {turn_id: "foreign"}, {read_only_review: false},
      {all_effects_settled: true}, {completed_work: true}, {visible_response: ""},
      {unfinished_work: {...work, remaining: "Different work"}}, {native_inventory_sha256: undefined}]
      .map(value => ({native_observation: {...proof, ...value}})),
    ...[{client_turn_id: "foreign"}, {message: "Changed"}, {origin: "web"}]
      .map(value => ({failed_turn: {...completed, ...value}}))]) {
    assert.notEqual(planBoundConversationRequest({...packet,
      context_recovery: {...packet.context_recovery, ...change}}).recovery_eligible, true);
  }
  const reservation = {version: 22, failed_turn_id: "partial", read_only_review: true,
    client_turn_id: plan.client_turn_id, original_turn: completed, unfinished_work: work};
  const reviewed = {session_id: "original", turn_id: "review", client_turn_id: plan.client_turn_id,
    message, origin: "lark", status: "completed", response: {message: "Verified existing result", proposals: [], gate: null}};
  const retained = {request: {...packet.request, turn_id: "review", context_recovery: reservation},
    current_session: session, pending_observation: {turn: reviewed, stop_observed: false}};
  assert.equal(planBoundConversationRequest(retained).pending, true, "A review never settles original business effects");
  assert.equal(planBoundConversationRequest({...retained, pending_observation: {
    ...retained.pending_observation, turn: {...reviewed, status: "failed", response: null}}}).pending, true);
  assert.equal(planBoundConversationRequest({...retained, pending_observation: {
    ...retained.pending_observation, stop_observed: true}}).pending, false);
  assert.equal(planBoundConversationRequest({...packet, request: retained.request,
    context_recovery: {...packet.context_recovery, failed_turn: reviewed}}).recovery_candidate, false);

  // A review does not consume the original single writable continuation.
  // Its original live owner must independently settle the native effects first.
  const reviewedTurn = {...reviewed, upstream_turn_id: "native-review",
    response: {schema_version: "loopx_chat_agent_response_v0", ...reviewed.response}};
  const retainedReview = {reservation: {...reservation, binding_snapshot: selected}, turn: reviewedTurn};
  const reservedReview = {...reviewedTurn, status: "queued"};
  const reserved = planBoundConversationRequest({...packet,
    request: {...packet.request, context_recovery: retainedReview.reservation},
    current_session: {...session, status: "busy", active_turn_id: reservedReview.turn_id},
    context_recovery: {...packet.context_recovery, live_work_owner: true, admitted_turn: reservedReview}});
  assert.equal(reserved.admitted_turn_id, reservedReview.turn_id, "An existing review keeps its exact queue receipt");
  assert.equal(reserved.work_attempt, undefined);
  const settled = {thread_id: "native", turn_id: completed.upstream_turn_id,
    completed_work: true, all_effects_settled: true, unfinished_work: work,
    native_inventory_sha256: "2".repeat(64)};
  const direct = planBoundConversationRequest({...packet,
    context_recovery: {...packet.context_recovery, live_work_owner: true, native_observation: settled}});
  assert.equal(direct.recovery_eligible, true, "Fresh writable proof still admits the original work-1");
  assert.equal(direct.work_attempt, 1);
  const afterReview = {...packet, request: {...retained.request, context_recovery: retainedReview.reservation},
    context_recovery: {...packet.context_recovery, retained_result_review: retainedReview,
      live_work_owner: true, native_observation: settled}};
  const resumed = planBoundConversationRequest(afterReview);
  assert.equal(resumed.recovery_eligible, true);
  assert.equal(resumed.work_attempt, 1);
  assert.equal(resumed.failed_turn_id, completed.turn_id);
  assert.equal(resumed.completed_review_turn_id, reviewed.turn_id);
  assert.equal(resumed.read_only_review, undefined);
  for (const change of [{live_work_owner: false}, {source_verified: false}, {stop_observed: true},
    {steering_observed: true}, {queued_input_present: true}, {later_equivalent_input_present: true},
    {attachments_present: true}, {native_observation: proof},
    {native_observation: {...settled, all_effects_settled: false}},
    {native_observation: {...settled, turn_id: "different-original"}},
    {retained_result_review: {...retainedReview, turn: {...reviewedTurn, status: "failed"}}},
    {retained_result_review: {...retainedReview, reservation: {...retainedReview.reservation, answer_review: true}}},
    {retained_result_review: {...retainedReview, reservation: {...retainedReview.reservation,
      unfinished_work: {...work, remaining: "Unrelated work"}}}},
    {retained_result_review: {...retainedReview, reservation: {...retainedReview.reservation,
      binding_snapshot: {...selected, project_ref: "0".repeat(24)}}}},
  ]) assert.notEqual(planBoundConversationRequest({...afterReview,
    context_recovery: {...afterReview.context_recovery, ...change}}).recovery_eligible, true);
  const workReservation = {version: 22, failed_turn_id: completed.turn_id, work_attempt: 1,
    unfinished_work: work, client_turn_id: resumed.client_turn_id, completed_review_turn_id: reviewed.turn_id,
    original_turn: completed, binding_snapshot: selected};
  const admitted = {...reviewedTurn, turn_id: "remaining", client_turn_id: resumed.client_turn_id, status: "queued"};
  const crash = {...afterReview, request: {...afterReview.request, context_recovery: workReservation,
      context_recovery_history: [retainedReview.reservation]},
    current_session: {...session, status: "busy", active_turn_id: admitted.turn_id},
    context_recovery: {...afterReview.context_recovery, native_observation: null, live_work_owner: false,
      admitted_turn: admitted, queued_input_present: true, later_equivalent_input_present: true}};
  assert.equal(planBoundConversationRequest(crash).admitted_turn_id, admitted.turn_id);
  assert.equal(planBoundConversationRequest({...crash, context_recovery: {
    ...crash.context_recovery, source_verified: false}}).recovery_eligible, false);
  assert.equal(planBoundConversationRequest({...crash, request: {
    ...crash.request, turn_id: admitted.turn_id}}).recovery_candidate, false);
  const failedContinuation = {...retained, request: {...crash.request, turn_id: admitted.turn_id},
    pending_observation: {turn: {...admitted, status: "failed", response: null, error_code: "host_gate"}, stop_observed: false}};
  assert.equal(planBoundConversationRequest(failedContinuation).pending, true);
});

test("a restarted Codex steward retains one restricted answer instead of replaying business", () => {
  const selected = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: []};
  const configured = planConversationBinding({...request, binding: selected}).state;
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
    observation, available_projects: [project], ...source}).context;
  const ref = "f".repeat(24), message = "Explain the existing results";
  const session = {session_id: "original", goal_id: "loopx-manager", agent_id: "codex", upstream_mode: "chat",
    status: "ready", active_turn_id: null, upstream_thread_id: "native", steward_context: context,
    channel_id: `manager.external.native.${row.binding_id}.${source.source_ref}`};
  const failed = {session_id: "original", turn_id: "failed", client_turn_id: `external-${ref}`,
    message, origin: "lark", status: "failed", error_code: "server_restarted", response: null,
    delta_count: 1, upstream_turn_id: "interrupted", completed_at: "2026-01-01T00:00:00Z"};
  const proof = {thread_id: "native", turn_id: "interrupted", read_only_review: true,
    no_final_answer: true, effects_observed: true, native_inventory_sha256: "1".repeat(64)};
  const allocation = {schema_version: "manager_executor_allocation_v0", selection_policy: "flexible",
    configured_endpoint: "antigravity", executor_endpoint: "codex", allocation_reason: "flexible_availability_fallback",
    configuration_revision: "current-pool", available: true, eligible_endpoints: ["antigravity", "codex"]};
  const configuration = {status: "ready", selection_policy: "flexible", executor_endpoint: "antigravity",
    eligible_endpoints: ["antigravity", "codex"], configuration_revision: "current-pool"};
  const packet = {request: {request_ref: ref, command: null, status: "accepted", delivery_verified: true,
    message, origin: "lark", binding_id: row.binding_id, session_id: "original", turn_id: "failed", source},
    binding: selected, current_session: session, context_recovery: {version: 22, failed_turn: failed,
      observed_at: "2026-01-01T00:10:00Z", source_verified: true, steering_observed: false,
      stop_observed: false, attachments_present: false, queued_input_present: false, native_observation: proof,
      metadata_capacity: {alternate_executor: {allocation, observed_at: Date.parse("2026-01-01T00:10:00Z") / 1000}},
      executor_configuration: configuration}};
  const plan = planBoundConversationRequest(packet);
  assert.equal(plan.recovery_eligible, true);
  assert.equal(plan.answer_review, true);
  assert.equal(plan.read_only_review, true);
  assert.equal(plan.answer_executor, "codex");
  assert.equal(plan.work_attempt, undefined);
  assert.equal(plan.quota_attempt, undefined);
  assert.equal(plan.client_turn_id, `external-${ref}-recovery-22-review-1`);
  for (const change of [{source_verified: false}, {stop_observed: true}, {steering_observed: true},
    {attachments_present: true}, {observed_at: "2026-01-01T00:00:59Z"}, {metadata_capacity: null},
    ...[{thread_id: "foreign"}, {turn_id: "other"}, {no_final_answer: false},
      {effects_observed: false}, {read_only_review: false}, {native_inventory_sha256: undefined}]
      .map(value => ({native_observation: {...proof, ...value}})),
    ...[{delta_count: 0}, {client_turn_id: "foreign"}, {message: "changed"}, {origin: "web"},
      {response: {}}, {error_code: "runtime_error"}].map(value => ({failed_turn: {...failed, ...value}}))]) {
    assert.notEqual(planBoundConversationRequest({...packet,
      context_recovery: {...packet.context_recovery, ...change}}).recovery_eligible, true);
  }
  assert.notEqual(planBoundConversationRequest({...packet,
    current_session: {...session, status: "busy", active_turn_id: "later"}}).recovery_eligible, true);
  const previous = {version: 22, failed_turn_id: "failed", client_turn_id: plan.client_turn_id,
    answer_review: true, read_only_review: true};
  const admitted = {session_id: "original", turn_id: "review", client_turn_id: plan.client_turn_id,
    message, origin: "lark"};
  const reserved = {...packet, request: {...packet.request, context_recovery: previous},
    context_recovery: {...packet.context_recovery, admitted_turn: admitted}};
  assert.equal(planBoundConversationRequest(reserved).admitted_turn_id, "review");
  assert.equal(planBoundConversationRequest({...reserved, request: {...reserved.request, turn_id: "review"},
    context_recovery: {...reserved.context_recovery, admitted_turn: null,
      failed_turn: {...failed, turn_id: "review", client_turn_id: plan.client_turn_id}}}).recovery_candidate, false);
});

test("an original project account-preparation failure shares its existing recovery budget", () => {
  const configured = planConversationBinding(request).state;
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
    observation, available_projects: [project], ...source}).context;
  const ref = "f".repeat(24), original = `external-${ref}`;
  const session = {session_id: "original", goal_id: null, agent_id: "codex", upstream_mode: "chat",
    status: "ready", upstream_thread_id: "native", active_turn_id: null,
    channel_id: `project.external.${row.binding_id}.${source.source_ref}`, project_context: context};
  const quota = {version: 22, failed_turn_id: "quota-failed", quota_attempt: 1,
    client_turn_id: `${original}-recovery-22-quota-1`};
  const failed = {session_id: "original", turn_id: "preparation-failed", client_turn_id: quota.client_turn_id,
    message: "Original material", origin: "lark", status: "failed", delta_count: 0, response: null,
    upstream_turn_id: null, started_at: null, first_event_at: null, error_code: "resume_failed",
    error: "The previous Agent conversation could not be restored.", completed_at: "2026-01-01T00:00:00Z"};
  const proof = {kind: "core_runtime_preparation_failure", session_id: "original", turn_id: failed.turn_id,
    events: [{event_id: "queued", kind: "turn.queued", delivery_mode: "session_queue"},
      {event_id: "failed", kind: "turn.failed", error_code: failed.error_code, message: failed.error}]};
  const packet = {request: {request_ref: ref, command: null, status: "accepted", delivery_verified: true,
    message: failed.message, origin: "lark", binding_id: row.binding_id, session_id: "original",
    turn_id: failed.turn_id, source, context_recovery: quota}, binding: row, current_session: session,
    context_recovery: {version: 22, failed_turn: failed, observed_at: "2026-01-01T00:10:00Z",
      source_verified: true, steering_observed: false, stop_observed: false, attachments_present: false,
      native_result_present: false, queued_input_present: false, later_equivalent_input_present: false,
      preparation_scope_available: true, preparation_observation: proof}};
  const plan = planBoundConversationRequest(packet);
  assert.equal(plan.recovery_eligible, true);
  assert.equal(plan.preparation_attempt, 2, "quota attempt one is retained, never reset");
  assert.equal(plan.not_before, "2026-01-01T00:02:00.000Z");
  assert.deepEqual(plan.native_thread_ids, []);
  for (const change of [{source_verified: false}, {stop_observed: true}, {steering_observed: true},
    {attachments_present: true}, {native_result_present: true}, {native_result_present: undefined},
    {queued_input_present: true}, {later_equivalent_input_present: true}, {preparation_scope_available: false},
    {observed_at: "2026-01-01T00:01:59Z"}, {preparation_observation: null},
    {preparation_observation: {...proof, events: [...proof.events, {event_id: "started", kind: "turn.started"}]}},
    {preparation_observation: {...proof, events: [proof.events[0], {...proof.events[1], event_id: "queued"}]}},
    {preparation_observation: {...proof, events: [{...proof.events[0], delivery_mode: "other"}, proof.events[1]]}},
    ...[{started_at: "2026-01-01T00:00:00Z"}, {started_at: undefined}, {first_event_at: "started"},
      {upstream_turn_id: "native-started"}, {delta_count: 1}, {response: {}}, {client_turn_id: original},
      {error: "Unknown history failure"}, {origin: "web"}].map(value => ({failed_turn: {...failed, ...value}}))]) {
    assert.notEqual(planBoundConversationRequest({...packet,
      context_recovery: {...packet.context_recovery, ...change}}).recovery_eligible, true);
  }
  const reservation = {version: 22, failed_turn_id: failed.turn_id, preparation_attempt: 2,
    client_turn_id: plan.client_turn_id};
  const reserved = {...packet, request: {...packet.request, context_recovery: reservation,
    context_recovery_history: [quota]}};
  const admitted = {session_id: "original", turn_id: "continued", client_turn_id: plan.client_turn_id,
    message: failed.message, origin: "lark", status: "completed"};
  assert.equal(planBoundConversationRequest({...reserved,
    current_session: {...session, active_turn_id: "continued", status: "busy"},
    context_recovery: {...reserved.context_recovery, admitted_turn: admitted, stop_observed: true,
      queued_input_present: true}}).admitted_turn_id, "continued");
  const next = {...reserved, request: {...reserved.request, turn_id: "continued"},
    context_recovery: {...reserved.context_recovery,
      failed_turn: {...failed, turn_id: "continued", client_turn_id: plan.client_turn_id},
      preparation_observation: {...proof, turn_id: "continued"}}};
  assert.equal(planBoundConversationRequest(next).preparation_attempt, 3);
  const third = {version: 22, failed_turn_id: "continued", preparation_attempt: 3,
    client_turn_id: `${original}-recovery-22-preparation-3`};
  assert.equal(planBoundConversationRequest({...next, request: {...next.request, turn_id: "last",
    context_recovery: third, context_recovery_history: [quota, reservation]},
    context_recovery: {...next.context_recovery,
      failed_turn: {...failed, turn_id: "last", client_turn_id: third.client_turn_id},
      preparation_observation: {...proof, turn_id: "last"}}}).recovery_candidate, false);
  for (const error of ["usage_limit_exceeded", "host_gate", "upstream_transport_failure"]) {
    assert.equal(planBoundConversationRequest({...next, context_recovery: {...next.context_recovery,
      failed_turn: {...next.context_recovery.failed_turn, started_at: "started", upstream_turn_id: "native", error_code: error},
      native_observation: {thread_id: "native", turn_id: "native", only_original_user_item: true,
        codex_error_info: "usageLimitExceeded", quota_available: true, quota_observed_at: 1767226200}}}).recovery_candidate, false);
  }
});

test("exhausted business quota permits one read-only answer, never a fresh business budget", () => {
  const selected = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: []};
  const configured = planConversationBinding({...request, binding: selected}).state;
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
    observation, available_projects: [project], ...source}).context;
  const ref = "f".repeat(24), message = "Explain the existing change";
  const previous = {version: 22, failed_turn_id: "second-failure", quota_attempt: 3,
    client_turn_id: `external-${ref}-recovery-22-quota-3`};
  const session = {session_id: "original", goal_id: "loopx-manager", agent_id: "codex", upstream_mode: "chat",
    status: "ready", upstream_thread_id: "native", active_turn_id: null,
    channel_id: `manager.external.native.${row.binding_id}.${source.source_ref}`, steward_context: context};
  const failed = {session_id: "original", turn_id: "third-failure", client_turn_id: previous.client_turn_id,
    message, origin: "lark", status: "failed", delta_count: 0, response: null, upstream_turn_id: "native-failed",
    error_code: "usage_limit_exceeded", completed_at: "2026-01-01T00:00:00Z"};
  const at = Date.parse("2026-01-01T00:10:00Z") / 1000;
  const proof = {thread_id: "native", turn_id: "native-failed", codex_error_info: "usageLimitExceeded",
    only_original_user_item: true, quota_available: true, quota_observed_at: at};
  const allocation = {schema_version: "manager_executor_allocation_v0", selection_policy: "flexible",
    configured_endpoint: "antigravity", executor_endpoint: "codex", allocation_reason: "flexible_availability_fallback",
    configuration_revision: "current-pool", available: true, eligible_endpoints: ["antigravity", "codex"]};
  const configuration = {status: "ready", selection_policy: "flexible", executor_endpoint: "antigravity",
    eligible_endpoints: ["antigravity", "codex"], configuration_revision: "current-pool"};
  const capacity = {alternate_executor: {allocation, observed_at: at}};
  const packet = {request: {request_ref: ref, command: null, status: "accepted", delivery_verified: true,
    message, origin: "lark", binding_id: row.binding_id, session_id: "original", turn_id: failed.turn_id,
    source, context_recovery: previous}, binding: selected, current_session: session,
    context_recovery: {version: 22, failed_turn: failed, observed_at: "2026-01-01T00:10:00Z",
      steering_observed: false, stop_observed: false, attachments_present: false, queued_input_present: false,
      source_verified: true, native_observation: proof, metadata_capacity: capacity, executor_configuration: configuration}};
  const plan = planBoundConversationRequest(packet);
  assert.equal(plan.recovery_eligible, true);
  assert.equal(plan.answer_review, true);
  assert.equal(plan.read_only_review, true);
  assert.equal(plan.answer_executor, "codex");
  assert.equal(plan.quota_attempt, undefined);
  assert.equal(previous.quota_attempt, 3);
  assert.equal(plan.client_turn_id, `external-${ref}-recovery-22-review-1`);
  const migrated = {...packet, current_session: {...session, upstream_thread_id: "later-review",
    upstream_context_history: [{thread_id: "native", manager_context_version: 22}]}};
  assert.equal(planBoundConversationRequest(migrated).recovery_eligible, true,
    "a later restricted answer must not strand the other exact historical failures");
  assert.notEqual(planBoundConversationRequest({...migrated, current_session: {...migrated.current_session,
    upstream_context_history: [{thread_id: "native", manager_context_version: 22, agent_id: "antigravity"}]}}).recovery_eligible, true);
  const history = {...session, upstream_context_history: [{thread_id: "123", manager_context_version: 22}]};
  for (const thread_id of [123, null, false, {}, [], "foreign"]) {
    const prior = planBoundConversationRequest({...packet, current_session: history,
      request: {...packet.request, context_recovery: {...previous, native_observation: {thread_id}}}});
    assert.deepEqual(prior.native_thread_ids, ["native", "123"],
      "an untrusted prior identity cannot reorder validated string identities");
    const proof = planBoundConversationRequest({...packet, current_session: {...session, upstream_thread_id: "123"},
      context_recovery: {...packet.context_recovery,
        native_observation: {...packet.context_recovery.native_observation, thread_id}}});
    assert.equal(proof.recovery_candidate, true);
    assert.equal(proof.recovery_eligible, false, "a proof must match without identity coercion");
  }
  assert.deepEqual(planBoundConversationRequest({...packet, current_session: history,
    request: {...packet.request, context_recovery: {...previous,
      native_observation: {thread_id: "123"}}}}).native_thread_ids, ["123", "native"]);
  assert.equal(planBoundConversationRequest({...packet,
    current_session: {...session, upstream_thread_id: "123"},
    context_recovery: {...packet.context_recovery, native_observation: {...proof, thread_id: "123"}}}).recovery_eligible, true,
  "numeric-looking string identities remain valid");
  for (const change of [{source_verified: false}, {stop_observed: true}, {steering_observed: true},
    {attachments_present: true}, {queued_input_present: true}, {later_equivalent_input_present: true},
    {metadata_capacity: null},
    {executor_configuration: {...configuration, configuration_revision: "changed"}},
    {metadata_capacity: {alternate_executor: {...capacity.alternate_executor, observed_at: at - 121}}},
    {metadata_capacity: {alternate_executor: {...capacity.alternate_executor, observed_at: at + 1}}},
    ...[{thread_id: "foreign"}, {thread_id: null}, {thread_id: 7}, {turn_id: "foreign"}, {only_original_user_item: false},
      {codex_error_info: "other"}].map(value => ({native_observation: {...proof, ...value}})),
    ...[{delta_count: 1}, {response: {}}, {status: "running"}, {message: "changed"},
      {error_code: "context_window_exceeded"},
      {client_turn_id: "uncorrelated"}].map(value => ({failed_turn: {...failed, ...value}}))]) {
    assert.notEqual(planBoundConversationRequest({...packet,
      context_recovery: {...packet.context_recovery, ...change}}).recovery_eligible, true);
  }
  const reservation = {version: 22, failed_turn_id: failed.turn_id, client_turn_id: plan.client_turn_id,
    answer_review: true, read_only_review: true};
  const reserved = {...packet, request: {...packet.request, context_recovery: reservation,
    context_recovery_history: [previous]}};
  assert.equal(planBoundConversationRequest(reserved).recovery_eligible, true);
  const admitted = {session_id: "original", turn_id: "review", client_turn_id: plan.client_turn_id, message, origin: "lark"};
  assert.equal(planBoundConversationRequest({...reserved, current_session: {...session, status: "busy", active_turn_id: "review"},
    context_recovery: {...reserved.context_recovery, native_observation: null,
      admitted_turn: admitted, later_equivalent_input_present: true}}).admitted_turn_id, "review");
  assert.notEqual(planBoundConversationRequest({...reserved, request: {...reserved.request,
    context_recovery_history: []}}).recovery_eligible, true);
  const failedReview = {...failed, ...admitted};
  assert.equal(planBoundConversationRequest({...reserved, request: {...reserved.request, turn_id: "review"},
    context_recovery: {...reserved.context_recovery, failed_turn: failedReview}}).recovery_candidate, false);
  assert.equal(planBoundConversationRequest({...packet,
    current_session: {...session, status: "busy", active_turn_id: "another"}}).recovery_candidate, false);
});

test("settled historical native work permits only one restricted steward answer review", () => {
  const selected = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: [], executor_endpoint_id: "antigravity"};
  const configured = planConversationBinding({...request, binding: selected}).state;
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
    observation, available_projects: [project], ...source}).context;
  const ref = "f".repeat(24), message = "Analyze existing evidence";
  const session = {session_id: "original", goal_id: "loopx-manager", agent_id: "antigravity",
    agent_model_id: "gemini-4-argon-high", upstream_mode: "default", status: "ready", upstream_thread_id: "native",
    active_turn_id: null, channel_id: `manager.external.native.${row.binding_id}.${source.source_ref}`, steward_context: context};
  const failed = {session_id: "original", turn_id: "failed", client_turn_id: `external-${ref}`, message, origin: "lark",
    status: "failed", delta_count: 0, response: null, upstream_turn_id: "native-failed", error_code: "antigravity_step_error",
    started_at: "2026-01-01T00:00:00Z", completed_at: "2026-01-01T00:00:10Z"};
  const at = Date.parse("2026-01-01T00:10:00Z") / 1000;
  const anchor = {thread_id: "native", start_step_index: 10, input_sha256: "a".repeat(64)};
  const proof = {...anchor, request_ref: ref, execution_id: "original-execution", end_step_index: 14, total_steps: 16,
    input_created_at: "2026-01-01T00:00:05Z", read_only_review: true, no_final_answer: true, effects_observed: true,
    visible_results: [{commandLine: "already ran", exitCode: 0, combinedOutput: "observed"}]};
  const allocation = {schema_version: "manager_executor_allocation_v0", selection_policy: "flexible",
    configured_endpoint: "antigravity", executor_endpoint: "codex", allocation_reason: "flexible_availability_fallback",
    configuration_revision: "current-pool", available: true, eligible_endpoints: ["antigravity", "codex"]};
  const configuration = {status: "ready", selection_policy: "flexible", executor_endpoint: "antigravity",
    eligible_endpoints: ["antigravity", "codex"], configuration_revision: "current-pool"};
  const capacity = {model_id: session.agent_model_id, account_ref: "c".repeat(64), remaining_fraction: 0,
    reset_time: "2026-01-08T00:00:00Z", observed_at: at, quota_exhausted: true, normal_completion: false,
    alternate_executor: {allocation, observed_at: at}};
  const packet = {request: {request_ref: ref, command: null, status: "accepted", delivery_verified: true,
    message, origin: "lark", binding_id: row.binding_id, session_id: "original", turn_id: "failed", source},
    binding: selected, current_session: session, context_recovery: {version: 22, failed_turn: failed,
      observed_at: "2026-01-01T00:10:00Z", steering_observed: false, stop_observed: false, attachments_present: false,
      source_verified: true, queued_input_present: false, later_source_input_present: true,
      native_input: anchor, native_observation: proof, metadata_capacity: capacity, executor_configuration: configuration}};
  const plan = planBoundConversationRequest(packet);
  assert.equal(plan.recovery_eligible, true);
  assert.equal(plan.answer_executor, "codex");
  assert.equal(plan.answer_review, true);
  assert.equal(plan.read_only_review, true);
  assert.equal(plan.quota_attempt, undefined);
  assert.equal(plan.client_turn_id, `external-${ref}-recovery-22-review-1`);
  const runtimeFailure = {...packet, context_recovery: {...packet.context_recovery,
    failed_turn: {...failed, error_code: "runtime_error"},
    native_observation: {...proof, visible_results: [{visible_response: "Existing visible draft"}]}}};
  const draftReview = planBoundConversationRequest(runtimeFailure);
  assert.equal(draftReview.recovery_eligible, true);
  assert.equal(draftReview.answer_review, true);
  assert.equal(draftReview.read_only_review, true);
  assert.equal(draftReview.quota_attempt, undefined);
  for (const result of [{visible_response: ""}, {visible_response: null},
    {visible_response: "draft", newEffect: true}, {visible_response: "x".repeat(256 * 1024)}]) {
    assert.notEqual(planBoundConversationRequest({...runtimeFailure,
      context_recovery: {...runtimeFailure.context_recovery,
        native_observation: {...proof, visible_results: [result]}}}).recovery_eligible, true);
  }
  for (const change of [{source_verified: false}, {stop_observed: true}, {steering_observed: true},
    {attachments_present: true}, {queued_input_present: true}, {metadata_capacity: null}]) {
    assert.notEqual(planBoundConversationRequest({...runtimeFailure,
      context_recovery: {...runtimeFailure.context_recovery, ...change}}).recovery_eligible, true);
  }
  const fileResult = {file_uri: "file:///authorized/existing-result.txt", content: "Original visible file result"};
  assert.equal(planBoundConversationRequest({...packet, context_recovery: {...packet.context_recovery,
    native_observation: {...proof, visible_results: [...proof.visible_results, fileResult]}}}).recovery_eligible, true);
  assert.equal(planBoundConversationRequest({...packet, context_recovery: {...packet.context_recovery,
    native_observation: {...proof, visible_results: [fileResult]}}}).recovery_eligible, true);
  for (const result of [{...fileResult, file_uri: "https://untrusted.invalid/result.txt"},
    {...fileResult, file_uri: "file://remote-host/result.txt"}, {...fileResult, file_uri: "file:///result.txt?next=run"},
    {...fileResult, content: null}, {file_uri: fileResult.file_uri}, {...fileResult, newEffect: true},
    {...fileResult, content: "x".repeat(256 * 1024)}]) {
    assert.notEqual(planBoundConversationRequest({...packet, context_recovery: {...packet.context_recovery,
      native_observation: {...proof, visible_results: [result]}}}).recovery_eligible, true);
  }
  assert.equal(planBoundConversationRequest({...packet, context_recovery: {...packet.context_recovery,
    metadata_capacity: {alternate_executor: capacity.alternate_executor}}}).recovery_eligible, true,
    "a verified alternate route does not depend on an unrelated primary quota read");
  for (const change of [{source_verified: false}, {stop_observed: true}, {steering_observed: true},
    {attachments_present: true}, {queued_input_present: true}, {native_input: null}, {metadata_capacity: null},
    {metadata_capacity: {...capacity, alternate_executor: null}},
    ...[{visible_results: []}, {visible_results: [{commandLine: "unknown", exitCode: null}]}, {thread_id: "other"},
      {input_sha256: "b".repeat(64)}, {no_final_answer: false}, {effects_observed: false},
      {end_step_index: 12}, {end_step_index: 17}].map(value => ({native_observation: {...proof, ...value}}))]) {
    assert.notEqual(planBoundConversationRequest({...packet,
      context_recovery: {...packet.context_recovery, ...change}}).recovery_eligible, true);
  }
  const policy = stewardConversationIdentity({context, answer_review: true});
  const config = policy.host_config as {features: Record<string, unknown>;
    permissions: {loopx_workspace_only_read: {network: {enabled: boolean}}}};
  assert.equal(config.features.shell_tool, false);
  assert.equal(config.features.apply_patch_freeform, false);
  assert.equal(config.permissions.loopx_workspace_only_read.network.enabled, false);
  const host = stewardConversationIdentity({context, answer_review: true, answer_review_host_read: true});
  assert.equal(host.permissions_profile, "loopx_steward_host_read");
  const hostConfig = host.host_config as {features: Record<string, unknown>;
    permissions: {loopx_steward_host_read: {filesystem: Record<string, unknown>; network: {enabled: boolean}}}};
  assert.equal(hostConfig.features.shell_tool, true);
  assert.equal(hostConfig.features.apply_patch_freeform, false);
  assert.equal(hostConfig.permissions.loopx_steward_host_read.filesystem[":root"], "read");
  assert.equal(hostConfig.permissions.loopx_steward_host_read.network.enabled, false);
  assert.equal(stewardConversationIdentity({context, metadata_review: true, answer_review_host_read: true}).permissions_profile,
    "loopx_workspace_only_read", "a formatter cannot gain the trusted-owner read subset");
  assert.throws(() => stewardConversationIdentity({context, metadata_review: true, answer_review: true,
    answer_review_host_read: true}), /exclusive/);
  const reservation = {version: 22, failed_turn_id: "failed", client_turn_id: plan.client_turn_id,
    answer_review: true, read_only_review: true};
  const admitted = {session_id: "original", turn_id: "reviewed", client_turn_id: plan.client_turn_id, message, origin: "lark"};
  assert.equal(planBoundConversationRequest({...packet, request: {...packet.request, context_recovery: reservation},
    context_recovery: {...packet.context_recovery, native_observation: null, admitted_turn: admitted}}).admitted_turn_id, "reviewed");
  const pending = {request: {...packet.request, turn_id: "reviewed", context_recovery: reservation},
    current_session: session, pending_observation: {stop_observed: false,
      turn: {...admitted, status: "completed", response: {message: "Partial direct answer", gate: {kind: "host_tool_gate", summary: "Missing evidence"}}}}};
  assert.equal(planBoundConversationRequest(pending).pending, true);
  assert.equal(planBoundConversationRequest({...pending, pending_observation: {...pending.pending_observation,
    turn: {...pending.pending_observation.turn, response: {message: "Complete direct answer", gate: null}}}}).pending, false);
});

test("an anchored pre-execution Argon quota failure resumes only through fresh original-source admission", () => {
  const selected = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: [], executor_endpoint_id: "antigravity"};
  const configured = planConversationBinding({...request, binding: selected}).state;
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
    observation, available_projects: [project], ...source}).context;
  const ref = "f".repeat(24), message = "Explain the original change";
  const session = {session_id: "original", goal_id: "loopx-manager", agent_id: "antigravity",
    agent_model_id: "gemini-4-argon-high", upstream_mode: "default", status: "ready", upstream_thread_id: "native",
    active_turn_id: null, channel_id: `manager.external.native.${row.binding_id}.${source.source_ref}`, steward_context: context};
  const failed = {session_id: "original", turn_id: "failed", client_turn_id: `external-${ref}`, message, origin: "lark",
    status: "failed", delta_count: 0, response: null, upstream_turn_id: "native-failed", error_code: "antigravity_step_error",
    started_at: "2026-01-01T00:00:00Z", completed_at: "2026-01-01T00:00:10Z"};
  const at = Date.parse("2026-01-01T00:10:00Z") / 1000;
  const anchor = {thread_id: "native", start_step_index: 10, input_sha256: "a".repeat(64)};
  const proof = {...anchor, request_ref: ref, execution_id: "original-execution", total_steps: 12,
    input_created_at: "2026-01-01T00:00:05Z", model_id: session.agent_model_id,
    pre_execution_quota_failure: true, only_original_user_item: true, latest_input_verified: true, observed_at: at};
  const allocation = {schema_version: "manager_executor_allocation_v0", selection_policy: "flexible",
    configured_endpoint: "antigravity", executor_endpoint: "codex", allocation_reason: "flexible_availability_fallback",
    configuration_revision: "current-pool", available: true, eligible_endpoints: ["antigravity", "codex"]};
  const configuration = {status: "ready", selection_policy: "flexible", executor_endpoint: "antigravity",
    eligible_endpoints: ["antigravity", "codex"], configuration_revision: "current-pool"};
  const capacity = {model_id: session.agent_model_id, account_ref: "c".repeat(64), remaining_fraction: 0,
    reset_time: "2026-01-08T00:00:00Z", observed_at: at, quota_exhausted: true, normal_completion: false,
    alternate_executor: {allocation, observed_at: at}};
  const packet = {request: {request_ref: ref, command: null, status: "accepted", delivery_verified: true,
    message, origin: "lark", binding_id: row.binding_id, session_id: "original", turn_id: "failed", source},
    binding: selected, current_session: session, context_recovery: {version: 22, failed_turn: failed,
      observed_at: "2026-01-01T00:10:00Z", steering_observed: false, stop_observed: false, attachments_present: false,
      source_verified: true, queued_input_present: false, later_source_input_present: false,
      native_input: anchor, native_observation: proof, metadata_capacity: capacity, executor_configuration: configuration}};
  const plan = planBoundConversationRequest(packet);
  assert.equal(plan.recovery_eligible, true);
  assert.equal(plan.quota_executor, "codex");
  assert.equal(plan.quota_attempt, 1);
  assert.equal(plan.client_turn_id, `external-${ref}-recovery-22-quota-1`);
  for (const change of [{source_verified: false}, {stop_observed: true}, {steering_observed: true},
    {attachments_present: true}, {queued_input_present: true}, {later_source_input_present: true},
    {native_input: null}, {native_observation: null}, {metadata_capacity: null},
    {executor_configuration: {...configuration, configuration_revision: "changed"}},
    {metadata_capacity: {...capacity, alternate_executor: null}},
    ...[{pre_execution_quota_failure: false}, {only_original_user_item: false}, {latest_input_verified: false},
      {total_steps: 13}, {input_sha256: "b".repeat(64)}, {request_ref: "foreign"}, {model_id: "other-model"},
      {observed_at: at - 121}, {observed_at: NaN}].map(value => ({native_observation: {...proof, ...value}})),
    ...[{delta_count: 1}, {response: {}}, {client_turn_id: "foreign"}, {origin: "web"}, {started_at: "unknown"}]
      .map(value => ({failed_turn: {...failed, ...value}}))]) {
    assert.notEqual(planBoundConversationRequest({...packet,
      context_recovery: {...packet.context_recovery, ...change}}).recovery_eligible, true);
  }
  const reserved = {...packet.request, context_recovery: {version: 22, failed_turn_id: "failed",
    quota_attempt: 1, client_turn_id: plan.client_turn_id}};
  const admitted = {session_id: "original", turn_id: "continued", client_turn_id: plan.client_turn_id, message, origin: "lark"};
  const adopted = planBoundConversationRequest({...packet, request: reserved,
    context_recovery: {...packet.context_recovery, native_observation: null, admitted_turn: admitted}});
  assert.equal(adopted.admitted_turn_id, "continued");
  assert.equal(adopted.recovery_eligible, true);
  const correlation = planBoundConversationRequest({...packet, request: reserved,
    current_session: {...session, status: "busy", active_turn_id: "newer"},
    context_recovery: {...packet.context_recovery, native_observation: null, stop_observed: true,
      later_source_input_present: true, queued_input_present: true, admitted_turn: admitted}});
  assert.equal(correlation.admitted_turn_id, "continued", "Later control may not lose already admitted correlation");
  assert.equal(correlation.recovery_eligible, true);
});

test("Core preparation failures resume through one bounded original-source queue, never from missing native evidence", () => {
  for (const endpoint of ["codex", "antigravity"]) {
    const selected = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: [], executor_endpoint_id: endpoint};
    const configured = planConversationBinding({...request, binding: selected}).state;
    const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
    const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
      observation, available_projects: [project], ...source}).context;
    const ref = "f".repeat(24), client = `external-${ref}`;
    const session = {session_id: "original", goal_id: "loopx-manager", agent_id: endpoint,
      upstream_mode: endpoint === "codex" ? "chat" : "default", status: "ready", upstream_thread_id: "native", active_turn_id: null,
      channel_id: `manager.external.native.${row.binding_id}.${source.source_ref}`, steward_context: context};
    const failed = {session_id: "original", turn_id: "failed", client_turn_id: client, message: "Original request",
      status: "failed", delta_count: 0, response: null, upstream_turn_id: null, error_code: "manager_authorization_unavailable",
      completed_at: "2026-01-01T00:00:00Z"};
    const proof = {kind: "core_context_preparation_failure", session_id: "original", turn_id: "failed", events: [
      {event_id: "one", kind: "turn.queued"}, {event_id: "two", kind: "agent.phase", phase: "manager_context"},
      {event_id: "three", kind: "manager.context", schema_version: "manager_turn_context_v1"},
      {event_id: "four", kind: "turn.failed", error_code: failed.error_code}]};
    const packet = {request: {request_ref: ref, command: null, status: "accepted", delivery_verified: true,
      message: failed.message, origin: "lark", binding_id: row.binding_id, session_id: "original", turn_id: "failed", source},
      binding: selected, current_session: session, context_recovery: {version: 21, failed_turn: failed,
        observed_at: "2026-01-01T00:10:00Z", steering_observed: false, stop_observed: false, attachments_present: false,
        source_verified: true, queued_input_present: false, later_equivalent_input_present: false,
        preparation_scope_available: true, preparation_observation: proof}};
    const plan = planBoundConversationRequest(packet);
    assert.equal(plan.recovery_eligible, true);
    assert.equal(plan.preparation_attempt, 1);
    assert.equal(plan.client_turn_id, `${client}-recovery-21-preparation-1`);
    assert.deepEqual(plan.native_thread_ids, []);
    for (const change of [{source_verified: false}, {stop_observed: true}, {steering_observed: true},
      {attachments_present: true}, {queued_input_present: true}, {later_equivalent_input_present: true},
      {later_equivalent_input_present: null}, {preparation_scope_available: false}, {observed_at: "2026-01-01T00:00:59Z"},
      {preparation_observation: null}, {preparation_observation: {...proof, events: []}},
      {preparation_observation: {...proof, events: [...proof.events, {event_id: "native", kind: "turn.started"}]}},
      {preparation_observation: {...proof, turn_id: "another"}},
      {native_observation: {effects_observed: true}},
      ...[{upstream_turn_id: "native-started"}, {delta_count: 1}, {response: {}}, {client_turn_id: "foreign"}]
        .map(value => ({failed_turn: {...failed, ...value}}))]) {
      assert.notEqual(planBoundConversationRequest({...packet, context_recovery: {...packet.context_recovery, ...change}}).recovery_eligible, true);
    }
    const reservation = {version: 21, failed_turn_id: "failed", preparation_attempt: 1, client_turn_id: plan.client_turn_id};
    const admitted = {session_id: "original", turn_id: "already-admitted", client_turn_id: plan.client_turn_id,
      message: failed.message, origin: "lark", attachments: [], status: "completed"};
    const crash = {...packet, request: {...packet.request, context_recovery: reservation},
      current_session: {...session, status: "busy", active_turn_id: "already-admitted"},
      context_recovery: {...packet.context_recovery, admitted_turn: admitted, stop_observed: true,
        later_equivalent_input_present: true, queued_input_present: true, preparation_observation: null}};
    assert.equal(planBoundConversationRequest(crash).admitted_turn_id, admitted.turn_id);
    assert.equal(planBoundConversationRequest({...crash, context_recovery: {...crash.context_recovery,
      admitted_turn: {...admitted, message: "Different intent"}}}).recovery_eligible, false);
    const next = {...packet, request: {...packet.request, turn_id: "next", context_recovery: reservation},
      context_recovery: {...packet.context_recovery, failed_turn: {...failed, turn_id: "next", client_turn_id: plan.client_turn_id},
        preparation_observation: {...proof, turn_id: "next"}}};
    assert.equal(planBoundConversationRequest(next).preparation_attempt, 2);
    assert.equal(planBoundConversationRequest(next).not_before, "2026-01-01T00:02:00.000Z");
    for (const error of ["host_gate", "usage_limit_exceeded", "upstream_transport_failure"]) {
      const dispatched = {...next, context_recovery: {...next.context_recovery,
        failed_turn: {...next.context_recovery.failed_turn, error_code: error, upstream_turn_id: "native-started"},
        native_observation: {thread_id: "native", turn_id: "native-started", pre_execution_routing_timeout: true,
          codex_error_info: "other", only_pre_execution_items: true}}};
      assert.equal(planBoundConversationRequest(dispatched).recovery_candidate, false);
      assert.equal(planBoundConversationRequest({...dispatched, request: {...dispatched.request,
        context_recovery: undefined, context_recovery_history: [reservation]}}).recovery_candidate, false);
    }
    const lastClient = `${client}-recovery-21-preparation-3`;
    assert.equal(planBoundConversationRequest({...next, request: {...next.request,
      context_recovery: {...reservation, preparation_attempt: 3, client_turn_id: lastClient}},
      context_recovery: {...next.context_recovery, failed_turn: {...next.context_recovery.failed_turn, client_turn_id: lastClient}}}).recovery_candidate, false);
    assert.equal(planBoundConversationRequest({...packet, request: {...packet.request,
      context_recovery: {...reservation, work_attempt: 1}}}).recovery_candidate, false);
  }
});

test("legacy image preflight failures need exact Core and image proof before one original continuation", () => {
  for (const endpoint of ["codex", "antigravity"]) {
    const selected = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: [], executor_endpoint_id: endpoint};
    const configured = planConversationBinding({...request, binding: selected}).state;
    const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
    const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
      observation, available_projects: [project], ...source}).context;
    const ref = "f".repeat(24), client = `external-${ref}`;
    const session = {session_id: "original", goal_id: "loopx-manager", agent_id: endpoint,
      upstream_mode: endpoint === "codex" ? "chat" : "default", status: "ready", upstream_thread_id: "native",
      active_turn_id: null, channel_id: `manager.external.native.${row.binding_id}.${source.source_ref}`, steward_context: context};
    const failed = {session_id: "original", turn_id: "failed", client_turn_id: client, message: "Explain this image",
      status: "failed", delta_count: 0, response: null, upstream_turn_id: null, error_code: "runtime_error",
      error: "attachments currently require the Codex Agent endpoint", completed_at: "2026-01-01T00:00:00Z"};
    const images = {count: 1, sha256: "a".repeat(64)};
    const proof = {kind: "core_attachment_preparation_failure", session_id: "original", turn_id: "failed", events: [
      {event_id: "one", kind: "turn.queued"},
      {event_id: "three", kind: "manager.context", schema_version: "manager_turn_context_v1"},
      {event_id: "four", kind: "turn.failed", error_code: "runtime_error"}]};
    const packet = {request: {request_ref: ref, command: null, status: "accepted", delivery_verified: true,
      message: failed.message, origin: "lark", binding_id: row.binding_id, session_id: "original", turn_id: "failed", source},
      binding: selected, current_session: session, context_recovery: {version: 22, failed_turn: failed,
        observed_at: "2026-01-01T00:10:00Z", steering_observed: false, stop_observed: false, attachments_present: true,
        image_attachments: {request: images, failed: images}, native_result_present: false,
        source_verified: true, queued_input_present: false, later_equivalent_input_present: false,
        preparation_scope_available: true, preparation_observation: proof}};
    const plan = planBoundConversationRequest(packet);
    assert.equal(plan.recovery_eligible, true);
    assert.equal(plan.preparation_attempt, 1);
    assert.deepEqual(plan.native_thread_ids, []);
    const withPhase = {...proof, events: [proof.events[0],
      {event_id: "two", kind: "agent.phase", phase: "manager_context"}, ...proof.events.slice(1)]};
    assert.equal(planBoundConversationRequest({...packet, context_recovery: {
      ...packet.context_recovery, preparation_observation: withPhase}}).recovery_eligible, true);
    for (const change of [{source_verified: false}, {stop_observed: true}, {queued_input_present: true},
      {later_equivalent_input_present: true}, {preparation_scope_available: false}, {attachments_present: false},
      {native_result_present: true}, {native_result_present: undefined}, {native_observation: {effects_observed: true}},
      {image_attachments: {request: images, failed: {...images, sha256: "b".repeat(64)}}},
      {image_attachments: {request: {count: 0, sha256: images.sha256}, failed: {count: 0, sha256: images.sha256}}},
      {preparation_observation: {...proof, events: proof.events.slice(1)}},
      {preparation_observation: {...proof, events: [...proof.events, {event_id: "native", kind: "turn.started"}]}},
      ...[{upstream_turn_id: "started"}, {delta_count: 1}, {response: {}}, {client_turn_id: "foreign"},
        {error: "The worker exited; execution may have occurred"}].map(value => ({failed_turn: {...failed, ...value}}))]) {
      assert.notEqual(planBoundConversationRequest({...packet, context_recovery: {
        ...packet.context_recovery, ...change}}).recovery_eligible, true);
    }
    const reservation = {version: 22, failed_turn_id: "failed", preparation_attempt: 1, client_turn_id: plan.client_turn_id};
    const admitted = {session_id: "original", turn_id: "continued", client_turn_id: plan.client_turn_id,
      message: failed.message, origin: "lark"};
    const crash = {...packet, request: {...packet.request, context_recovery: reservation}, context_recovery: {
      ...packet.context_recovery, admitted_turn: admitted, image_attachments: {request: images, failed: images, admitted: images}}};
    assert.equal(planBoundConversationRequest(crash).admitted_turn_id, "continued");
    assert.notEqual(planBoundConversationRequest({...crash, context_recovery: {...crash.context_recovery,
      image_attachments: {...crash.context_recovery.image_attachments, admitted: {...images, sha256: "b".repeat(64)}}}}).recovery_eligible, true);
    assert.equal(planBoundConversationRequest({...packet, request: {...packet.request, turn_id: "continued",
      context_recovery: reservation}, context_recovery: {...packet.context_recovery,
        failed_turn: {...failed, turn_id: "continued", client_turn_id: plan.client_turn_id},
        preparation_observation: {...proof, turn_id: "continued"}}}).recovery_candidate, false);
  }
});

test("completed truncated text adopts only its anchored original final, never a later result or action", () => {
  const selected = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: [], executor_endpoint_id: "antigravity"};
  const configured = planConversationBinding({...request, binding: selected}).state;
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
    observation, available_projects: [project], ...source}).context;
  const ref = "f".repeat(24);
  const session = {session_id: "original", goal_id: "loopx-manager", agent_id: "antigravity",
    upstream_mode: "default", status: "ready", upstream_thread_id: "native", active_turn_id: null,
    channel_id: `manager.external.native.${row.binding_id}.${source.source_ref}`, steward_context: context};
  const response = {schema_version: "loopx_chat_agent_response_v0", message: "Original `", proposals: [], gate: null};
  const completed = {session_id: "original", turn_id: "truncated", client_turn_id: `external-${ref}`,
    message: "Original request", status: "completed", response, error_code: null,
    started_at: "2026-01-01T00:00:00Z", completed_at: "2026-01-01T00:00:10Z"};
  const anchor = {thread_id: "native", start_step_index: 4, input_sha256: "1".repeat(64)};
  const proof = {...anchor, request_ref: ref, execution_id: "original-execution",
    input_created_at: "2026-01-01T00:00:05Z", total_steps: 12, end_step_index: 8,
    answer_sha256: "2".repeat(64), latest_input_verified: false, normal_terminal_verified: true,
    response: {...response, message: "Original `<loopx-review-json>` is a literal marker. Complete answer."}};
  const original = {request_ref: ref, command: null, status: "accepted", delivery_verified: true,
    message: completed.message, origin: "lark", binding_id: row.binding_id, session_id: "original", turn_id: "truncated", source};
  const recovery = {version: 21, failed_turn: completed, native_input: anchor, response_literal_open: true,
    queued_input_present: false, steering_observed: false, stop_observed: false, attachments_present: false,
    source_verified: true, native_observation: proof};
  const packet = {request: original, binding: selected, current_session: session, context_recovery: recovery};
  assert.equal(planBoundConversationRequest(packet).operation, "adopt_native_result");
  assert.equal(planBoundConversationRequest(packet).recovery_eligible, true);
  assert.equal(planBoundConversationRequest(packet).client_turn_id, undefined);
  for (const change of [{native_input: null}, {response_literal_open: false}, {queued_input_present: true},
    {source_verified: false}, {stop_observed: true}, {steering_observed: true}, {attachments_present: true},
    {native_input: {...anchor, thread_id: "later"}}, {native_input: {...anchor, input_sha256: "3".repeat(64)}},
    ...[{start_step_index: 5}, {end_step_index: 12}, {end_step_index: 5}, {end_step_index: null},
      {normal_terminal_verified: false}, {input_created_at: "2026-01-02T00:00:00Z"},
      {response: {...proof.response, message: response.message}},
      {response: {...proof.response, message: "Later answer"}},
      {response: {...proof.response, proposals: [{kind: "action"}]}},
      {response: {...proof.response, gate: {kind: "response_metadata_invalid"}}},
      {response: {...proof.response, extra_metadata: "changed"}}]
      .map(value => ({native_observation: {...proof, ...value}})),
    {failed_turn: {...completed, response: {...response, protected_action: {kind: "action"}}}}]) {
    assert.equal(planBoundConversationRequest({...packet, context_recovery: {...recovery, ...change}}).recovery_eligible,
      false, JSON.stringify(change));
  }
  for (const change of [{status: "closed"}, {status: "busy", active_turn_id: "newer"}, {active_turn_id: "newer"}]) {
    assert.equal(planBoundConversationRequest({...packet, current_session: {...session, ...change}}).recovery_candidate, false);
  }
  const latest = {...proof, latest_input_verified: true};
  delete (latest as Partial<typeof proof>).end_step_index;
  assert.equal(planBoundConversationRequest({...packet, context_recovery: {...recovery, native_observation: latest}}).recovery_eligible, true);
  const adopted = {turn_id: completed.turn_id, message_id: `native.${completed.turn_id}.recovered`,
    message_text: "Frozen original answer", native_observation: proof, delivery_verified: false};
  const pending = {...original, native_result_recovery: adopted};
  assert.equal(planBoundConversationRequest({request: pending, current_session: session,
    pending_observation: {turn: completed, stop_observed: false}}).pending, true);
  const delivered = {...pending, native_result_recovery: {...adopted, delivery_verified: true}};
  assert.equal(planBoundConversationRequest({request: delivered, current_session: session,
    pending_observation: {turn: completed, stop_observed: false}}).pending, false);
  assert.equal(planBoundConversationRequest({...packet, request: delivered}).recovery_candidate, false);
});

test("native terminal recovery adopts a result without Turn admission or lost failure provenance", () => {
  const selected = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: [], executor_endpoint_id: "antigravity"};
  const configured = planConversationBinding({...request, binding: selected}).state;
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
    observation, available_projects: [project], ...source}).context;
  const ref = "f".repeat(24);
  const session = {session_id: "original", goal_id: "loopx-manager", agent_id: "antigravity",
    upstream_mode: "default", status: "ready", upstream_thread_id: "native", active_turn_id: null,
    channel_id: `manager.external.native.${row.binding_id}.${source.source_ref}`, steward_context: context};
  const failed = {session_id: "original", turn_id: "failed", client_turn_id: `external-${ref}`, message: "Original request",
    status: "failed", delta_count: 1, response: null, error_code: "antigravity_rpc_error",
    started_at: "2026-01-01T00:00:00Z", completed_at: "2026-01-01T00:00:10Z"};
  const proof = {thread_id: "native", request_ref: ref, execution_id: "original-execution",
    input_created_at: "2026-01-01T00:00:05Z", start_step_index: 4, total_steps: 8,
    input_sha256: "1".repeat(64), answer_sha256: "2".repeat(64),
    latest_input_verified: true, normal_terminal_verified: true,
    response: {schema_version: "loopx_chat_agent_response_v0", message: "Existing answer", proposals: [], gate: null}};
  const packet = {request: {request_ref: ref, command: null, status: "accepted", delivery_verified: true,
    message: "Original request", origin: "lark", binding_id: row.binding_id, session_id: "original", turn_id: "failed", source},
    binding: selected, current_session: session, context_recovery: {version: 21, failed_turn: failed,
      steering_observed: false, stop_observed: false, attachments_present: false, source_verified: true, native_observation: proof}};
  const plan = planBoundConversationRequest(packet);
  assert.equal(plan.operation, "adopt_native_result");
  assert.equal(plan.recovery_eligible, true);
  assert.equal(plan.client_turn_id, undefined);
  const anchor = {thread_id: "native", start_step_index: proof.start_step_index, input_sha256: proof.input_sha256};
  const historicalResult = {...packet, context_recovery: {...packet.context_recovery, native_input: anchor,
    native_observation: {...proof, latest_input_verified: false, end_step_index: 6}}};
  assert.equal(planBoundConversationRequest(historicalResult).recovery_eligible, true);
  assert.equal(planBoundConversationRequest(historicalResult).client_turn_id, undefined);
  for (const change of [{native_input: null}, {source_verified: false}, {stop_observed: true},
    {native_input: {...anchor, input_sha256: "9".repeat(64)}},
    {native_input: {...anchor, start_step_index: 3}},
    {native_input: {...anchor, thread_id: "other"}},
    ...[null, 5, 8, 9].map(end_step_index => ({native_observation: {
      ...historicalResult.context_recovery.native_observation, end_step_index}}))]) {
    assert.equal(planBoundConversationRequest({...historicalResult,
      context_recovery: {...historicalResult.context_recovery, ...change}}).recovery_eligible, false);
  }
  for (const field of ["input_sha256", "answer_sha256"]) {
    for (const invalid of [null, false, 1, [], {}, "", "1".repeat(63), "1".repeat(65),
      "A".repeat(64), `sha256:${"1".repeat(64)}`]) {
      assert.equal(planBoundConversationRequest({...packet, context_recovery: {...packet.context_recovery,
        native_observation: {...proof, [field]: invalid}}}).recovery_eligible, false,
      `${field} must reject ${JSON.stringify(invalid)}`);
    }
    assert.equal(planBoundConversationRequest({...packet, context_recovery: {...packet.context_recovery,
      native_observation: {...proof, [field]: "abcdef0123456789".repeat(4)}}}).recovery_eligible, true);
  }
  for (const change of [{source_verified: false}, {stop_observed: true}, {steering_observed: true},
    {attachments_present: true}, {native_observation: {...proof, thread_id: "other"}},
    {native_observation: {...proof, request_ref: "a".repeat(24)}},
    {native_observation: {...proof, normal_terminal_verified: false}},
    {native_observation: {...proof, latest_input_verified: false}},
    {native_observation: {...proof, input_created_at: "2026-01-02T00:00:00Z"}},
    {failed_turn: {...failed, client_turn_id: "another-input"}},
    {failed_turn: {...failed, error_code: "host_gate"}}, {failed_turn: {...failed, status: "interrupted"}}]) {
    assert.equal(planBoundConversationRequest({...packet, context_recovery: {...packet.context_recovery, ...change}}).recovery_eligible, false);
  }
  for (const change of [{status: "closed"}, {session_mode: "attached_host"}, {agent_id: "codex"},
    {status: "busy", active_turn_id: "current"}, {status: "ready", active_turn_id: "current"},
    {status: "busy", active_turn_id: null}]) {
    assert.equal(planBoundConversationRequest({...packet, current_session: {...session, ...change}}).recovery_candidate, false);
  }
  assert.throws(() => planBoundConversationRequest({...packet,
    binding: {...selected, audience: "group", group_refs: ["a".repeat(24)]}}), /group Chat requires a native project executor/);
  const newRef = "9".repeat(24);
  assert.equal(planBoundConversationRequest({...packet, request: {...packet.request, request_ref: newRef},
    context_recovery: {...packet.context_recovery, failed_turn: {...failed, client_turn_id: `external-${newRef}`},
      native_observation: {...proof, request_ref: newRef}}}).recovery_eligible, true);
  const adopted = {turn_id: "failed", message_id: "native.failed.recovered", message_text: "Frozen answer",
    native_observation: proof, delivery_verified: true};
  assert.equal(planBoundConversationRequest({...packet,
    request: {...packet.request, native_result_recovery: {...adopted, delivery_verified: false}}}).recovery_eligible, true);
  assert.equal(planBoundConversationRequest({...packet,
    request: {...packet.request, native_result_recovery: {...adopted, delivery_verified: false, message_id: "other"}}}).recovery_eligible, false);
  const delivered = {...packet.request, native_result_recovery: adopted};
  assert.equal(planBoundConversationRequest({...packet, request: delivered}).recovery_candidate, false);
  assert.equal(planBoundConversationRequest({request: delivered, current_session: session,
    pending_observation: {turn: failed, stop_observed: false}}).pending, false);
  const incomplete = {...delivered, native_result_recovery: {...adopted,
    native_observation: {...proof, response: {...proof.response, gate: {kind: "response_metadata_invalid"}}}}};
  assert.equal(planBoundConversationRequest({request: incomplete, current_session: session,
    pending_observation: {turn: failed, stop_observed: false}}).pending, true);
  const handoffs = [{goal_id: "work", agent_id: "worker", brief: "Preserve this exact instruction"}];
  const raw = "<loopx-review-json>" + JSON.stringify({message: "Original prose", context_handoff: handoffs,
    proposals: [], gate: null}) + "</loopx-review-json>";
  const frozen = {...proof, answer_sha256: createHash("sha256").update(raw).digest("hex"),
    response: {...proof.response, gate: {kind: "response_metadata_invalid", summary: "Unsubmitted handoffs"}}};
  const owed = {...delivered, native_result_recovery: {...adopted, native_observation: frozen}};
  const metadata = {...packet, request: owed, context_recovery: {...packet.context_recovery,
    observed_at: "2026-01-01T00:00:30Z",
    queued_input_present: false, native_observation: {...frozen, raw_response: raw},
    metadata_capacity: {model_id: "gemini-4-argon-high", remaining_fraction: 1,
      account_ref: "3".repeat(64),
      reset_time: "2026-01-10T00:00:00Z", observed_at: Date.parse("2026-01-01T00:00:30Z") / 1000,
      quota_exhausted: false, normal_completion: true}}};
  const review = planBoundConversationRequest(metadata);
  assert.equal(review.recovery_eligible, true);
  assert.equal(review.metadata_review, true);
  assert.deepEqual(review.metadata_handoffs, handoffs);
  assert.ok(String(review.client_turn_id).endsWith("-metadata-1"));
  for (const change of [{source_verified: false}, {stop_observed: true}, {steering_observed: true},
    {native_observation: {...frozen, raw_response: raw + "changed"}},
    {native_observation: {...frozen, raw_response: raw, total_steps: 9}}]) {
    assert.equal(planBoundConversationRequest({...metadata, context_recovery: {...metadata.context_recovery, ...change}}).recovery_eligible, false);
  }
  assert.equal(planBoundConversationRequest({...metadata, current_session: {...session, active_turn_id: "newer"}}).recovery_candidate, false);
  const reservation = {version: 21, failed_turn_id: failed.turn_id, client_turn_id: review.client_turn_id,
    metadata_review: true, metadata_handoffs: handoffs};
  const admitted = {session_id: session.session_id, turn_id: "format-review", client_turn_id: review.client_turn_id,
    message: owed.message, origin: owed.origin, attachments: [], status: "failed", error_code: "host_gate"};
  const crash = {...metadata, request: {...owed, context_recovery: reservation},
    current_session: {...session, status: "busy", active_turn_id: admitted.turn_id},
    context_recovery: {...metadata.context_recovery, native_observation: null, admitted_turn: admitted}};
  assert.equal(planBoundConversationRequest(crash).admitted_turn_id, admitted.turn_id);
  const exhausted = {...owed, turn_id: admitted.turn_id, context_recovery: reservation};
  assert.equal(planBoundConversationRequest({...metadata, request: exhausted,
    context_recovery: {...metadata.context_recovery, failed_turn: admitted}}).recovery_candidate, false);
  assert.equal(planBoundConversationRequest({request: exhausted, current_session: session,
    pending_observation: {turn: admitted, stop_observed: false}}).pending, true,
    "A held/failed format review retains the original unresolved handoffs without retry authority");
  const objectBrief = {Hypotheses: ["Original hypothesis"], Evidence: ["Read evidence"],
    Constraints: ["No changes to frozen work"]};
  const objectHandoff = {schema_version: "context_handoff_v0", goal_id: "work", agent_id: "worker", brief: objectBrief};
  const objectRaw = "<loopx-review-json>" + JSON.stringify({message: "Original prose", context_handoff: objectHandoff,
    proposals: []}) + "</loopx-review-json>";
  const historical = {...frozen, latest_input_verified: false, end_step_index: 6,
    answer_sha256: createHash("sha256").update(objectRaw).digest("hex")};
  const completed = {...failed, status: "completed", error_code: null, response: historical.response};
  const completedRow = {...packet.request};
  const direct = {...metadata, request: completedRow, context_recovery: {...metadata.context_recovery,
    failed_turn: completed, native_input: {thread_id: "native", start_step_index: 4, input_sha256: historical.input_sha256},
    native_observation: {...historical, raw_response: objectRaw}}};
  const directPlan = planBoundConversationRequest(direct);
  assert.equal(directPlan.recovery_eligible, true);
  assert.equal(directPlan.metadata_review, true);
  assert.equal(directPlan.recover_native_result, undefined, "Already delivered prose cannot be adopted/sent again");
  assert.deepEqual(directPlan.metadata_handoffs, [{goal_id: "work", agent_id: "worker", brief: JSON.stringify(objectBrief)}]);
  for (const handoff of [{...objectHandoff, execution_binding_id: "invented"},
    {...objectHandoff, schema_version: "unknown-version"}, {...objectHandoff, brief: {budget: 9007199254740992}},
    {...objectHandoff, brief: {effects_settled: true}}, {...objectHandoff, brief: null}]) {
    const invalidRaw = "<loopx-review-json>" + JSON.stringify({message: "Original prose", context_handoff: handoff,
      proposals: []}) + "</loopx-review-json>";
    assert.equal(planBoundConversationRequest({...direct, context_recovery: {...direct.context_recovery,
      native_observation: {...historical, raw_response: invalidRaw,
        answer_sha256: createHash("sha256").update(invalidRaw).digest("hex")}}}).recovery_eligible, false);
  }
  for (const change of [{source_verified: false}, {queued_input_present: true}, {stop_observed: true},
    {steering_observed: true}, {native_input: null}, {metadata_capacity: null},
    {native_observation: {...historical, raw_response: objectRaw, latest_input_verified: false, end_step_index: 8}},
    {native_observation: {...historical, raw_response: objectRaw, execution_id: ""}},
    {native_observation: {...historical, raw_response: objectRaw, input_sha256: "9".repeat(64)}},
    {native_observation: {...historical, raw_response: objectRaw, response: {...historical.response, message: "Changed prose"}}}]) {
    assert.equal(planBoundConversationRequest({...direct, context_recovery: {...direct.context_recovery, ...change}}).recovery_eligible, false);
  }
  const lowCapacity = {...metadata.context_recovery.metadata_capacity, quota_exhausted: true,
    normal_completion: false, remaining_fraction: 0.006};
  const low = {...direct, context_recovery: {...direct.context_recovery, metadata_capacity: lowCapacity}};
  const held = planBoundConversationRequest(low);
  assert.equal(held.recovery_eligible, false, "A small positive fraction cannot override the actual quota failure");
  assert.deepEqual(held.metadata_quota_wait, lowCapacity);

  const allocation = {schema_version: "manager_executor_allocation_v0", selection_policy: "flexible",
    configured_endpoint: "antigravity", executor_endpoint: "codex", allocation_reason: "flexible_availability_fallback",
    configuration_revision: "route-revision", available: true, eligible_endpoints: ["antigravity", "codex"]};
  const configuration = {status: "ready", selection_policy: "flexible", executor_endpoint: "antigravity",
    eligible_endpoints: ["antigravity", "codex"], configuration_revision: "route-revision"};
  const alternate = {allocation, observed_at: lowCapacity.observed_at};
  const fallbackPacket = {...direct, context_recovery: {...direct.context_recovery,
    executor_configuration: configuration, metadata_capacity: {...lowCapacity, alternate_executor: alternate}}};
  const fallback = planBoundConversationRequest(fallbackPacket);
  assert.equal(fallback.recovery_eligible, true);
  assert.equal(fallback.metadata_executor, "codex");
  for (const change of [{executor_configuration: {...configuration, selection_policy: "pinned"}},
    {executor_configuration: {...configuration, configuration_revision: "changed"}},
    {metadata_capacity: {...lowCapacity, alternate_executor: {...alternate, observed_at: alternate.observed_at - 121}}}]) {
    assert.equal(planBoundConversationRequest({...fallbackPacket,
      context_recovery: {...fallbackPacket.context_recovery, ...change}}).recovery_eligible, false);
  }
  const waitingRow = {...completedRow, metadata_quota_wait: held.metadata_quota_wait};
  assert.equal(planBoundConversationRequest({...low, request: waitingRow}).recovery_eligible, false);
  assert.equal(planBoundConversationRequest({...low, request: waitingRow,
    context_recovery: {...low.context_recovery, metadata_capacity: {...lowCapacity, remaining_fraction: 1}}}).recovery_eligible, false);
  assert.equal(planBoundConversationRequest({...low, request: waitingRow,
    context_recovery: {...low.context_recovery, metadata_capacity: {...lowCapacity, remaining_fraction: 1,
      reset_time: "2026-01-11T00:00:00Z"}}}).recovery_eligible, true);
  assert.equal(planBoundConversationRequest({...low, request: waitingRow,
    context_recovery: {...low.context_recovery, metadata_capacity: {...lowCapacity, remaining_fraction: 1,
      account_ref: "4".repeat(64)}}}).recovery_eligible, true);
  assert.equal(planBoundConversationRequest({...low, request: waitingRow,
    context_recovery: {...low.context_recovery, metadata_capacity: {...lowCapacity,
      reset_time: "2026-01-10T00:00:00+00:00"}}}).recovery_eligible, false);
  for (const capacity of [{...lowCapacity, model_id: "another-model"}, {...lowCapacity, observed_at: 0},
    {...lowCapacity, remaining_fraction: -1}, {...lowCapacity, remaining_fraction: 2},
    {...lowCapacity, quota_exhausted: null}, {...lowCapacity, reset_time: "unknown"}]) {
    assert.equal(planBoundConversationRequest({...low, request: waitingRow,
      context_recovery: {...low.context_recovery, metadata_capacity: capacity}}).recovery_eligible, false);
  }
  const unfinished = {...delivered, native_result_recovery: {...adopted,
    native_observation: {...proof, response: {...proof.response,
      unfinished_work: {remaining: "Still owed", dependency: "Original worker", effects_settled: true}}}}};
  assert.equal(planBoundConversationRequest({request: unfinished, current_session: session,
    pending_observation: {turn: failed, stop_observed: false}}).pending, true);
  assert.equal(planBoundConversationRequest({request: packet.request, current_session: session,
    pending_observation: {turn: {...failed, status: "completed", response: {...proof.response,
      gate: {kind: "response_metadata_invalid", summary: "Action metadata was not admitted"}}}, stop_observed: false}}).pending, true);
});

for (const endpoint of ["codex", "antigravity", "kiro-cli"]) {
  test(`retained steward identity permits bound reads after selecting ${endpoint}`, () => {
    const actor = {schema_version: "loopx_steward_managed_actor_v0",
      goal_id: `steward-agent-${"8".repeat(24)}`, goal_instance_id: null, agent_id: "steward",
      session_id: "7".repeat(32), source_ref: "6".repeat(24), creation_operation_id: "original-creation"};
    const steward = {...row, binding_id: "8".repeat(24), transport_ref: "owner-steward",
      provider_ref: "9".repeat(24), context_kind: "steward", grant: "portfolio_read", goal_ids: [],
      executor_endpoint_id: endpoint, managed_actor: actor};
    const configured = {...planConversationBinding(request).state, bindings: [row, steward]};
    const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
    const notes = resolveBoundConversation({current: configured, binding_id: row.binding_id,
      observation, available_projects: [project], ...source});
    assert.deepEqual(notes.binding, row, "An unrelated steward cannot block project admission");
    const selected = resolveBoundConversation({current: configured, binding_id: steward.binding_id,
      observation: {...observation, transport_ref: steward.transport_ref, provider_ref: steward.provider_ref},
      available_projects: [project], ...source});
    assert.deepEqual(selected.binding.managed_actor, actor);
    assert.equal(selected.binding.executor_endpoint_id, endpoint);
    for (const invalid of [{...steward, context_kind: "project", grant: "workspace_read"},
      {...steward, audience: "group", group_refs: ["5".repeat(24)]},
      {...steward, managed_actor: {...actor, agent_id: "another-agent"}}]) {
      assert.throws(() => resolveBoundConversation({current: {...configured, bindings: [row, invalid]},
        binding_id: row.binding_id, observation, available_projects: [project], ...source}));
    }
    if (endpoint !== "codex") {
      assert.throws(() => stewardManagedBasis({binding: steward,
        session: {steward_context: selected.context}, observation}), /original idle private steward/,
      "Retaining an identity cannot authorize unsupported native execution");
    }
  });
}

test("unfinished completed work requires typed intent and pinned settled native/source proof, once", () => {
  const configured = planConversationBinding(request).state;
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
    observation, available_projects: [project], ...source}).context;
  const work = {remaining: "Finish the remaining work", dependency: "Temporary source outage", effects_settled: true};
  const completed = {session_id: "original", turn_id: "partial", status: "completed", error_code: null,
    response: {message: "An honest partial answer", proposals: [], protected_action: null, gate: null, unfinished_work: work},
    upstream_turn_id: "native-partial", completed_at: "2026-01-01T00:00:00Z"};
  const session = {session_id: "original", goal_id: null, agent_id: "codex", upstream_mode: "chat", status: "ready",
    upstream_thread_id: "native", active_turn_id: null, project_context: context,
    channel_id: `project.external.${row.binding_id}.${source.source_ref}`};
  const native = {thread_id: "native", turn_id: "native-partial", completed_work: true,
    native_inventory_sha256: "a".repeat(64),
    all_effects_settled: true, unfinished_work: {effects_settled: true, dependency: work.dependency, remaining: work.remaining}};
  const packet = {request: {request_ref: "f".repeat(24), command: null, status: "accepted", delivery_verified: true,
    message: "Original request", origin: "lark", binding_id: row.binding_id, session_id: "original", turn_id: "partial", source},
    binding: row, current_session: session, context_recovery: {version: 21, failed_turn: completed,
      observed_at: "2026-01-01T00:10:00Z", steering_observed: false, stop_observed: false, attachments_present: false,
      queued_input_present: false, later_equivalent_input_present: false,
      source_verified: true, native_observation: native}};
  const plan = planBoundConversationRequest(packet);
  assert.equal(plan.recovery_eligible, true);
  assert.equal(plan.work_attempt, 1);
  assert.ok(String(plan.client_turn_id).endsWith("-work-1"));
  for (const change of [{source_verified: false}, {steering_observed: true}, {stop_observed: true},
    {queued_input_present: true}, {later_equivalent_input_present: true}, {later_equivalent_input_present: null},
    {attachments_present: true}, {observed_at: "2026-01-01T00:00:59Z"},
    {native_observation: {...native, native_inventory_sha256: null}},
    {native_observation: {...native, all_effects_settled: false}},
    {native_observation: {...native, thread_id: "foreign"}},
    {native_observation: {...native, unfinished_work: {...work, remaining: "Another task"}}}]) {
    assert.equal(planBoundConversationRequest({...packet,
      context_recovery: {...packet.context_recovery, ...change}}).recovery_eligible, false);
  }
  for (const response of [{message: "Incomplete; please retry", proposals: []},
    {...completed.response, gate: {kind: "host_tool_gate"}},
    {...completed.response, unfinished_work: {...work, effects_settled: false}},
    {...completed.response, unfinished_work: {...work, grant: "write"}}]) {
    assert.equal(planBoundConversationRequest({...packet, context_recovery: {...packet.context_recovery,
      failed_turn: {...completed, response}}}).recovery_candidate, false);
  }
  const reserved = {version: 21, failed_turn_id: "partial", work_attempt: 1, unfinished_work: work,
    client_turn_id: plan.client_turn_id, not_before: plan.not_before};
  const admitted = {session_id: "original", turn_id: "continuation", client_turn_id: plan.client_turn_id,
    message: packet.request.message, origin: "lark", attachments: [], status: "completed"};
  const crash = {...packet, request: {...packet.request, context_recovery: reserved},
    current_session: {...session, status: "busy", active_turn_id: admitted.turn_id},
    context_recovery: {...packet.context_recovery, native_observation: null, admitted_turn: admitted,
      later_equivalent_input_present: true, queued_input_present: true}};
  const recovered = planBoundConversationRequest(crash);
  assert.equal(recovered.admitted_turn_id, admitted.turn_id);
  assert.equal(recovered.work_attempt, 1);
  assert.equal(recovered.read_only_review, undefined);
  assert.equal(planBoundConversationRequest({...crash, context_recovery: {...crash.context_recovery,
    source_verified: false}}).recovery_eligible, false);
  assert.equal(planBoundConversationRequest({...crash, context_recovery: {...crash.context_recovery,
    admitted_turn: {...admitted, message: "Changed input"}}}).recovery_eligible, false);
  for (const status of ["completed", "failed"]) {
    assert.equal(planBoundConversationRequest({...packet, request: {...packet.request,
      turn_id: admitted.turn_id, context_recovery: reserved}, context_recovery: {...packet.context_recovery,
      failed_turn: {...completed, turn_id: admitted.turn_id, status, error_code: "host_gate"}}}).recovery_candidate, false);
  }
});

test("a delivered same-question review permits one independent legacy result read, never a business retry", () => {
  const configured = planConversationBinding(request).state;
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
    observation, available_projects: [project], ...source}).context;
  const response = {schema_version: "loopx_chat_agent_response_v0", message: "The original source could not be processed",
    proposals: [], protected_action: null, gate: null};
  const completed = {session_id: "original", turn_id: "legacy", client_turn_id: `external-${"f".repeat(24)}`,
    message: "File this item", origin: "lark", status: "completed", response, upstream_turn_id: "native-legacy",
    created_at: "2026-01-01T00:02:00Z", completed_at: "2026-01-01T00:03:00Z"};
  const work = {remaining: "File the original item", dependency: "Source outage", effects_settled: true};
  const original = {...completed, turn_id: "earlier", client_turn_id: `external-${"d".repeat(24)}`,
    upstream_turn_id: "native-earlier", created_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:01:00Z", response: {...response, unfinished_work: work}};
  const reviewed = {...completed, turn_id: "reviewed", client_turn_id: "earlier-review-1",
    completed_at: "2026-01-01T00:04:00Z", response: {...response, message: "Verified the existing item in the catalog"}};
  const reserved = {version: 21, client_turn_id: reviewed.client_turn_id, failed_turn_id: original.turn_id,
    read_only_review: true, original_turn: original, binding_snapshot: row, unfinished_work: work};
  const sibling = {request_ref: "d".repeat(24), binding_id: row.binding_id, session_id: "original", source,
    message: completed.message, origin: "lark", command: null, status: "accepted", delivery_verified: true,
    turn_id: reviewed.turn_id, context_recovery: reserved};
  const related = {request: sibling, turn: reviewed, steering_observed: false};
  const session = {session_id: "original", goal_id: null, agent_id: "codex", upstream_mode: "chat", status: "ready",
    upstream_thread_id: "native", active_turn_id: null, project_context: context,
    channel_id: `project.external.${row.binding_id}.${source.source_ref}`};
  const native = {thread_id: "native", turn_id: "native-legacy", read_only_review: true,
    completed_result_review: true, untyped_result_review: true, unfinished_work: null, response,
    visible_response: response.message, native_inventory_sha256: "a".repeat(64)};
  const packet = {request: {request_ref: "f".repeat(24), binding_id: row.binding_id, source, command: null,
    status: "accepted", delivery_verified: true, session_id: "original", turn_id: completed.turn_id,
    message: completed.message, origin: "lark"}, binding: row, current_session: session,
    context_recovery: {version: 21, failed_turn: completed, observed_at: "2026-01-01T00:10:00Z",
      live_work_owner: false, source_verified: true, stop_observed: false, steering_observed: false,
      queued_input_present: false, attachments_present: false, related_result_review: related, native_observation: native}};
  const plan = planBoundConversationRequest(packet);
  assert.equal(plan.recovery_eligible, true);
  assert.equal(plan.legacy_result_review, true);
  assert.equal(plan.read_only_review, true);
  assert.equal(plan.work_attempt, undefined);
  assert.equal(plan.unfinished_work, undefined);
  assert.ok(String(plan.client_turn_id).startsWith(`external-${packet.request.request_ref}-`));
  assert.notEqual(plan.client_turn_id, reviewed.client_turn_id);
  for (const change of [{related_result_review: null}, {source_verified: false}, {live_work_owner: true},
    {stop_observed: true}, {steering_observed: true}, {queued_input_present: true}, {attachments_present: true},
    {native_observation: {...native, untyped_result_review: false}},
    {native_observation: {...native, response: {...response, message: "A different answer"}}},
    {native_observation: {...native, all_effects_settled: true}},
    {related_result_review: {...related, steering_observed: true}},
    {related_result_review: {...related, turn: {...reviewed, completed_at: completed.completed_at}}},
    {related_result_review: {...related, turn: {...reviewed, response: {...response, gate: {kind: "evidence_gap"}}}}}]) {
    assert.equal(planBoundConversationRequest({...packet,
      context_recovery: {...packet.context_recovery, ...change}}).recovery_eligible, false);
  }
  for (const change of [{request_ref: packet.request.request_ref}, {delivery_verified: false}, {message: "Different question"},
    {binding_id: "0".repeat(24)}, {source: {...source, sender_ref: "0".repeat(24)}},
    {context_recovery: {...reserved, legacy_result_review: true}},
    {context_recovery: {...reserved, work_attempt: 1}},
    {context_recovery: {...reserved, original_turn: {...original, completed_at: "2026-01-01T00:03:00Z"}}}]) {
    assert.equal(planBoundConversationRequest({...packet, context_recovery: {...packet.context_recovery,
      related_result_review: {...related, request: {...sibling, ...change}}}}).recovery_candidate, false);
  }
  const own = {version: 21, failed_turn_id: completed.turn_id, client_turn_id: plan.client_turn_id,
    original_turn: completed, read_only_review: true, legacy_result_review: true, related_result_review: related};
  const admitted = {...completed, turn_id: "own-review", status: "queued", client_turn_id: plan.client_turn_id};
  const interrupted = {...packet, request: {...packet.request, context_recovery: own},
    context_recovery: {...packet.context_recovery, admitted_turn: admitted, native_observation: null},
    current_session: {...session, status: "busy", active_turn_id: admitted.turn_id}};
  assert.equal(planBoundConversationRequest(interrupted).admitted_turn_id, admitted.turn_id);
  const held = {...interrupted, request: {...interrupted.request, turn_id: admitted.turn_id},
    context_recovery: {...interrupted.context_recovery, retained_result_review: {reservation: own, turn: admitted}}};
  assert.equal(planBoundConversationRequest(held).recovery_candidate, false, "A delivered review never obtains another attempt");
  const failedPreparation = {...admitted, status: "failed", response: null, upstream_turn_id: null,
    error_code: "host_gate", delta_count: 0, completed_at: "2026-01-01T00:08:00Z"};
  const events = [{kind: "turn.queued", payload: {delivery_mode: "session_queue"}},
    {kind: "turn.failed", payload: {error_code: "host_gate"}}];
  const beforeModel = {...packet, request: {...packet.request, context_recovery: own, turn_id: failedPreparation.turn_id},
    context_recovery: {...packet.context_recovery, retained_result_review: {reservation: own, turn: failedPreparation},
      admitted_turn: failedPreparation, result_review_preparation: {turn: failedPreparation, events}}};
  const prepared = planBoundConversationRequest(beforeModel);
  assert.equal(prepared.recovery_eligible, true);
  assert.equal(prepared.read_only_review, true);
  assert.equal(prepared.review_preparation_attempt, 1);
  assert.equal(prepared.original_result_turn_id, completed.turn_id);
  assert.equal(prepared.client_turn_id, String(plan.client_turn_id).replace(/-review-1$/, "-preparation-1"));
  assert.equal(prepared.work_attempt, undefined);
  for (const change of [{upstream_turn_id: "possibly-dispatched"}, {delta_count: 1}, {response},
    {error_code: "runtime_error"}, {client_turn_id: "another-review"}]) {
    assert.equal(planBoundConversationRequest({...beforeModel, context_recovery: {...beforeModel.context_recovery,
      result_review_preparation: {turn: {...failedPreparation, ...change}, events}}}).recovery_eligible, false);
  }
  for (const change of [{source_verified: false}, {stop_observed: true}, {native_observation: null},
    {result_review_preparation: {turn: failedPreparation, events: [...events, {kind: "turn.started"}]}}]) {
    assert.equal(planBoundConversationRequest({...beforeModel,
      context_recovery: {...beforeModel.context_recovery, ...change}}).recovery_eligible, false);
  }
  const retriedReservation = {...own, client_turn_id: prepared.client_turn_id,
    review_preparation_attempt: 1, preparation_failed_turn_id: failedPreparation.turn_id};
  const admittedPreparation = {...admitted, turn_id: "prepared-review", client_turn_id: prepared.client_turn_id};
  const lostAck = {...beforeModel, request: {...beforeModel.request, context_recovery: retriedReservation,
    context_recovery_history: [own]}, current_session: {...session, status: "busy", active_turn_id: admittedPreparation.turn_id},
    context_recovery: {...beforeModel.context_recovery, admitted_turn: admittedPreparation, native_observation: null}};
  assert.equal(planBoundConversationRequest(lostAck).admitted_turn_id, admittedPreparation.turn_id);
  assert.equal(planBoundConversationRequest({...lostAck, request: {...lostAck.request, turn_id: admittedPreparation.turn_id}})
    .recovery_candidate, false, "A second preparation recovery is never granted");
});

test("the App steward is an explicit source preference, never a new grant or peer fallback", () => {
  const steward = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: []};
  const configured = planConversationBinding({...request, binding: steward}).state;
  const choose = {current: configured, expected_revision: 1, operation: "select_app_steward",
    binding_id: row.binding_id, observation, available_projects: [project]};
  const selected = planConversationBinding(choose).state;
  assert.equal(selected.app_steward_binding_id, row.binding_id);
  assert.deepEqual(selected.bindings, configured.bindings, "Selection must not revise any grant or managed actor");
  assert.equal(planConversationBinding({...choose, current: selected, expected_revision: 2}).changed, false);
  for (const denied of [{expected_revision: 0}, {binding_id: "f".repeat(24)},
    {observation: {...observation, provider_ref: "f".repeat(24)}},
    {observation: {...observation, operator_ref: "f".repeat(24)}},
    {observation: {...observation, verified: false}}, {available_projects: []},
    {current: {...configured, bindings: [row]}}]) {
    assert.throws(() => planConversationBinding({...choose, ...denied}));
  }
  const disconnected = planConversationBinding({current: selected, expected_revision: 2,
    operation: "disconnect", binding_id: row.binding_id}).state;
  assert.equal(disconnected.app_steward_binding_id, row.binding_id, "Revocation retains the unavailable original selection");
  assert.throws(() => listLocalBoundSessions({current: disconnected, binding_id: row.binding_id,
    app_steward_entry: true, observation, available_projects: [project], sessions: []}), /no longer authorized/);
  const cleared = planConversationBinding({current: disconnected, expected_revision: 3,
    operation: "select_app_steward", binding_id: null}).state;
  assert.equal(cleared.app_steward_binding_id, null);
  assert.deepEqual(cleared.bindings, []);
  // A different steward may still be inspected normally, but cannot claim to
  // be the explicitly selected App entry, including with an empty inventory.
  assert.throws(() => listLocalBoundSessions({current: configured, binding_id: row.binding_id,
    app_steward_entry: true, observation, available_projects: [project], sessions: []}), /selected original steward/);
});

test("a legacy Steward recovery adopts its exact admitted Turn after the session advances", () => {
  const selected = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: []};
  const configured = planConversationBinding({...request, binding: selected}).state;
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
    observation, available_projects: [project], ...source}).context;
  const ref = "f".repeat(24), message = "Original synthetic request";
  const session = {session_id: "original", goal_id: "loopx-manager", agent_id: "codex",
    upstream_mode: "chat", status: "ready", upstream_thread_id: "updated-native", active_turn_id: null,
    manager_context_version: 22, steward_context: context,
    channel_id: `manager.external.native.${row.binding_id}.${source.source_ref}`};
  const failed = {session_id: "original", turn_id: "failed", status: "failed", delta_count: 0,
    response: null, error_code: "context_window_exceeded", upstream_turn_id: "native-failed"};
  const reservation = {version: 22, failed_turn_id: "failed", client_turn_id: `external-${ref}-recovery-22`};
  const admitted = {session_id: "original", turn_id: "continued", status: "completed",
    client_turn_id: reservation.client_turn_id, origin: "lark", message, attachments: []};
  const packet = {binding: selected, current_session: session, request: {request_ref: ref, command: null,
    status: "accepted", delivery_verified: true, binding_id: row.binding_id, session_id: "original",
    turn_id: "failed", source, message, origin: "lark", context_recovery: reservation},
  context_recovery: {version: 22, failed_turn: failed, steering_observed: false, source_verified: true,
    native_observation: null, admitted_turn: admitted}};
  const plan = planBoundConversationRequest(packet);
  assert.equal(plan.recovery_eligible, true);
  assert.equal(plan.admitted_turn_id, "continued");
  assert.deepEqual(plan.native_thread_ids, []);
  assert.equal(planBoundConversationRequest({...packet, context_recovery: {
    ...packet.context_recovery, source_verified: false}}).recovery_eligible, false);
  for (const change of [{session_id: "foreign"}, {client_turn_id: "foreign"}, {turn_id: failed.turn_id},
    {message: "Changed request"}, {origin: "external"}, {attachments: [{type: "image"}]}]) {
    assert.equal(planBoundConversationRequest({...packet, context_recovery: {
      ...packet.context_recovery, admitted_turn: {...admitted, ...change}}}).recovery_eligible, false);
  }
});

for (const kind of ["project", "steward"]) for (const retryKind of ["routing", "quota"]) {
  test(`${kind} ${retryKind} recovery is bounded, source-bound and independent of context migrations`, () => {
    const selected = kind === "steward" ? {...row, context_kind: kind, grant: "portfolio_read", goal_ids: []} : row;
    const configured = planConversationBinding({...request, binding: selected}).state;
    const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
    const context = resolveBoundConversation({current: configured, binding_id: row.binding_id,
      observation, available_projects: [project], ...source}).context;
    const session = {session_id: "original", goal_id: kind === "steward" ? "loopx-manager" : null,
      agent_id: "codex", upstream_mode: "chat", status: "ready",
      upstream_thread_id: "native", active_turn_id: null, manager_context_version: 21,
      channel_id: `${kind === "steward" ? "manager.external.native" : "project.external"}.${row.binding_id}.${source.source_ref}`,
      ...(kind === "steward" ? {steward_context: context} : {project_context: context})};
    const failed = {session_id: "original", turn_id: "failed", status: "failed", delta_count: 0,
      response: null, error_code: "host_gate", upstream_turn_id: "native-failed", completed_at: "2026-01-01T00:00:00Z"};
    const observed = {thread_id: "native", turn_id: "native-failed", codex_error_info: "other",
      pre_execution_routing_timeout: true, only_pre_execution_items: true, only_original_user_item: false};
    const attemptKey = `${retryKind}_attempt`;
    if (retryKind === "quota") {
      Object.assign(failed, {error_code: "usage_limit_exceeded"});
      Object.assign(observed, {codex_error_info: "usageLimitExceeded", only_original_user_item: true,
        pre_execution_routing_timeout: false, only_pre_execution_items: false, quota_available: true,
        quota_observed_at: Date.parse("2026-01-01T00:10:00Z") / 1000});
    }
    const packet = {request: {request_ref: "f".repeat(24), command: null, status: "accepted", delivery_verified: true,
      message: "Original synthetic request", origin: "lark",
      binding_id: row.binding_id, session_id: "original", turn_id: "failed", source},
      binding: selected, current_session: session, context_recovery: {version: 21, failed_turn: failed,
        observed_at: "2026-01-01T00:10:00Z", steering_observed: false, source_verified: true, native_observation: observed}};
    const plan = planBoundConversationRequest(packet);
    assert.equal(plan.recovery_eligible, true);
    assert.equal(plan[attemptKey], 1);
    assert.equal(plan.not_before, "2026-01-01T00:01:00.000Z");
    const reservation = {version: 21, failed_turn_id: "failed", [attemptKey]: 1, client_turn_id: plan.client_turn_id};
    assert.deepEqual(planBoundConversationRequest({...packet, request: {...packet.request, context_recovery: reservation}}), plan);
    const admitted = {session_id: "original", turn_id: "already-admitted", client_turn_id: plan.client_turn_id,
      message: packet.request.message, origin: "lark", status: "completed", attachments: []};
    const crash = {...packet, request: {...packet.request, context_recovery: reservation},
      current_session: {...session, status: "busy", active_turn_id: "already-admitted"},
      context_recovery: {...packet.context_recovery, native_observation: null, admitted_turn: admitted}};
    assert.equal(planBoundConversationRequest(crash).admitted_turn_id, "already-admitted");
    assert.equal(planBoundConversationRequest({...crash,
      context_recovery: {...crash.context_recovery, stop_observed: true,
        later_equivalent_input_present: true}}).admitted_turn_id, "already-admitted");
    for (const change of [{session_id: "foreign"}, {client_turn_id: "foreign"},
      {message: "another request"}, {attachments: [{type: "image"}]}]) {
      assert.equal(planBoundConversationRequest({...crash,
        context_recovery: {...crash.context_recovery, admitted_turn: {...admitted, ...change}}}).recovery_eligible, false);
    }
    const next = {...packet, request: {...packet.request, turn_id: "next", context_recovery: reservation},
      context_recovery: {...packet.context_recovery, failed_turn: {...failed, turn_id: "next"}}};
    assert.equal(planBoundConversationRequest(next)[attemptKey], 2);
    assert.equal(planBoundConversationRequest(next).not_before, "2026-01-01T00:02:00.000Z");
    assert.equal(planBoundConversationRequest({...next, request: {...next.request,
      context_recovery: {...reservation, [attemptKey]: 3}}}).recovery_candidate, false);
    for (const change of [{source_verified: false}, {steering_observed: true}, {stop_observed: true},
      {later_equivalent_input_present: true},
      {observed_at: "2026-01-01T00:00:59Z"}, {observed_at: "invalid"},
      {native_observation: {...observed, ...(retryKind === "quota"
        ? {only_original_user_item: false} : {only_pre_execution_items: false})}},
      ...(retryKind === "quota" ? [
        {native_observation: {...observed, quota_available: false}},
        {native_observation: {...observed, quota_observed_at: 0}},
        {native_observation: {...observed, quota_observed_at: Date.parse("2026-01-01T00:11:00Z") / 1000}},
      ] : []),
      {native_observation: {...observed, thread_id: "foreign"}},
      {native_observation: {...observed, codex_error_info: "cyberPolicy"}}]) {
      assert.notEqual(planBoundConversationRequest({...packet,
        context_recovery: {...packet.context_recovery, ...change}}).recovery_eligible, true);
    }
    for (const change of [{status: "closed"}, {status: "stale"}, {goal_id: "other"}, {active_turn_id: "other"},
      {session_mode: "attached_host"}, {channel_id: "foreign"}]) {
      assert.notEqual(planBoundConversationRequest({...packet,
        current_session: {...session, ...change}}).recovery_eligible, true);
    }
    if (kind === "project") {
      assert.equal(planBoundConversationRequest({...packet,
        binding: {...selected, audience: "group", group_refs: ["1".repeat(24)]}}).recovery_eligible, false);
      assert.equal(planBoundConversationRequest({...packet,
        binding: {...selected, grant: "workspace_write"}}).recovery_eligible, false);
    }
  });
}

for (const kind of ["project", "steward"]) {
  const selected = kind === "steward" ? {...row, context_kind: kind, grant: "portfolio_read", goal_ids: []} : row;
  const configured = planConversationBinding({...request, binding: selected}).state;
  const lookup = {current: configured, binding_id: row.binding_id, observation, available_projects: [project]};
  const context = resolveBoundConversation({...lookup, source_ref: "e".repeat(24),
    sender_ref: row.operator_ref, private_human_message: true}).context;
  const session = {session_id: "original", goal_id: kind === "steward" ? "loopx-manager" : null,
    agent_id: "codex", executor_endpoint_id: "codex", session_mode: "managed_runtime", status: "ready",
    channel_id: `${kind === "steward" ? "manager.external.native" : "project.external"}.${row.binding_id}.${"e".repeat(24)}`,
    ...(kind === "steward" ? {steward_context: context} : {project_context: context})};
  const continuation = {...lookup, source_ref: "e".repeat(24), sender_ref: row.operator_ref,
    private_human_message: false, origin: "web", session_context: context};

  test(`${kind} discovery reuses verified saved identity and excludes unrelated or closed Sessions`, () => {
    const snapshots = [session, {...session, session_id: "historical", status: "closed"},
      {session_id: "local", goal_id: null, channel_id: `project.${project.project_ref}`}];
    const before = structuredClone(snapshots);
    assert.deepEqual(listLocalBoundSessions({...lookup, sessions: snapshots}), {session_ids: ["original"]});
    assert.deepEqual(snapshots, before);
    assert.deepEqual(listLocalBoundSessions({...lookup, sessions: []}), {session_ids: []});
    assert.throws(() => listLocalBoundSessions({...lookup, sessions: [session, session]}), /ambiguous/);
    assert.deepEqual(resolveBoundConversation({...continuation, session}).context, context);
    assert.throws(() => resolveBoundConversation({...continuation, session: {...session, status: "closed"}}),
      /canonical bound Session identity/);
  });

  test(`${kind} discovery refuses changed canonical role, executor, channel or saved source`, () => {
    for (const change of [{goal_id: "another-goal"}, {channel_id: "manager"}, {agent_id: "other"},
      {executor_endpoint_id: "other"}, {session_mode: "attached_host"},
      {[kind === "steward" ? "project_context" : "steward_context"]: context},
      {[kind === "steward" ? "steward_context" : "project_context"]:
        {...context as Record<string, unknown>, operator_ref: "f".repeat(24)}},
      {[kind === "steward" ? "steward_context" : "project_context"]:
        {...context as Record<string, unknown>, source_ref: "f".repeat(24)}}]) {
      assert.throws(() => listLocalBoundSessions({...lookup, sessions: [{...session, ...change}]}));
      assert.throws(() => resolveBoundConversation({...continuation, session: {...session, ...change}}));
    }
    for (const change of [{current}, {available_projects: []},
      {observation: {...observation, verified: false}},
      {observation: {...observation, provider_ref: "f".repeat(24)}},
      {observation: {...observation, operator_ref: "f".repeat(24)}},
      {observation: {...observation, transport_ref: "another-app"}}]) {
      // An empty inventory is not permission to bypass current source checks.
      assert.throws(() => listLocalBoundSessions({...lookup, ...change, sessions: []}));
    }
  });

  test(`${kind} bound identity is independent of JSON object property order`, () => {
    const reordered = Object.fromEntries(Object.entries(context as Record<string, unknown>).reverse());
    const field = kind === "steward" ? "steward_context" : "project_context";
    const restored = {...session, [field]: reordered};
    assert.deepEqual(listLocalBoundSessions({...lookup, sessions: [restored]}), {session_ids: ["original"]});
    // The ingress snapshot and canonical store may use different serialization
    // order. Neither changes the audience, workspace or original Session.
    for (const saved of [context, reordered]) {
      assert.deepEqual(resolveBoundConversation({...continuation, session_context: saved,
        session: restored}).context, context);
    }
    for (const change of [{source_ref: "f".repeat(24)}, {grant: "workspace_write"},
      {operator_ref: "f".repeat(24)}, {unexpected: true}]) {
      const changed = {...reordered, ...change};
      assert.throws(() => resolveBoundConversation({...continuation, session_context: changed,
        session: {...restored, [field]: changed}}));
    }
  });
}

test("local discovery never promotes a group binding to a private recipient", () => {
  const group = "1".repeat(24);
  const configured = planConversationBinding({...request,
    binding: {...row, audience: "group", group_refs: [group]}, available_group_refs: [group]}).state;
  assert.throws(() => listLocalBoundSessions({current: configured, binding_id: row.binding_id,
    observation, available_projects: [project], sessions: []}), /private binding/);
});

test("an unfrozen recovered stop cannot borrow current cancellation scope", () => {
  const request = {request_ref: "e".repeat(24), command: "stop"};
  const current = {session_id: "new-session", active_turn_id: "new-active"};
  assert.throws(() => planBoundConversationRequest({request, current_session: current,
    recovering_request: true, background_turn_ids: ["new-background"]}), /original stop scope was not recorded/);
  assert.equal(planBoundConversationRequest({request, current_session: current,
    recovering_request: false}).turn_id, "new-active");
  assert.deepEqual(planBoundConversationRequest({request: {...request, target_recorded: true,
    session_id: "original", turn_id: null, background_turn_ids: ["old"]},
    current_session: current, recovering_request: true, background_turn_ids: ["new-background"]}), {
    operation: "stop", session_id: "original", turn_id: null,
    background_turn_ids: ["old"], response_code: "stop_requested"});
});

test("group topics require an observed App membership and cannot inherit private recipients or portfolio", () => {
  const group = "1".repeat(24), topic = "2".repeat(24);
  const configure = {...request, binding: {...row, audience: "group", group_refs: [group]},
    available_group_refs: [group]};
  const next = planConversationBinding(configure).state;
  const use = {current: next, binding_id: row.binding_id, source_ref: topic, topic_ref: topic,
    group_ref: group, sender_ref: "3".repeat(24), private_human_message: false,
    group_human_message: true, observation, available_projects: [project]};
  const resolved = resolveBoundConversation(use);
  const context = resolved.context as Record<string, unknown>;
  assert.equal(context.audience, "bound_group");
  assert.equal(context.filesystem_scope, "workspace_only");
  assert.equal(resolved.owner_manager_audience, false);
  assert.deepEqual(resolveBoundConversation({...use, session_context: context}).context, context);
  assert.deepEqual(resolveConversationScope({goal_id: null, channel_id: resolved.channel_id,
    project_context: context, origin: "lark"}),
    {kind: "project_workspace", goal_ids: [], private_conversation: false});
  const host = projectConversationIdentity({context}).host_config as Record<string, unknown>;
  assert.deepEqual(host.skills, {include_instructions: false});
  assert.equal(host.project_doc_max_bytes, 0);
  assert.equal(host.allow_login_shell, false);
  for (const denied of [{group_ref: "4".repeat(24)}, {topic_ref: "4".repeat(24)},
    {private_human_message: true}, {group_human_message: false},
    {observation: {...observation, verified: false}}, {current}]) {
    assert.throws(() => resolveBoundConversation({...use, ...denied}));
  }
  for (const denied of [{available_group_refs: []}, {available_group_refs: undefined},
    {binding: {...configure.binding, context_kind: "steward", grant: "portfolio_read", goal_ids: []}},
    {binding: {...configure.binding, executor_endpoint_id: "claude-code"}},
    {binding: {...configure.binding, group_refs: [group, group]}}]) {
    assert.throws(() => planConversationBinding({...configure, ...denied}));
  }
  for (const command of ["agents", "select_agent", "select_project"]) {
    assert.equal(planBoundConversationRequest({binding: configure.binding,
      request: {request_ref: topic, command}, current_session: null}).response_code, "group_recipient_unavailable");
  }
  const changed = planConversationBinding({...configure, current: next, expected_revision: 1,
    binding: {...configure.binding, binding_id: "5".repeat(24), group_refs: [group, "4".repeat(24)]},
    available_group_refs: [group, "4".repeat(24)]}).state;
  assert.throws(() => resolveBoundConversation({...use, current: changed}), /no longer authorized/);
  assert.equal(project.audience, "local_owner");
});

test("only a current verified private steward binding proves the machine owner audience", () => {
  const steward = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: []};
  const next = planConversationBinding({...request, binding: steward}).state;
  const use = {current: next, binding_id: row.binding_id, source_ref: "e".repeat(24),
    sender_ref: row.operator_ref, private_human_message: true, observation, available_projects: [project]};
  assert.equal(resolveBoundConversation(use).owner_manager_audience, true);
  assert.equal(resolveBoundConversation({...use,
    available_projects: [{...project, filesystem_scope: "workspace_only"}]}).owner_manager_audience, false);
  const projectState = planConversationBinding(request).state;
  assert.equal(resolveBoundConversation({...use, current: projectState}).owner_manager_audience, false);
  for (const denied of [{private_human_message: false}, {sender_ref: "f".repeat(24)},
    {observation: {...observation, provider_ref: "f".repeat(24)}},
    {observation: {...observation, verified: false}}, {available_projects: []}, {current}]) {
    assert.throws(() => resolveBoundConversation({...use, ...denied}));
  }
});

test("images use the managed conversation and never disappear into commands or an attached host", () => {
  const request = {request_ref: "e".repeat(24), command: null};
  assert.equal(planBoundConversationRequest({request, current_session: null, attachment_count: 1}).operation, "admit_turn");
  for (const command of ["stop", "new", "select_agent", "commission"]) {
    assert.equal(planBoundConversationRequest({request: {...request, command}, current_session: null,
      attachment_count: 1}).response_code, "unsupported_attachment");
  }
  assert.equal(planBoundConversationRequest({request, current_session: null, attachment_count: 1,
    agent_target: {session_id: "existing-host"}}).response_code, "unsupported_attachment");
  for (const count of [-1, 5, 0.5, "1"]) {
    assert.throws(() => planBoundConversationRequest({request, current_session: null, attachment_count: count}), /attachment count/);
  }
});

test("owner steward scope reads the current registry, preserves its Session and never widens a project or legacy scope", () => {
  const steward = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: [], goal_scope: "all_registered"};
  const next = planConversationBinding({...request, binding: steward}).state;
  const use = {current: next, binding_id: row.binding_id, source_ref: "e".repeat(24), sender_ref: row.operator_ref,
    private_human_message: true, observation, available_projects: [project], available_goal_ids: ["notes", "maintenance"]};
  const selected = resolveBoundConversation(use);
  assert.deepEqual((selected.context as Record<string, unknown>).goal_ids, ["maintenance", "notes"]);
  assert.deepEqual((resolveBoundConversation({...use, available_goal_ids: ["new-goal"], session_context: selected.context}).context as Record<string, unknown>).goal_ids, ["new-goal"]);
  assert.throws(() => resolveBoundConversation({...use, available_goal_ids: undefined}), /Goal scope/);
  assert.throws(() => resolveBoundConversation({...use, observation: {...observation, verified: false}}), /verified/);
  assert.throws(() => planConversationBinding({...request, binding: {...row, goal_scope: "all_registered"}}), /scope/);
  const restricted = planConversationBinding({...request, current: next, expected_revision: 1,
    binding: {...steward, goal_scope: "selected"}}).state;
  assert.deepEqual((resolveBoundConversation({...use, current: restricted}).context as Record<string, unknown>).goal_ids, []);
  assert.throws(() => resolveBoundConversation({...use, current: restricted, session_context: selected.context}), /context changed/);
  const legacy = planConversationBinding({...request, binding: {...steward, goal_scope: undefined}}).state;
  assert.deepEqual((resolveBoundConversation({...use, current: legacy}).context as Record<string, unknown>).goal_ids, []);
});

test("explicit project writes remain App-bound and cannot exceed the host grant, another executor or an existing Session", () => {
  const original = planConversationBinding(request).state as typeof current;
  const use = {current: original, binding_id: row.binding_id, source_ref: "e".repeat(24),
    sender_ref: row.operator_ref, private_human_message: true, observation, available_projects: [project]};
  const read = resolveBoundConversation(use);
  assert.equal(projectConversationIdentity({context: read.context}).sandbox, "read-only");
  assert.equal(projectConversationIdentity({context: {...project, grant: "workspace_write"}}).sandbox, "workspace-write");
  const writable = [{...project, grant: "workspace_write"}];
  assert.throws(() => planConversationBinding({...request, current: original, expected_revision: 1,
    binding: {...row, grant: "workspace_write"}, available_projects: writable}), /new binding identity/);
  const write = {...row, binding_id: "f".repeat(24), grant: "workspace_write"};
  const next = planConversationBinding({...request, current: original, expected_revision: 1, binding: write, available_projects: writable}).state;
  assert.throws(() => planConversationBinding({...request, binding: write}), /workspace/);
  assert.throws(() => resolveBoundConversation({...use, current: next, binding_id: write.binding_id}), /write grant/);
  use.available_projects = writable;
  const selected = resolveBoundConversation({...use, current: next, binding_id: write.binding_id});
  assert.equal(projectConversationIdentity({context: selected.context}).sandbox, "workspace-write");
  assert.deepEqual(resolveConversationScope({goal_id: null, channel_id: selected.channel_id,
    project_context: selected.context, origin: "lark"}), {kind: "project_workspace", goal_ids: [], private_conversation: false});
  assert.throws(() => resolveBoundConversation({...use, current: next}), /no longer authorized/);
  assert.throws(() => resolveBoundConversation({...use, current: next, binding_id: write.binding_id,
    session_context: read.context}), /context changed/);
  for (const bad of [{...write, executor_endpoint_id: "claude-code"},
    {...write, context_kind: "steward", goal_ids: []}, {...write, grant: "danger-full-access"}]) {
    assert.throws(() => planConversationBinding({...request, binding: bad}), /unsupported/);
  }
  assert.throws(() => resolveBoundConversation({...use, current: next, binding_id: write.binding_id,
    sender_ref: "c".repeat(24)}), /audience/);
});

test("binding independently verifies the owner and does not create a Goal", () => {
  const result = planConversationBinding(request);
  assert.equal(result.changed, true);
  const next = result.state as typeof current;
  assert.equal(next.revision, 1);
  assert.equal("goal_id" in next.bindings[0], false);
  const repeat = planConversationBinding({...request, current: next, expected_revision: 1,
    binding: {...row, binding_id: "e".repeat(24)}});
  assert.equal(repeat.changed, false);
  for (const proof of [{...observation, verified: false}, {...observation, operator_ref: "f".repeat(24)},
    {...observation, provider_ref: "e".repeat(24)}]) {
    assert.throws(() => planConversationBinding({...request, observation: proof}), /independently verified/);
  }
  assert.throws(() => planConversationBinding({...request, available_projects: []}), /workspace/);
  assert.throws(() => planConversationBinding({...request, expected_revision: 1}), /revision/);
});

test("a selected steward retains its bounded portfolio for provider and local continuation", () => {
  const steward = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: []};
  const next = planConversationBinding({...request, binding: steward}).state as typeof current;
  const use = {current: next, binding_id: row.binding_id, source_ref: "e".repeat(24),
    sender_ref: row.operator_ref, private_human_message: true, observation, available_projects: [project]};
  const selected = resolveBoundConversation(use);
  assert.equal(resolveConversationScope({channel_id: selected.channel_id, goal_id: "loopx-manager",
    steward_context: selected.context, origin: "lark"}).bound_steward, true);
  assert.equal(resolveConversationScope({channel_id: selected.channel_id, goal_id: "loopx-manager",
    steward_context: selected.context, origin: "web"}).bound_steward, true);
  const context = selected.context as Record<string, unknown>;
  const proposal = {proposal_id: "proposal-" + "f".repeat(32), status: "applied", action_kind: "goal.create",
    context, receipt: {outcome: "goal_created", projection_verified: true, resource_ids: {goal_id: "fresh-goal"}}};
  const adoption = {current: next, expected_revision: 1, operation: "adopt_created_goal", binding_id: row.binding_id,
    context, proposal, goal: {goal_id: "fresh-goal", workspace_path: project.workspace_path, creation_operation_id: proposal.proposal_id}};
  const result = planConversationBinding(adoption).state as typeof current;
  assert.deepEqual((result.bindings[0] as Record<string, unknown>).goal_ids, ["fresh-goal"]);
  assert.deepEqual((resolveBoundConversation({...use, current: result, session_context: context}).context as Record<string, unknown>).goal_ids, ["fresh-goal"]);
  for (const bad of [{...adoption, goal: {...adoption.goal, workspace_path: "/other/private"}},
    {...adoption, proposal: {...proposal, status: "preview_ready"}},
    {...adoption, proposal: {...proposal, context: {...context, source_ref: "f".repeat(24)}}},
    {...adoption, proposal: {...proposal, receipt: {...proposal.receipt, projection_verified: false}}}]) {
    assert.throws(() => planConversationBinding(bad));
  }
});

test("a commission requires an explicit budget and confirmation stays within its audience and expiry", () => {
  assert.deepEqual(stewardCommand({command: "commission", message: "/delegate --tokens 12000 Read README"}),
    {argument: "Read README", native_token_budget: 12000});
  for (const message of ["delegate work", "/delegate Read README", "/delegate --tokens 0 work", "/delegate --tokens 1000"])
    assert.throws(() => stewardCommand({command: "commission", message}));
  const context = {...project, kind: "bound_steward", audience: "bound_owner", grant: "portfolio_read", goal_ids: [],
    binding_id: row.binding_id, source_ref: "e".repeat(24), provider_ref: row.provider_ref, operator_ref: row.operator_ref};
  const proposal = {action_kind: "goal.create", created_at: "2026-01-01T10:00:00Z",
    context: {...context, kind: "manager", goal_id: "loopx-manager", expires_at: "2026-01-01T10:15:00Z"}};
  assert.equal(authorizeStewardCreation({context, proposal, now: "2026-01-01T10:01:00Z"}).authorized, true);
  assert.throws(() => authorizeStewardCreation({context, proposal, now: "2026-01-01T10:16:00Z"}), /expired/);
  assert.throws(() => authorizeStewardCreation({context: {...context, provider_ref: "f".repeat(24)}, proposal,
    now: "2026-01-01T10:01:00Z"}), /audience/);
  assert.throws(() => planBoundConversationRequest({binding: row, current_session: null,
    request: {request_ref: "e".repeat(24), command: "commission"}}), /selected steward/);
});

test("one App has one binding owner, and a context change cannot move an existing Session", () => {
  const next = planConversationBinding(request).state as typeof current;
  const second = {...row, transport_ref: "other-app", binding_id: "e".repeat(24)};
  assert.throws(() => planConversationBinding({...request, current: next, expected_revision: 1,
    binding: second, observation: {...observation, transport_ref: "other-app"}}), /ownership/);
  const other = {...project, project_ref: "f".repeat(24), workspace_path: "/authorized/other"};
  assert.throws(() => planConversationBinding({...request, current: next, expected_revision: 1,
    binding: {...row, project_ref: other.project_ref}, available_projects: [other]}), /new binding identity/);
});

test("Todo confirmation names a current Goal, preserves its owner and cannot start work", () => {
  const context = {...project, kind: "bound_steward", audience: "bound_owner", grant: "portfolio_read",
    goal_ids: ["maintenance"], binding_id: row.binding_id, source_ref: "e".repeat(24),
    provider_ref: row.provider_ref, operator_ref: row.operator_ref};
  const proposal = {action_kind: "todo.create", created_at: "2026-01-01T10:00:00Z",
    context: {...context, kind: "manager", goal_id: "loopx-manager", expires_at: "2026-01-01T10:15:00Z"},
    normalized_parameters: {goal_id: "maintenance", text: "Remember a feature", priority: "P2"}};
  const request = {context, proposal, now: "2026-01-01T10:01:00Z", operation: "confirm"};
  assert.equal(authorizeStewardCreation(request).authorized, true);
  for (const change of [{goal_id: "foreign"}, {goal_id: undefined}, {start_execution: true}, {agent_id: "worker"}]) {
    assert.throws(() => authorizeStewardCreation({...request,
      proposal: {...proposal, normalized_parameters: {...proposal.normalized_parameters, ...change}}}), /Todo confirmation/);
  }
  for (const field of ["binding_id", "source_ref", "provider_ref", "operator_ref", "project_ref"]) {
    assert.throws(() => authorizeStewardCreation({...request, context: {...context, [field]: "f".repeat(24)}}), /audience/);
  }
  for (const operation of ["stop", "resume"]) {
    assert.throws(() => authorizeStewardCreation({...request, operation}), /cannot start/);
  }
  assert.throws(() => authorizeStewardCreation({...request, now: "2026-01-01T10:16:00Z"}), /expired/);
});

test("external project audience has its own Session identity without portfolio or peer scope", () => {
  const next = planConversationBinding(request).state;
  const use = {current: next, binding_id: row.binding_id, source_ref: "e".repeat(24),
    sender_ref: row.operator_ref, private_human_message: true, observation, available_projects: [project]};
  const selected = resolveBoundConversation(use);
  assert.equal(selected.channel_id, `project.external.${row.binding_id}.${use.source_ref}`);
  assert.deepEqual(resolveConversationScope({channel_id: selected.channel_id, project_context: selected.context,
    goal_id: null, origin: "lark"}), {kind: "project_workspace", goal_ids: [], private_conversation: false});
  for (const bad of [{...use, sender_ref: "f".repeat(24)}, {...use, private_human_message: false},
    {...use, available_projects: []}, {...use, observation: {...observation, provider_ref: "f".repeat(24)}}]) {
    assert.throws(() => resolveBoundConversation(bad));
  }
  assert.throws(() => resolveBoundConversation({...use, source_ref: "f".repeat(24), session_context: selected.context}), /context changed/);
  const revoked = planConversationBinding({current: next, expected_revision: 1,
    operation: "disconnect", binding_id: row.binding_id}).state;
  assert.throws(() => resolveBoundConversation({...use, current: revoked}), /no longer authorized/);
});


test("native commands retain their recorded target across a new Session and redelivery", () => {
  const request = {request_ref: "e".repeat(24), command: "stop", target_recorded: true,
    session_id: "original", turn_id: "original-turn"};
  const current = {session_id: "replacement", active_turn_id: "different-turn"};
  assert.deepEqual(planBoundConversationRequest({request, current_session: current}), {
    operation: "stop", session_id: "original", turn_id: "original-turn", response_code: "stop_requested"});
  assert.deepEqual(planBoundConversationRequest({request: {...request, command: null}, current_session: current}), {
    operation: "admit_turn", client_turn_id: `external-${request.request_ref}`, session_id: "original", turn_id: null});
  assert.equal(planBoundConversationRequest({request: {...request, command: "unsupported"},
    current_session: null}).response_code, "unsupported_attachment");
  assert.throws(() => planBoundConversationRequest({request: {...request, command: "grant"}, current_session: current}),
    /unsupported/);
});

test("status and help project only the verified binding without opening a Session", () => {
  const context = resolveBoundConversation({current: planConversationBinding(request).state,
    binding_id: row.binding_id, source_ref: "e".repeat(24), sender_ref: row.operator_ref,
    private_human_message: true, observation, available_projects: [project]}).context;
  const input = {binding: row, context, current_session: null, queued_count: 0, active_turn: null,
    observed_at: "2026-01-01T10:00:00Z", request: {request_ref: "f".repeat(24), command: "status"}};
  const plan = planBoundConversationRequest(input);
  assert.equal(plan.operation, "reply");
  assert.equal(plan.session_id, null);
  assert.equal(plan.response_code, "no_session");
  assert.deepEqual(plan.status_snapshot, {schema_version: "loopx_chat_bound_status_v0",
    observed_at: input.observed_at, context_kind: "project", workspace_path: project.workspace_path,
    executor_endpoint_id: "codex", grant: "workspace_read", authorized_commission_count: 0,
    session_status: null, active_turn_status: null, active_turn_observation_available: true, queued_count: 0});
  assert.equal(planBoundConversationRequest({...input, request: {...input.request, command: "help"}}).response_code,
    "conversation_help");
  for (const bad of [{...input, binding: {...row, provider_ref: "a".repeat(24)}},
    {...input, queued_count: -1}, {...input, queued_count: .5}, {...input, queued_count: 1},
    {...input, observed_at: "unknown"}]) assert.throws(() => planBoundConversationRequest(bad));
});

test("status reads the canonical queue and exact active Turn, retaining unavailable evidence", () => {
  const selected = resolveBoundConversation({current: planConversationBinding(request).state,
    binding_id: row.binding_id, source_ref: "e".repeat(24), sender_ref: row.operator_ref,
    private_human_message: true, observation, available_projects: [project]});
  const session = {session_id: "original", channel_id: selected.channel_id, goal_id: null,
    project_context: selected.context, status: "busy", active_turn_id: "original-turn"};
  const turn = {session_id: session.session_id, turn_id: session.active_turn_id, status: "running"};
  const input = {binding: row, context: selected.context, current_session: session, queued_count: 2,
    active_turn: turn, observed_at: "2026-01-01T10:00:00Z",
    request: {request_ref: "f".repeat(24), command: "status"}};
  const snapshot = planBoundConversationRequest(input).status_snapshot as Record<string, unknown>;
  assert.equal(snapshot.queued_count, 2);
  assert.equal(snapshot.active_turn_status, "running");
  assert.equal((planBoundConversationRequest({...input, active_turn: null}).status_snapshot as Record<string, unknown>)
    .active_turn_observation_available, false);
  for (const bad of [{...input, current_session: {...session, channel_id: "another-owner"}},
    {...input, active_turn: {...turn, session_id: "another-session"}},
    {...input, active_turn: {...turn, turn_id: "later-turn"}},
    {...input, active_turn: {...turn, status: {pretend: "completed"}}}]) {
    assert.throws(() => planBoundConversationRequest(bad));
  }
});

test("steward status counts its fresh grant, not a global portfolio or old Session scope", () => {
  const steward = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: ["fresh-goal"]};
  const context = {...project, kind: "bound_steward", audience: "bound_owner", grant: "portfolio_read",
    goal_ids: ["fresh-goal"], binding_id: row.binding_id, source_ref: "e".repeat(24),
    provider_ref: row.provider_ref, operator_ref: row.operator_ref};
  const plan = planBoundConversationRequest({binding: steward, context, queued_count: 0, active_turn: null,
    observed_at: "2026-01-01T10:00:00Z", request: {request_ref: "f".repeat(24), command: "help"},
    current_session: {session_id: "steward", channel_id: `manager.external.native.${row.binding_id}.${context.source_ref}`,
      goal_id: "loopx-manager", steward_context: {...context, goal_ids: []}, status: "ready", active_turn_id: null}});
  assert.equal((plan.status_snapshot as Record<string, unknown>).authorized_commission_count, 1);
  assert.equal(JSON.stringify(plan).includes("fresh-goal"), false);
});

test("an exact registered attached Session needs its own App grant and workspace", () => {
  const target = {target_ref: "f".repeat(24), host_ref: "a".repeat(24), session_id: "exact-session", goal_id: "sample-goal",
    goal_instance_id: "lifetime-a", agent_id: "notes-worker", executor_endpoint_id: "codex"};
  const observed = {session: {...target, session_mode: "attached_host", status: "ready"},
    goal: {goal_id: target.goal_id, workspace_path: project.workspace_path, registered_agents: [target.agent_id]},
    host_binding_verified: true, host_ref: target.host_ref, host_audience_binding_ids: []};
  const configured = planConversationBinding(request).state as typeof current;
  const grantInput = {current: configured, expected_revision: 1, operation: "grant_agent_target",
    binding_id: row.binding_id, target, target_observation: observed, observation, available_projects: [project]};
  const longGoal = "g".repeat(160);
  assert.equal(planConversationBinding({...grantInput, target: {...target, goal_id: longGoal},
    target_observation: {...observed, session: {...observed.session, goal_id: longGoal},
      goal: {...observed.goal, goal_id: longGoal}}}).changed, true);
  const granted = planConversationBinding(grantInput).state as typeof current;
  const selected = resolveBoundConversation({current: granted, binding_id: row.binding_id, source_ref: "e".repeat(24),
    sender_ref: row.operator_ref, private_human_message: true, observation, available_projects: [project]});
  const input = {binding: (granted.bindings as unknown[])[0], context: selected.context, target, target_observation: observed};
  assert.deepEqual(resolveConversationAgentTarget(input).target, target);
  for (const bad of [{...input, target: {...target, agent_id: "codex"}},
    {...input, context: {...selected.context as object, provider_ref: "e".repeat(24)}},
    {...input, target_observation: {...observed, host_binding_verified: false}},
    {...input, target_observation: {...observed, session: {...observed.session, goal_instance_id: "recreated"}}},
    {...input, target_observation: {...observed, goal: {...observed.goal, registered_agents: []}}},
    {...input, target_observation: {...observed, goal: {...observed.goal, workspace_path: "/other"}}},
    {...input, target_observation: {...observed, session: {...observed.session, external_conversation_binding_id: "e".repeat(24)}}}]) {
    assert.throws(() => resolveConversationAgentTarget(bad));
  }
  const revoked = planConversationBinding({current: granted, expected_revision: 2, operation: "revoke_agent_target",
    binding_id: row.binding_id, target_ref: target.target_ref, observation}).state as typeof current;
  assert.throws(() => resolveConversationAgentTarget({...input, binding: (revoked.bindings as unknown[])[0]}), /grant/);
});

test("attached status names the registered recipient and stop cannot pretend host push support", () => {
  const target = {target_ref: "f".repeat(24), host_ref: "a".repeat(24), session_id: "original", goal_id: "sample-goal",
    goal_instance_id: null, agent_id: "notes-worker", executor_endpoint_id: "codex"};
  const cadence = {goal_id: target.goal_id, agent_id: target.agent_id, automation_id: null,
    configuration_revision: 4, min_interval_minutes: 60,
    eligibility: {state: "waiting", reason: "minimum_interval_wait", eligible_now: false,
      next_eligible_at_ms: 1791637200000}};
  const context = resolveBoundConversation({current: planConversationBinding(request).state,
    binding_id: row.binding_id, source_ref: "e".repeat(24), sender_ref: row.operator_ref,
    private_human_message: true, observation, available_projects: [project]}).context;
  const session = {...target, session_mode: "attached_host", status: "busy", active_turn_id: "original-turn"};
  const input = {binding: {...row, agent_targets: [target]}, context, current_session: session, agent_target: target,
    queued_count: 2, active_turn: {session_id: "original", turn_id: "original-turn", status: "running"},
    automation_cadence: cadence,
    observed_at: "2026-01-01T10:00:00Z", request: {request_ref: "e".repeat(24), command: "status"}};
  const snapshot = planBoundConversationRequest(input).status_snapshot as Record<string, unknown>;
  assert.equal(snapshot.recipient_agent_id, "notes-worker");
  assert.equal(snapshot.queued_count, 2);
  assert.deepEqual(snapshot.automation_cadence, cadence);
  for (const command of ["stop", "new"]) assert.equal(planBoundConversationRequest({...input,
    request: {...input.request, command}}).response_code, "attached_control_unavailable");
  assert.throws(() => planBoundConversationRequest({...input, automation_cadence: undefined}), /exact selected Agent status/);
  for (const automation_cadence of [
    {...cadence, goal_id: "another-goal"},
    {...cadence, agent_id: "another-agent"},
    {...cadence, automation_id: "timer"},
    {...cadence, eligibility: {...cadence.eligibility, next_eligible_at_ms: null}},
    {...cadence, eligibility: {state: "unavailable", reason: "owner_read_failed",
      eligible_now: null, next_eligible_at_ms: null}},
  ]) {
    assert.throws(() => planBoundConversationRequest({...input, automation_cadence}));
  }
  const unavailable = {...cadence, configuration_revision: null, min_interval_minutes: null,
    eligibility: {state: "unavailable", reason: "owner_read_failed", eligible_now: null, next_eligible_at_ms: null}};
  const unavailableSnapshot = planBoundConversationRequest({...input, automation_cadence: unavailable})
    .status_snapshot as Record<string, unknown>;
  assert.deepEqual(unavailableSnapshot.automation_cadence, unavailable);
  assert.throws(() => planBoundConversationRequest({...input, request: {...input.request, command: "help"}}),
    /exact selected Agent status/);
});


test("provider and authorized local input classify the same bounded private audience", () => {
  for (const contextKind of ["project", "steward"]) {
    const selectedRow = contextKind === "project" ? row : {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: []};
    const next = planConversationBinding({...request, binding: selectedRow}).state;
    const selected = resolveBoundConversation({current: next, binding_id: row.binding_id,
      source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true,
      observation, available_projects: [project]});
    const session = {goal_id: contextKind === "project" ? null : "loopx-manager", channel_id: selected.channel_id,
      ...(contextKind === "project" ? {project_context: selected.context} : {steward_context: selected.context})};
    assert.deepEqual(resolveConversationScope({...session, origin: "external"}),
      resolveConversationScope({...session, origin: "lark"}));
    assert.deepEqual(resolveConversationScope({...session, origin: "web"}),
      resolveConversationScope({...session, origin: "external"}));
  }
});


test("local input requires the exact existing private context and fresh binding proof", () => {
  for (const kind of ["project", "steward"]) {
    const binding = kind === "project" ? row : {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: []};
    const next = planConversationBinding({...request, binding}).state;
    const providerUse = {current: next, binding_id: row.binding_id, source_ref: "e".repeat(24),
      sender_ref: row.operator_ref, private_human_message: true, observation, available_projects: [project]};
    const original = resolveBoundConversation(providerUse);
    const local = {...providerUse, origin: "web", private_human_message: false, session_context: original.context};
    assert.deepEqual(resolveBoundConversation(local), original);
    const scope = resolveConversationScope({goal_id: kind === "project" ? null : "loopx-manager",
      channel_id: original.channel_id, project_context: original.context, steward_context: original.context, origin: "web"});
    assert.equal(scope.private_conversation, false);
    assert.equal(scope.kind, kind === "project" ? "project_workspace" : "external_audience");
    for (const bad of [{session_context: undefined}, {private_human_message: true}, {origin: "model"},
      {sender_ref: "f".repeat(24)}, {current}, {available_projects: []},
      {observation: {...observation, verified: false}},
      {session_context: {...original.context as object, source_ref: "f".repeat(24)}}]) {
      assert.throws(() => resolveBoundConversation({...local, ...bad}));
    }
  }
});

test("local input cannot impersonate a group participant or inherit its workspace", () => {
  const groupRef = "1".repeat(24), topic = "2".repeat(24);
  const group = {...row, audience: "group", group_refs: [groupRef]};
  const next = planConversationBinding({...request, binding: group, available_group_refs: [groupRef]}).state;
  const use = {current: next, binding_id: row.binding_id, source_ref: topic, group_ref: groupRef, topic_ref: topic,
    sender_ref: "3".repeat(24), private_human_message: false, group_human_message: true, observation, available_projects: [project]};
  const selected = resolveBoundConversation(use);
  assert.throws(() => resolveBoundConversation({...use, origin: "web", session_context: selected.context}));
  assert.equal(resolveConversationScope({goal_id: null, channel_id: selected.channel_id,
    project_context: selected.context, origin: "web"}).kind, "unavailable");
});

test("pending batches preserve individual decisions and cannot enter execution or recovery", () => {
  const request = {request_ref: "f".repeat(24), command: null, status: "accepted",
    session_id: "original", turn_id: "failed"};
  const turn = {session_id: "original", turn_id: "failed", status: "failed", error_code: "usage_limit_exceeded"};
  const packet = {request, current_session: {session_id: "original", status: "ready"},
    pending_observation: {turn, stop_observed: false}};
  const observations = [packet,
    {...packet, current_session: null},
    {...packet, current_session: {...packet.current_session, status: "closed"}},
    {...packet, pending_observation: {turn, stop_observed: true}},
    ...[null, {...turn, error_code: "policy"}, {...turn, turn_id: "foreign"},
      {...turn, status: "completed", response: {message: "Partial", proposals: [], gate: null,
        protected_action: null, unfinished_work: {remaining: "Retained work", dependency: "offline", effects_settled: true}}}]
      .map(value => ({...packet, pending_observation: {turn: value, stop_observed: false}}))];
  const before = structuredClone(observations);
  assert.deepEqual(planBoundConversationRequest({pending_observations: observations}),
    {pending_flags: observations.map(item => planBoundConversationRequest(item).pending)});
  assert.deepEqual(observations, before);
  assert.deepEqual(planBoundConversationRequest({pending_observations: []}), {pending_flags: []});
  for (const value of [null, {}, Array(65).fill(packet), [packet, {...packet, request: {}}],
    [packet, {request, current_session: packet.current_session}],
    [packet, {...packet, context_recovery: {}}], [{pending_observations: []}]]) {
    assert.throws(() => planBoundConversationRequest({pending_observations: value}));
  }
  assert.throws(() => planBoundConversationRequest({pending_observations: [packet], request}));
});

test("quota backlog projection retires explicit lifecycle stops, not unavailable recovery", () => {
  const original = {request_ref: "f".repeat(24), command: null, status: "accepted",
    session_id: "original", turn_id: "failed"};
  const turn = {session_id: "original", turn_id: "failed", status: "failed", error_code: "usage_limit_exceeded"};
  const packet = {request: original, current_session: {session_id: "original", status: "ready"},
    pending_observation: {turn, stop_observed: false}};
  assert.equal(planBoundConversationRequest(packet).pending, true);
  // No binding/provider/native allowance input is necessary for this read.
  assert.equal(planBoundConversationRequest({...packet, request: {...original,
    context_recovery: {quota_attempt: 3, not_before: "2099-01-01T00:00:00Z"}}}).pending, true);
  assert.equal(planBoundConversationRequest({...packet, current_session: null}).pending, true,
    "missing session evidence must not silently retire known quota work");
  assert.equal(planBoundConversationRequest({...packet,
    current_session: {session_id: "foreign", status: "closed"}}).pending, true);
  assert.equal(planBoundConversationRequest({...packet,
    current_session: {...packet.current_session, status: "closed"}}).pending, false);
  assert.equal(planBoundConversationRequest({...packet,
    pending_observation: {...packet.pending_observation, stop_observed: true}}).pending, false);
  for (const changed of [null, {...turn, error_code: "policy"}, {...turn, status: "completed"},
    {...turn, session_id: "foreign"}, {...turn, turn_id: "foreign"}]) {
    assert.equal(planBoundConversationRequest({...packet,
      pending_observation: {turn: changed, stop_observed: false}}).pending, false);
  }
  for (const changed of [{...original, command: "stop"}, {...original, status: "rejected"}]) {
    assert.equal(planBoundConversationRequest({...packet, request: changed}).pending, false);
  }
});

for (const errorCode of ["response_timeout", "upstream_transport_failure", "context_window_exceeded",
  "antigravity_rpc_error", "antigravity_transport_error", "antigravity_step_error", "runtime_error", "server_restarted"]) {
test(`delivered ${errorCode} notices retain the exact failed request, including other executors`, () => {
  const request = {request_ref: "f".repeat(24), command: null, status: "accepted",
    session_id: "original", turn_id: "timed-out", delivery_verified: true};
  const turn = {session_id: "original", turn_id: "timed-out", status: "failed", error_code: errorCode};
  const packet = {request, current_session: {session_id: "original", status: "ready", agent_id: "antigravity"},
    pending_observation: {turn, stop_observed: false}};
  assert.deepEqual(planBoundConversationRequest(packet), {pending: true});
  assert.equal(planBoundConversationRequest({...packet, current_session: null}).pending, true);
  assert.equal(planBoundConversationRequest({...packet,
    pending_observation: {turn, stop_observed: true}}).pending, false);
  assert.equal(planBoundConversationRequest({...packet,
    current_session: {...packet.current_session, status: "closed"}}).pending, false);
  for (const changed of [{...turn, session_id: "foreign"}, {...turn, turn_id: "foreign"},
    {...turn, status: "completed"}, {...turn, error_code: "policy"}]) {
    assert.equal(planBoundConversationRequest({...packet,
      pending_observation: {turn: changed, stop_observed: false}}).pending, false);
  }
  assert.equal(planBoundConversationRequest({...packet, request: {...request, command: "stop"}}).pending, false);
});
}

test("delivered partial results remain pending without authorizing another execution", () => {
  const original = {request_ref: "f".repeat(24), command: null, status: "accepted",
    session_id: "original", turn_id: "partial"};
  const work = {remaining: "Finish the retained result", dependency: "Temporary source outage", effects_settled: true};
  const response = {message: "Partial result", proposals: [], gate: null, protected_action: null, unfinished_work: work};
  const turn = {session_id: "original", turn_id: "partial", status: "completed", response};
  const packet = {request: original, current_session: {session_id: "original", status: "ready"},
    pending_observation: {turn, stop_observed: false}};
  assert.deepEqual(planBoundConversationRequest(packet), {pending: true});
  for (const unfinished_work of [null, {...work, effects_settled: false}, {...work, remaining: ""}, {...work, extra: true}]) {
    assert.equal(planBoundConversationRequest({...packet, pending_observation: {turn: {...turn,
      response: {...response, unfinished_work}}, stop_observed: false}}).pending, false);
  }
  for (const changed of [{...turn, response: {...response, gate: {kind: "approval"}}},
    {...turn, response: {...response, protected_action: {kind: "publish"}}},
    {...turn, response: {...response, proposals: [{kind: "new-work"}]}},
    {...turn, session_id: "foreign"}, {...turn, turn_id: "foreign"}]) {
    assert.equal(planBoundConversationRequest({...packet,
      pending_observation: {turn: changed, stop_observed: false}}).pending, false);
  }
  assert.equal(planBoundConversationRequest({...packet, pending_observation: {turn, stop_observed: true}}).pending, false);
  assert.equal(planBoundConversationRequest({...packet,
    current_session: {...packet.current_session, status: "closed"}}).pending, false);

  const recovery = {quota_attempt: 1, failed_turn_id: "quota-failure", client_turn_id: "original-quota-continuation"};
  const gated = {...turn, client_turn_id: recovery.client_turn_id,
    response: {...response, unfinished_work: null, gate: {kind: "authorization", summary: "Verification remains blocked"}}};
  const waiting = {...packet, request: {...original, context_recovery: recovery},
    pending_observation: {turn: gated, stop_observed: false}};
  assert.deepEqual(planBoundConversationRequest(waiting), {pending: true});
  for (const context_recovery of [null, {...recovery, quota_attempt: 0}, {...recovery, quota_attempt: 4},
    {...recovery, failed_turn_id: "partial"}, {...recovery, client_turn_id: "unrelated"}]) {
    assert.equal(planBoundConversationRequest({...waiting,
      request: {...original, context_recovery}}).pending, false);
  }
  assert.equal(planBoundConversationRequest({...waiting, pending_observation: {turn: {...gated,
    response: {...gated.response, gate: null}}, stop_observed: false}}).pending, false);
  assert.equal(planBoundConversationRequest({...waiting,
    pending_observation: {turn: gated, stop_observed: true}}).pending, false);
});


test("a steward quota endpoint change preserves its exact bound audience", () => {
  const selected = {...row, context_kind: "steward", grant: "portfolio_read", goal_ids: [], executor_endpoint_id: "antigravity"};
  const configured = planConversationBinding({...request, binding: selected}).state;
  const source = {source_ref: "e".repeat(24), sender_ref: row.operator_ref, private_human_message: true};
  const packet = {current: configured, binding_id: row.binding_id, observation, available_projects: [project], ...source};
  const context = resolveBoundConversation(packet).context;
  const allocation = {schema_version: "manager_executor_allocation_v0", selection_policy: "flexible",
    configured_endpoint: "antigravity", executor_endpoint: "codex", configuration_revision: "revision-1",
    allocation_reason: "flexible_availability_fallback", eligible_endpoints: ["antigravity", "codex"]};
  const session = {session_id: "original", goal_id: "loopx-manager", agent_id: "codex", executor_endpoint_id: "codex",
    session_mode: "managed_runtime", status: "ready", upstream_thread_id: "codex-native",
    channel_id: `manager.external.native.${row.binding_id}.${source.source_ref}`, steward_context: context,
    manager_executor_allocation: allocation, upstream_context_history: [{agent_id: "antigravity", thread_id: "original-native"}]};
  assert.deepEqual(resolveBoundConversation({...packet, session, session_context: context}).context, context);
  for (const changed of [{upstream_context_history: []}, {agent_id: "other"}, {status: "closed"},
    {manager_executor_allocation: {...allocation, eligible_endpoints: ["codex"]}},
    {manager_executor_allocation: {...allocation, selection_policy: "pinned"}},
    {steward_context: {...context as object, source_ref: "f".repeat(24)}}]) {
    assert.throws(() => resolveBoundConversation({...packet, session: {...session, ...changed}, session_context: context}));
  }
  assert.throws(() => resolveBoundConversation({...packet, sender_ref: "f".repeat(24), session, session_context: context}));
});
