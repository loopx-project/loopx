import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { FileAuthorityStore } from "../../loopx/control_plane/coordination/file_authority_store.ts";
import type { AuthorityStoreCommit } from "../../loopx/control_plane/coordination/authority_store.ts";
import {
  AuthorityStoreProtocolError,
  canonicalAuthorityBytes,
  canonicalAuthorityObject,
  canonicalAuthoritySha256,
} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import { normalizeTodoAgent } from "../../loopx/control_plane/coordination/todo_agents.ts";
import {
  TODO_DOMAIN_ITEM_SCHEMA,
  TODO_DOMAIN_READ_RECORD_SCHEMA,
  TODO_DOMAIN_RECORD_CONTRACT,
} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {
  TODO_CANONICAL_READ_RECORD_FIELDS,
  TODO_CANONICAL_READ_RECORD_SCHEMA,
  prepareCoordinationProjectionCommit,
  type CoordinationProjectionMutation,
} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {
  LOCAL_COORDINATION_TODO_ARCHIVE_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA,
  LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA,
  archiveLocalCoordinationTodos,
  listLocalCoordinationTodos,
  claimLocalCoordinationTodo,
  promoteLocalCoordinationAuthority,
  reviewLocalCoordinationAuthorityPromotion,
  readLocalCoordinationTodo,
  terminalLifecycleLocalCoordinationTodo,
} from "../../loopx/control_plane/coordination/local_authority_runtime.ts";
import {
  COORDINATION_TODO_CLAIM_RESULT_SCHEMA,
  evaluateCoordinationTodoClaimDecision,
} from "../../loopx/control_plane/coordination/todo_claim.ts";
import {computeContinuationTodoFacts} from "../../loopx/control_plane/coordination/continuation_note.ts";
import {
  checkLegacyCoordinationWriteAllowed,
  engageLegacyCoordinationWriterFence,
  loadLegacyCoordinationWriterFence,
  LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
  LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA,
} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {
  bootstrapCoordinationRuntimeShadow,
  COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA,
} from "../../loopx/control_plane/coordination/runtime_shadow.ts";
import { qualifiedShadow, promotionRequest, engageFence } from "./local_promotion_fixture.ts";
import { sourceRequest } from "./shadow_file_fixture.ts";
import { LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA } from "../../loopx/control_plane/coordination/coordination_state_contract.generated.ts";
import { executeTaskLeaseAcquire } from "../../loopx/control_plane/work_items/task_lease_acquire.ts";
import {
  TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION,
  executeTaskLeaseLifecycle,
} from "../../loopx/control_plane/work_items/task_lease_lifecycle.ts";

function sha256(value: unknown): string {
  return createHash("sha256").update(canonicalAuthorityBytes(value)).digest("hex");
}

function withTodoReadModel<T extends Record<string, unknown>>(projection: T): T {
  const todos = projection.todos as Record<string, unknown>[];
  return {
    ...projection,
    todo_read_model: {
      schema_version: TODO_CANONICAL_READ_RECORD_SCHEMA,
      todo_count: todos.length,
      records_sha256: sha256(todos),
      contract_fields: [...TODO_CANONICAL_READ_RECORD_FIELDS],
    },
  };
}

function todoRecord(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: "todo_item_v0",
    todo_id: "todo_a",
    role: "agent",
    status: "open",
    done: false,
    text: "Qualify canonical Todo semantics",
    archive_state: "active",
    source_section: "Agent Todo",
    ...overrides,
  };
}

async function applyTestProjectionMutation(
  store: FileAuthorityStore,
  operationId: string,
  expectedProviderRevision: string,
  mutations: readonly CoordinationProjectionMutation[],
) {
  const current = await store.loadAuthority();
  assert.equal(current.status, "loaded");
  if (current.status !== "loaded") throw new Error("canonical test fixture is missing");
  return store.commitAuthority(prepareCoordinationProjectionCommit({
    goal_id: "goal-a",
    operation_id: operationId,
    expected_provider_revision: expectedProviderRevision,
    projection: current.head,
    mutations,
  }));
}

function noteFacts(note: unknown): string {
  const parsed = canonicalAuthorityObject(JSON.parse(String(note)), "continuation note");
  return canonicalAuthoritySha256(parsed);
}

async function claimSeededTodo(
  root: string,
  todo: Record<string, unknown>,
  operationId: string,
) {
  const store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
  await store.commitAuthority({
    expected_provider_revision: null,
    operation_id: "seed",
    events: [],
    next_projection: withTodoReadModel({
      goal_id: "goal-a",
      handoff_mode: "soft_claim",
      todos: [todo],
      leases: [],
    }),
    receipts: [],
  });
  const result = await claimLocalCoordinationTodo({
    schema_version: LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
    todo_id: "todo_a",
    role: "agent",
    claimed_by: "agent-a",
    actor_agent_id: "agent-a",
    registered_agents: ["agent-a", "agent-b"],
    operation_id: operationId,
    observed_at: "2026-09-05T04:30:00Z",
    dry_run: false,
  });
  return { result, receipt: await store.readReceipt(operationId) };
}

test("legacy write guard flips from allowed to fail-closed after the durable fence", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-legacy-writer-fence-"));
  const shadow = await qualifiedShadow(root);
  const request = promotionRequest(root, shadow.projection, shadow.providerRevision);
  const check = {
    schema_version: LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
  };
  assert.equal((await checkLegacyCoordinationWriteAllowed(check)).status, "allowed");
  await engageFence(request);
  const replayed = await engageLegacyCoordinationWriterFence({
    schema_version: LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
    runtime_root: request.runtime_root,
    goal_id: request.goal_id,
    state_path: join(request.runtime_root, "ACTIVE_GOAL_STATE.md"),
    fence: request.writer_fence,
  });
  assert.equal(replayed.status, "replayed");
  const conflict = await engageLegacyCoordinationWriterFence({
    schema_version: LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
    runtime_root: request.runtime_root,
    goal_id: request.goal_id,
    state_path: join(request.runtime_root, "ACTIVE_GOAL_STATE.md"),
    fence: { ...request.writer_fence, fence_id: "legacy-writer-fence:other" },
  });
  assert.equal(conflict.status, "conflict");
  const blocked = await checkLegacyCoordinationWriteAllowed(check);
  assert.equal(blocked.status, "blocked");
  assert.equal(blocked.reason_code, "legacy_coordination_writer_fenced");
  assert.equal(blocked.authority_mode, "file_v0");
});

test("new file outbox qualification does not implicitly enable canonical promotion", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-local-authority-promote-"));
  const shadow = await qualifiedShadow(root);
  const request = promotionRequest(root, shadow.projection, shadow.providerRevision);
  await engageFence(request);
  const applied = await promoteLocalCoordinationAuthority(request);
  assert.equal(applied.status, "failed");
  assert.equal(applied.reason_code, "local_authority_shadow_not_qualified");
  const canonical = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a", {existingOnly: true});
  assert.equal((await canonical.loadAuthority()).status, "missing");
});

test("local promotion fences shadow revision, digest, and writer-fence identity", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-local-authority-promote-fence-"));
  const shadow = await qualifiedShadow(root);
  const request = promotionRequest(root, shadow.projection, shadow.providerRevision);

  const missingFence = await promoteLocalCoordinationAuthority(request);
  assert.equal(missingFence.status, "failed");
  assert.equal(missingFence.reason_code, "local_authority_writer_fence_not_verified");
  await engageFence(request);

  const staleRevision = await promoteLocalCoordinationAuthority({
    ...request,
    expected_shadow_provider_revision: "file:stale",
  });
  assert.equal(staleRevision.status, "failed");
  assert.equal(staleRevision.reason_code, "local_authority_writer_fence_revision_mismatch");

  const mismatchedFence = await promoteLocalCoordinationAuthority({
    ...request,
    writer_fence: { ...request.writer_fence, source_projection_sha256: "0".repeat(64) },
  });
  assert.equal(mismatchedFence.status, "failed");
  assert.equal(
    mismatchedFence.reason_code,
    "local_authority_writer_fence_projection_mismatch",
  );

  const unqualified = await promoteLocalCoordinationAuthority({
    ...request,
    minimum_operations: 2,
  });
  assert.equal(unqualified.status, "failed");
  assert.equal(unqualified.reason_code, "local_authority_shadow_not_qualified");
  const canonical = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
  assert.equal((await canonical.loadAuthority()).status, "missing");
});

test("reviewed promotion previews without effects and atomically applies the whole Goal", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-reviewed-promotion-"));
  const shadow = await qualifiedShadow(root, "hard_lease");
  const sourceProjection = { ...shadow.projection };
  delete sourceProjection.capture_lineage_id;
  delete sourceProjection.capture_profile;
  delete sourceProjection.source_root_digest;
  sourceProjection.partitions = { todos: null, leases: null };
  const statePath = join(root, "ACTIVE_GOAL_STATE.md");
  const source = await sourceRequest({
    root,
    statePath,
    store: new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a"),
    baseline: sourceProjection,
  }, sourceProjection);
  const request = {
    ...source,
    schema_version: LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA,
    operation_id: "promote:goal-a:reviewed",
    minimum_operations: 1,
    required_event_kinds: ["todo_claim"],
    execute: false,
  };

  const preview = await reviewLocalCoordinationAuthorityPromotion(request);
  assert.equal(preview.status, "preview_ready", JSON.stringify(preview));
  assert.equal(preview.promotion_ready, true);
  assert.equal((await loadLegacyCoordinationWriterFence(root, "goal-a")).status, "missing");
  const canonical = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a", { existingOnly: true });
  assert.equal((await canonical.loadAuthority()).status, "missing");

  const applied = await reviewLocalCoordinationAuthorityPromotion({ ...request, execute: true });
  assert.equal(applied.status, "applied", JSON.stringify(applied));
  assert.equal(applied.legacy_writer_fenced, true);
  assert.equal((await loadLegacyCoordinationWriterFence(root, "goal-a")).status, "loaded");
  const head = await canonical.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status === "loaded") {
    assert.equal(canonicalAuthoritySha256(head.head), canonicalAuthoritySha256(shadow.projection));
  }
  const replayed = await reviewLocalCoordinationAuthorityPromotion({ ...request, execute: true });
  assert.equal(replayed.status, "replayed", JSON.stringify(replayed));
});

test("reviewed promotion resumes the exact request after a fence-to-canonical interruption", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-reviewed-promotion-recovery-"));
  const shadow = await qualifiedShadow(root, "hard_lease");
  const sourceProjection = { ...shadow.projection };
  delete sourceProjection.capture_lineage_id;
  delete sourceProjection.capture_profile;
  delete sourceProjection.source_root_digest;
  sourceProjection.partitions = { todos: null, leases: null };
  const statePath = join(root, "ACTIVE_GOAL_STATE.md");
  const source = await sourceRequest({
    root,
    statePath,
    store: new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a"),
    baseline: sourceProjection,
  }, sourceProjection);
  const request = {
    ...source,
    schema_version: LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA,
    operation_id: "promote:goal-a:recoverable",
    minimum_operations: 1,
    required_event_kinds: ["todo_claim"],
    execute: true,
  };
  const legacyBytes = await readFile(statePath);
  class InterruptOnceStore extends FileAuthorityStore {
    private interrupt = true;
    override async commitAuthority(commit: AuthorityStoreCommit) {
      if (this.interrupt) {
        this.interrupt = false;
        throw new Error("synthetic interruption after durable fence");
      }
      return await super.commitAuthority(commit);
    }
  }
  const canonical = new InterruptOnceStore(join(root, "authority", "file-v0"), "goal-a");
  const dependencies = {createCanonicalStore: () => canonical};

  const interrupted = await reviewLocalCoordinationAuthorityPromotion(request, dependencies);
  assert.equal(interrupted.status, "failed", JSON.stringify(interrupted));
  assert.equal(interrupted.legacy_writer_fenced, true);
  assert.equal((await canonical.loadAuthority()).status, "missing");
  assert.deepEqual(await readFile(statePath), legacyBytes);

  const changed = await reviewLocalCoordinationAuthorityPromotion({
    ...request,
    operation_id: "promote:goal-a:different",
  }, dependencies);
  assert.equal(changed.status, "failed", JSON.stringify(changed));
  assert.equal(changed.reason_code, "local_authority_writer_fence_conflict");
  assert.equal(changed.legacy_writer_fenced, true);
  assert.equal((await canonical.loadAuthority()).status, "missing");

  const recovered = await reviewLocalCoordinationAuthorityPromotion(request, dependencies);
  assert.equal(recovered.status, "recovered", JSON.stringify(recovered));
  assert.equal(recovered.legacy_writer_fenced, true);
  const head = await canonical.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status === "loaded") {
    assert.equal(canonicalAuthoritySha256(head.head), canonicalAuthoritySha256(shadow.projection));
  }
  assert.deepEqual(await readFile(statePath), legacyBytes);
});

test("reviewed promotion rejects a non-hard-lease Goal without fencing writers", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-reviewed-promotion-mode-"));
  const shadow = await qualifiedShadow(root, "soft_claim");
  const sourceProjection: JsonObject = {
    ...shadow.projection,
    partitions: { todos: null, leases: null },
  };
  delete sourceProjection.capture_lineage_id;
  delete sourceProjection.capture_profile;
  delete sourceProjection.source_root_digest;
  const statePath = join(root, "ACTIVE_GOAL_STATE.md");
  const source = await sourceRequest({
    root,
    statePath,
    store: new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a"),
    baseline: sourceProjection,
  }, sourceProjection);
  const result = await reviewLocalCoordinationAuthorityPromotion({
    ...source,
    schema_version: LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA,
    operation_id: "promote:goal-a:wrong-mode",
    minimum_operations: 1,
    required_event_kinds: ["todo_claim"],
    execute: true,
  });
  assert.equal(result.status, "not_ready", JSON.stringify(result));
  assert.equal(result.reason_code, "local_authority_promotion_requires_hard_lease");
  assert.equal((await loadLegacyCoordinationWriterFence(root, "goal-a")).status, "missing");
});

test("new bootstrap and provider list fail closed without exact Todo consumer semantics", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-local-authority-semantic-fence-"));
  const incomplete = { goal_id: "goal-a", todos: [{ todo_id: "todo_a", role: "agent", status: "open" }], leases: [] };
  const bootstrapped = await bootstrapCoordinationRuntimeShadow({
    schema_version: COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA,
    runtime_root: root, goal_id: "goal-a", operation_id: "bootstrap:goal-a:incomplete",
    source_version: "state:0", projection: incomplete,
  });
  assert.equal(bootstrapped.status, "failed");
  assert.equal((await new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a", {existingOnly: true}).loadAuthority()).status, "missing");

  const canonical = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
  const committed = await canonical.commitAuthority({
    expected_provider_revision: null,
    operation_id: "unsafe:test-only",
    events: [{ schema_version: "test_v0" }],
    next_projection: incomplete,
    receipts: [],
  });
  assert.equal(committed.status, "applied");
  const listed = await listLocalCoordinationTodos({
    schema_version: LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
  });
  assert.equal(listed.status, "failed");
  assert.equal(listed.reason_code, "invalid_local_coordination_todo_list_request");
});

test("provider-first Todo claim preserves the complete record and is replay-safe", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-local-authority-claim-"));
  const store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
  const richTodo = todoRecord({
    priority: "P0",
    action_kind: "implement",
    required_capabilities: ["network"],
    excluded_agents: ["agent-b"],
    note: "preserve this field",
  });
  const initial = await store.commitAuthority({
    expected_provider_revision: null,
    operation_id: "promote:claim-test",
    events: [{ schema_version: "promotion_v0" }],
    next_projection: withTodoReadModel({
      goal_id: "goal-a",
      handoff_mode: "soft_claim",
      todos: [richTodo],
      leases: [],
    }),
    receipts: [],
  });
  assert.equal(initial.status, "applied");

  const request = {
    schema_version: LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
    todo_id: "todo_a",
    role: "agent",
    claimed_by: " Agent-A ",
    actor_agent_id: "AGENT-A",
    registered_agents: ["agent-a", "agent-b"],
    operation_id: "todo-claim:goal-a:todo_a:one",
    observed_at: "2026-09-05T04:30:00Z",
    dry_run: false,
  };
  const applied = await claimLocalCoordinationTodo(request);
  assert.equal(applied.status, "applied", JSON.stringify(applied));
  assert.equal(applied.changed, true);
  assert.equal(applied.source_authority, "file_v0");
  assert.equal(
    (applied.mutation_authority as Record<string, unknown>).mode,
    "registered_peer_actor",
  );

  const read = await readLocalCoordinationTodo({
    schema_version: LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
    todo_id: "todo_a",
  });
  assert.equal(read.status, "found");
  assert.deepEqual(read.todo, {
    ...richTodo,
    claimed_by: "agent-a",
    updated_at: (read.todo as Record<string, unknown>).updated_at,
  });

  const replayed = await claimLocalCoordinationTodo(request);
  assert.equal(replayed.status, "replayed");

  const repeated = await claimLocalCoordinationTodo({
    ...request,
    operation_id: "todo-claim:goal-a:todo_a:two",
  });
  assert.equal(repeated.status, "no_change");
  assert.equal(repeated.changed, false);
});

test("agent id normalization folds any whitespace run like the Python kernel", async () => {
  // Parity with loopx/control_plane/todos/contract.py normalize_todo_claimed_by:
  // compact_todo_text collapses every Python-recognized whitespace run (including
  // U+0085 NEL, U+001C..U+001F information separators, tabs, and NBSP) into
  // one space before mapping it to "-", so the same claim command keeps working
  // before and after promotion.
  const whitespaceVariants = [
    "Agent A",
    "Agent\tA",
    "Agent\u0085A",
    "Agent\u001cA",
    "Agent\u001dA",
    "Agent\u001eA",
    "Agent\u001fA",
    "Agent\u00a0A",
    "\u0085 Agent \t A \u001c ",
  ];
  for (const variant of whitespaceVariants) {
    assert.equal(normalizeTodoAgent(variant, "claimed_by"), "agent-a");
  }
  // BOM (U+FEFF) is not Python whitespace and must not be accepted
  assert.throws(
    () => normalizeTodoAgent("\ufeffAgent A", "claimed_by"),
    AuthorityStoreProtocolError,
  );

  const root = await mkdtemp(join(tmpdir(), "loopx-local-authority-claim-tab-"));
  const store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
  const seeded = await store.commitAuthority({
    expected_provider_revision: null,
    operation_id: "promote:claim-tab-test",
    events: [{ schema_version: "promotion_v0" }],
    next_projection: withTodoReadModel({
      goal_id: "goal-a",
      handoff_mode: "soft_claim",
      todos: [todoRecord()],
      leases: [],
    }),
    receipts: [],
  });
  assert.equal(seeded.status, "applied");

  const applied = await claimLocalCoordinationTodo({
    schema_version: LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
    todo_id: "todo_a",
    role: "agent",
    claimed_by: "Agent\u0085A",
    actor_agent_id: "\u0085 Agent \t A \u001c ",
    registered_agents: ["agent-a"],
    operation_id: "todo-claim:goal-a:todo_a:tab",
    observed_at: "2026-09-05T04:30:00Z",
    dry_run: false,
  });
  assert.equal(applied.status, "applied", JSON.stringify(applied));

  const read = await readLocalCoordinationTodo({
    schema_version: LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
    todo_id: "todo_a",
  });
  assert.equal(read.status, "found");
  assert.equal((read.todo as Record<string, unknown>).claimed_by, "agent-a");
});

test("one TypeScript decision owns promoted and legacy Todo claims", () => {
  const input = {
    goal_id: "goal-a",
    todo_id: "todo_a",
    claimed_by: "agent-a",
    actor_agent_id: "agent-a",
    expected_role: "agent",
    registered_agents: ["agent-a", "agent-b"],
    operation_id: "decision-only",
    dry_run: true,
    now: new Date(0),
  };
  const accepted = evaluateCoordinationTodoClaimDecision(todoRecord(), input);
  assert.equal(accepted.status, "accepted");
  assert.equal(
    (accepted.mutation_authority as Record<string, unknown>).mode,
    "registered_peer_actor",
  );

  const rejected = evaluateCoordinationTodoClaimDecision(
    todoRecord({ excluded_agents: [" Agent-A "] }),
    input,
  );
  assert.equal(rejected.status, "rejected");
  assert.equal(rejected.reason_code, "actor_excluded");

  assert.throws(
    () => evaluateCoordinationTodoClaimDecision(
      todoRecord({ excluded_agents: ["agent-a", " Agent-A "] }),
      input,
    ),
    /unique public-safe agent ids/,
  );
});

test("the shared claim decision rejects every pre-commit lifecycle boundary", () => {
  const base: Parameters<typeof evaluateCoordinationTodoClaimDecision>[1] = {
    goal_id: "goal-a",
    todo_id: "todo_a",
    claimed_by: "agent-a",
    actor_agent_id: "agent-a",
    expected_role: "agent",
    registered_agents: ["agent-a", "agent-b"],
    operation_id: "decision-only",
    dry_run: true,
    now: new Date(0),
  };
  const cases: Array<[Record<string, unknown>, Partial<typeof base>, string]> = [
    [{ status: "done" }, {}, "todo_not_open"],
    [{ archive_state: "archive" }, {}, "todo_archived"],
    [{ role: "user" }, { expected_role: "user" }, "todo_not_agent"],
    [{ removed_continuation_policy: "author_reviewer_handoff" }, {},
      "removed_continuation_policy"],
    [{}, { actor_agent_id: "agent-b" }, "claim_actor_mismatch"],
    [{}, { actor_agent_id: null }, "actor_required"],
  ];
  for (const [todoOverrides, inputOverrides, code] of cases) {
    const result = evaluateCoordinationTodoClaimDecision(
      todoRecord(todoOverrides),
      { ...base, ...inputOverrides },
    );
    assert.equal(result.status, "rejected", code);
    assert.equal(result.reason_code, code);
  }
});

test("cross-agent claim without transfer grant is rejected (default behavior preserved)", () => {
  const result = evaluateCoordinationTodoClaimDecision(
    todoRecord({ claimed_by: "agent-b" }),
    {
      goal_id: "goal-a",
      todo_id: "todo_a",
      claimed_by: "agent-a",
      actor_agent_id: "agent-a",
      expected_role: "agent",
      registered_agents: ["agent-a", "agent-b"],
      operation_id: "handoff",
      dry_run: true,
      now: new Date(0),
    },
  );
  assert.equal(result.status, "rejected");
  assert.equal(result.reason_code, "claim_owner_mismatch");
});

test("cross-agent claim with valid transfer grant is accepted (handoff boundary)", () => {
  // The note must carry the correct loopx-explicit-continuation marker and
  // todo_facts matching the current Todo. The shared validateContinuationNote
  // enforces this invariant in the final claim decision.
  const todo = todoRecord({ claimed_by: "agent-b" });
  const note = JSON.stringify({kind: "loopx-explicit-continuation", source_session: "s1",
    rationale: "handoff", source_refs: ["artifact:decision.md"],
    todo_facts: computeContinuationTodoFacts(todo)});
  const result = evaluateCoordinationTodoClaimDecision(
    todoRecord({ claimed_by: "agent-b", note }),
    {
      goal_id: "goal-a",
      todo_id: "todo_a",
      claimed_by: "agent-a",
      actor_agent_id: "agent-a",
      expected_role: "agent",
      registered_agents: ["agent-a", "agent-b"],
      operation_id: "handoff",
      expected_provider_revision: "rev-123",
      transfer_grant: {
        schema_version: "todo_transfer_grant_v0",
        source_agent_id: "agent-b",
        target_agent_id: "agent-a",
        todo_id: "todo_a",
        expected_revision: "rev-123",
        continuation_note_facts: noteFacts(note),
      },
      dry_run: true,
      now: new Date(0),
    },
  );
  assert.equal(result.status, "accepted");
});

test("transfer grant with mismatched continuation_note_facts is rejected", () => {
  const todo = todoRecord({ claimed_by: "agent-b" });
  const note = JSON.stringify({kind: "loopx-explicit-continuation", source_session: "s1",
    rationale: "handoff", source_refs: ["artifact:decision.md"],
    todo_facts: computeContinuationTodoFacts(todo)});
  const result = evaluateCoordinationTodoClaimDecision(
    todoRecord({ claimed_by: "agent-b", note }),
    {
      goal_id: "goal-a",
      todo_id: "todo_a",
      claimed_by: "agent-a",
      actor_agent_id: "agent-a",
      expected_role: "agent",
      registered_agents: ["agent-a", "agent-b"],
      operation_id: "handoff",
      expected_provider_revision: "rev-123",
      transfer_grant: {
        schema_version: "todo_transfer_grant_v0",
        source_agent_id: "agent-b",
        target_agent_id: "agent-a",
        todo_id: "todo_a",
        expected_revision: "rev-123",
        continuation_note_facts: "tampered-facts-hash",
      },
      dry_run: true,
      now: new Date(0),
    },
  );
  assert.equal(result.status, "rejected");
  assert.equal(result.reason_code, "claim_owner_mismatch");
});

test("transfer grant with wrong source agent is rejected", () => {
  const todo = todoRecord({ claimed_by: "agent-b" });
  const note = JSON.stringify({kind: "loopx-explicit-continuation", source_session: "s1",
    rationale: "handoff", source_refs: ["artifact:decision.md"],
    todo_facts: computeContinuationTodoFacts(todo)});
  const result = evaluateCoordinationTodoClaimDecision(
    todoRecord({ claimed_by: "agent-b", note }),
    {
      goal_id: "goal-a",
      todo_id: "todo_a",
      claimed_by: "agent-a",
      actor_agent_id: "agent-a",
      expected_role: "agent",
      registered_agents: ["agent-a", "agent-b"],
      operation_id: "handoff",
      expected_provider_revision: "rev-123",
      transfer_grant: {
        schema_version: "todo_transfer_grant_v0",
        source_agent_id: "agent-c",
        target_agent_id: "agent-a",
        todo_id: "todo_a",
        expected_revision: "rev-123",
        continuation_note_facts: noteFacts(note),
      },
      dry_run: true,
      now: new Date(0),
    },
  );
  assert.equal(result.status, "rejected");
  assert.equal(result.reason_code, "claim_owner_mismatch");
});

test("transfer grant with wrong target agent is rejected", () => {
  const todo = todoRecord({ claimed_by: "agent-b" });
  const note = JSON.stringify({kind: "loopx-explicit-continuation", source_session: "s1",
    rationale: "handoff", source_refs: ["artifact:decision.md"],
    todo_facts: computeContinuationTodoFacts(todo)});
  const result = evaluateCoordinationTodoClaimDecision(
    todoRecord({ claimed_by: "agent-b", note }),
    {
      goal_id: "goal-a",
      todo_id: "todo_a",
      claimed_by: "agent-a",
      actor_agent_id: "agent-a",
      expected_role: "agent",
      registered_agents: ["agent-a", "agent-b"],
      operation_id: "handoff",
      expected_provider_revision: "rev-123",
      transfer_grant: {
        schema_version: "todo_transfer_grant_v0",
        source_agent_id: "agent-b",
        target_agent_id: "agent-c",
        todo_id: "todo_a",
        expected_revision: "rev-123",
        continuation_note_facts: noteFacts(note),
      },
      dry_run: true,
      now: new Date(0),
    },
  );
  assert.equal(result.status, "rejected");
  assert.equal(result.reason_code, "claim_owner_mismatch");
});

test("transfer grant with wrong todo_id is rejected", () => {
  const todo = todoRecord({ claimed_by: "agent-b" });
  const note = JSON.stringify({kind: "loopx-explicit-continuation", source_session: "s1",
    rationale: "handoff", source_refs: ["artifact:decision.md"],
    todo_facts: computeContinuationTodoFacts(todo)});
  const result = evaluateCoordinationTodoClaimDecision(
    todoRecord({ claimed_by: "agent-b", note }),
    {
      goal_id: "goal-a",
      todo_id: "todo_a",
      claimed_by: "agent-a",
      actor_agent_id: "agent-a",
      expected_role: "agent",
      registered_agents: ["agent-a", "agent-b"],
      operation_id: "handoff",
      expected_provider_revision: "rev-123",
      transfer_grant: {
        schema_version: "todo_transfer_grant_v0",
        source_agent_id: "agent-b",
        target_agent_id: "agent-a",
        todo_id: "todo_b",
        expected_revision: "rev-123",
        continuation_note_facts: noteFacts(note),
      },
      dry_run: true,
      now: new Date(0),
    },
  );
  assert.equal(result.status, "rejected");
  assert.equal(result.reason_code, "claim_owner_mismatch");
});

test("transfer grant with wrong expected_revision is rejected", () => {
  const todo = todoRecord({ claimed_by: "agent-b" });
  const note = JSON.stringify({kind: "loopx-explicit-continuation", source_session: "s1",
    rationale: "handoff", source_refs: ["artifact:decision.md"],
    todo_facts: computeContinuationTodoFacts(todo)});
  const result = evaluateCoordinationTodoClaimDecision(
    todoRecord({ claimed_by: "agent-b", note }),
    {
      goal_id: "goal-a",
      todo_id: "todo_a",
      claimed_by: "agent-a",
      actor_agent_id: "agent-a",
      expected_role: "agent",
      registered_agents: ["agent-a", "agent-b"],
      operation_id: "handoff",
      expected_provider_revision: "rev-123",
      transfer_grant: {
        schema_version: "todo_transfer_grant_v0",
        source_agent_id: "agent-b",
        target_agent_id: "agent-a",
        todo_id: "todo_a",
        expected_revision: "rev-456",
        continuation_note_facts: noteFacts(note),
      },
      dry_run: true,
      now: new Date(0),
    },
  );
  assert.equal(result.status, "rejected");
  assert.equal(result.reason_code, "claim_owner_mismatch");
});

test("transfer grant with wrong schema_version is rejected", () => {
  const todo = todoRecord({ claimed_by: "agent-b" });
  const note = JSON.stringify({kind: "loopx-explicit-continuation", source_session: "s1",
    rationale: "handoff", source_refs: ["artifact:decision.md"],
    todo_facts: computeContinuationTodoFacts(todo)});
  const result = evaluateCoordinationTodoClaimDecision(
    todoRecord({ claimed_by: "agent-b", note }),
    {
      goal_id: "goal-a",
      todo_id: "todo_a",
      claimed_by: "agent-a",
      actor_agent_id: "agent-a",
      expected_role: "agent",
      registered_agents: ["agent-a", "agent-b"],
      operation_id: "handoff",
      expected_provider_revision: "rev-123",
      transfer_grant: {
        schema_version: "todo_transfer_grant_v99",
        source_agent_id: "agent-b",
        target_agent_id: "agent-a",
        todo_id: "todo_a",
        expected_revision: "rev-123",
        continuation_note_facts: noteFacts(note),
      } as unknown as Parameters<typeof evaluateCoordinationTodoClaimDecision>[1]["transfer_grant"],
      dry_run: true,
      now: new Date(0),
    },
  );
  assert.equal(result.status, "rejected");
  assert.equal(result.reason_code, "claim_owner_mismatch");
});

test("promoted claim rejection preserves the public result envelope", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-claim-rejection-envelope-"));
  const { result: rejected, receipt } = await claimSeededTodo(
    root,
    todoRecord({ status: "done", done: true }),
    "claim-rejected",
  );

  assert.equal(
    rejected.schema_version,
    COORDINATION_TODO_CLAIM_RESULT_SCHEMA,
    JSON.stringify(rejected),
  );
  assert.equal(rejected.status, "failed");
  assert.equal(rejected.reason_code, "todo_not_open", JSON.stringify(rejected));
  assert.deepEqual(receipt, { status: "missing" });
});

test("promoted claim normalizes persisted excluded-agent identities", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-claim-excluded-normalize-"));
  const { result: rejected, receipt } = await claimSeededTodo(
    root,
    todoRecord({ excluded_agents: [" Agent-A "] }),
    "claim-excluded",
  );

  assert.equal(rejected.schema_version, COORDINATION_TODO_CLAIM_RESULT_SCHEMA);
  assert.equal(rejected.status, "failed");
  assert.equal(rejected.reason_code, "actor_excluded");
  assert.deepEqual(receipt, { status: "missing" });
});

for (const native of [false, true]) {
  test(`claim rejects malformed previews and replays historical intent (${native ? "native" : "v0"})`, async () => {
    const root = await mkdtemp(join(tmpdir(), "loopx-claim-intent-"));
    const store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
    const todo = todoRecord();
    if (native) {
      todo.schema_version = TODO_DOMAIN_ITEM_SCHEMA;
      delete todo.source_section;
    }
    const projection = withTodoReadModel({
      goal_id: "goal-a", handoff_mode: "hard_lease", todos: [todo],
      leases: [{todo_id: "todo_a", owner: "agent-a", status: "active",
        expires_at: "2026-09-05T05:00:00Z"}],
    });
    if (native) {
      Object.assign(projection, {todo_read_model: {
        schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA,
        todo_count: 1, records_sha256: sha256([todo]),
        contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields],
      }});
    }
    await store.commitAuthority({
      expected_provider_revision: null, operation_id: "seed", events: [],
      next_projection: projection, receipts: [],
    });
    const request = {
      schema_version: LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA,
      runtime_root: root, goal_id: "goal-a", todo_id: "todo_a", role: "agent",
      claimed_by: "agent-a", actor_agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"],
      operation_id: "claim-a", observed_at: "2026-09-05T04:30:00Z", dry_run: false,
    };
    const before = await store.loadAuthority();
    for (const dry_run of ["true", 1, null, undefined]) {
      const rejected = await claimLocalCoordinationTodo({...request, dry_run});
      assert.equal(rejected.reason_code, "invalid_local_coordination_todo_claim_request");
      assert.deepEqual(await store.loadAuthority(), before);
      assert.deepEqual(await store.readReceipt("claim-a"), {status: "missing"});
    }
    assert.equal((await claimLocalCoordinationTodo({...request, dry_run: true})).status, "planned");
    assert.deepEqual(await store.loadAuthority(), before);
    const applied = await claimLocalCoordinationTodo(request);
    assert.equal(applied.status, "applied", JSON.stringify(applied));
    const claimed = await store.loadAuthority();
    assert.equal(claimed.status, "loaded");
    if (claimed.status !== "loaded") return;
    const claimedTodo = (claimed.head.todos as Record<string, unknown>[])[0]!;
    if (native) {
      assert.equal(claimedTodo.source_section, undefined);
      assert.equal(claimedTodo.index, undefined);
    }
    // Operation B completes/archives/reassigns the Todo. A retry of A must
    // return A's receipt even after its actor registration and lease expire.
    const completed = await applyTestProjectionMutation(
      store,
      "complete-b",
      claimed.provider_revision,
      [{kind: "todo_upsert", todo: {...claimedTodo, status: "done", done: true,
        archive_state: "archive", claimed_by: "agent-b"}}],
    );
    assert.equal(completed.status, "applied");
    const afterB = await store.loadAuthority();
    for (const registered_agents of [["agent-b"], []]) {
      const replay = await claimLocalCoordinationTodo({...request,
        registered_agents, observed_at: "2026-09-06T04:30:00Z"});
      assert.equal(replay.status, "replayed", JSON.stringify(replay));
      assert.equal(replay.changed, false);
      assert.equal(replay.provider_revision, applied.provider_revision);
      assert.deepEqual(replay.original_receipt, applied.original_receipt);
      assert.equal(replay.updated_at, applied.updated_at);
    }
    for (const changedIntent of [{claimed_by: "agent-b"}, {actor_agent_id: "agent-b"},
      {todo_id: "todo_b"}, {role: "user"}, {dry_run: true}]) {
      const mismatch = await claimLocalCoordinationTodo({...request, ...changedIntent});
      assert.equal(mismatch.reason_code, "coordination_operation_identity_mismatch");
    }
    assert.deepEqual(await store.loadAuthority(), afterB);
    const fresh = await claimLocalCoordinationTodo({...request, operation_id: "claim-new"});
    assert.equal(fresh.reason_code, "todo_not_open");
  });

  test(`no-change claim is a durable terminal result (${native ? "native" : "v0"})`, async () => {
    for (const later of [{claimed_by: null}, {claimed_by: "agent-b"},
      {status: "done", done: true, archive_state: "archive"}]) {
      const root = await mkdtemp(join(tmpdir(), "loopx-claim-no-change-"));
      const store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
      const todo = todoRecord({claimed_by: "agent-a", note: "preserve original bytes"});
      if (native) {
        todo.schema_version = TODO_DOMAIN_ITEM_SCHEMA;
        delete todo.source_section;
      }
      const projection = withTodoReadModel({goal_id: "goal-a", handoff_mode: "soft_claim",
        todos: [todo], leases: []});
      if (native) {
        Object.assign(projection, {todo_read_model: {
          schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA, todo_count: 1,
          records_sha256: sha256([todo]), contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields],
        }});
      }
      await store.commitAuthority({expected_provider_revision: null, operation_id: "seed",
        events: [], next_projection: projection, receipts: []});
      const before = await store.loadAuthority();
      const request = {
        schema_version: LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA,
        runtime_root: root, goal_id: "goal-a", todo_id: "todo_a", role: "agent",
        claimed_by: "agent-a", actor_agent_id: "agent-a", registered_agents: ["agent-a"],
        operation_id: "claim-no-change", observed_at: "2026-09-05T04:30:00Z", dry_run: false,
      };
      const preview = await claimLocalCoordinationTodo({...request, dry_run: true});
      assert.equal(preview.status, "no_change");
      assert.equal(preview.changed, false);
      assert.deepEqual(await store.readReceipt(request.operation_id), {status: "missing"});
      const freshEmpty = await claimLocalCoordinationTodo({...request, registered_agents: []});
      assert.equal(freshEmpty.reason_code, "actor_not_registered");
      assert.deepEqual(await store.loadAuthority(), before);
      const accepted = await claimLocalCoordinationTodo(request);
      assert.equal(accepted.status, "no_change", JSON.stringify(accepted));
      assert.equal(accepted.changed, false);
      const receipt = await store.readReceipt(request.operation_id);
      assert.equal(receipt.status, "found");
      const afterA = await store.loadAuthority();
      assert.equal(afterA.status, "loaded");
      if (afterA.status !== "loaded" || before.status !== "loaded") return;
      assert.deepEqual(afterA.head, before.head);
      assert.notEqual(afterA.provider_revision, before.provider_revision);
      const scan = await store.scanCommitted(before.cursor, 10);
      assert.equal(scan.status, "page");
      if (scan.status !== "page") return;
      assert.equal(scan.transactions.length, 1);
      assert.deepEqual(scan.transactions[0]?.events, []);
      const changed = await applyTestProjectionMutation(
        store,
        "later-change",
        afterA.provider_revision,
        [{kind: "todo_upsert", todo: {...todo, ...later}}],
      );
      assert.equal(changed.status, "applied");
      const afterB = await store.loadAuthority();
      const replayed = await claimLocalCoordinationTodo({...request, registered_agents: [],
        observed_at: "2026-09-06T04:30:00Z"});
      assert.deepEqual(replayed, {...accepted, status: "replayed"});
      for (const changedIntent of [{claimed_by: "agent-b"}, {actor_agent_id: "agent-b"},
        {todo_id: "todo_b"}, {role: "user"}, {dry_run: true}]) {
        assert.equal((await claimLocalCoordinationTodo({...request, ...changedIntent})).reason_code,
          "coordination_operation_identity_mismatch");
      }
      for (const registered_agents of [null, "agent-a", [null], [42], ["invalid!"],
        ["agent-a", "agent-a"]]) {
        assert.equal((await claimLocalCoordinationTodo({...request, registered_agents})).status, "failed");
      }
      assert.deepEqual(await store.loadAuthority(), afterB);
      assert.deepEqual(await store.readReceipt(request.operation_id), receipt);
    }
  });
}

test("receipt-only claims require head CAS and recover a lost commit response", async () => {
  for (const fault of ["conflict", "lost_response"] as const) {
    const root = await mkdtemp(join(tmpdir(), "loopx-claim-no-change-fault-"));
    const directory = join(root, "authority", "file-v0");
    const store = new FileAuthorityStore(directory, "goal-a");
    const projection = withTodoReadModel({goal_id: "goal-a", todos: [
      todoRecord({claimed_by: "agent-a"}),
    ], leases: []});
    await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
      events: [], receipts: [], next_projection: projection});
    const before = await store.loadAuthority();
    assert.equal(before.status, "loaded");
    if (before.status !== "loaded") return;
    class FaultStore extends FileAuthorityStore {
      override async commitAuthority(commit: AuthorityStoreCommit) {
        if (fault === "conflict") {
          const concurrent = await store.commitAuthority({...commit, operation_id: "concurrent",
            receipts: [], next_projection: withTodoReadModel({...projection,
              todos: [todoRecord({claimed_by: "agent-b"})]})});
          assert.equal(concurrent.status, "applied");
          return super.commitAuthority(commit);
        }
        assert.equal((await super.commitAuthority(commit)).status, "applied");
        return {status: "ambiguous" as const, reason_code: "lost_response",
          reason: "commit response lost after persistence"};
      }
    }
    const result = await claimLocalCoordinationTodo({
      schema_version: LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA,
      runtime_root: root, goal_id: "goal-a", todo_id: "todo_a", role: "agent",
      claimed_by: "agent-a", actor_agent_id: "agent-a", registered_agents: ["agent-a"],
      operation_id: "no-change", observed_at: "2026-09-05T04:30:00Z", dry_run: false,
    }, {createStore: () => new FaultStore(directory, "goal-a")});
    assert.equal(result.changed, false);
    assert.equal(result.status, fault === "conflict" ? "conflict" : "recovered");
    const receipt = await store.readReceipt("no-change");
    assert.equal(receipt.status, fault === "conflict" ? "missing" : "found");
    const after = await store.loadAuthority();
    assert.equal(after.status, "loaded");
    if (after.status !== "loaded") return;
    if (fault === "conflict") {
      assert.equal((after.head.todos as Record<string, unknown>[])[0]?.claimed_by, "agent-b");
    } else {
      assert.deepEqual(after.head, before.head);
      assert.ok(result.original_receipt);
    }
  }
});

test("provider-first Todo claim validates authority and hard-lease ownership", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-local-authority-claim-gates-"));
  const store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
  const initial = await store.commitAuthority({
    expected_provider_revision: null,
    operation_id: "promote:claim-gates",
    events: [{ schema_version: "promotion_v0" }],
    next_projection: withTodoReadModel({
      goal_id: "goal-a",
      handoff_mode: "hard_lease",
      todos: [todoRecord({ excluded_agents: ["agent-b"] })],
      leases: [],
    }),
    receipts: [],
  });
  assert.equal(initial.status, "applied");
  const base = {
    schema_version: LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
    todo_id: "todo_a",
    role: "agent",
    actor_agent_id: "agent-a",
    registered_agents: ["agent-a", "agent-b"],
    operation_id: "todo-claim:goal-a:todo_a:gated",
    observed_at: "2026-09-05T04:30:00Z",
    dry_run: false,
  };

  const mismatch = await claimLocalCoordinationTodo({
    ...base,
    claimed_by: "agent-b",
  });
  assert.equal(mismatch.reason_code, "claim_actor_mismatch");

  const missingLease = await claimLocalCoordinationTodo({
    ...base,
    claimed_by: "agent-a",
  });
  assert.equal(missingLease.reason_code, "handoff_mode_requires_lease");
  assert.match(
    String(missingLease.reason),
    /loopx todo claim --task-lease-idempotency-key/,
  );
  assert.match(
    String(missingLease.reason),
    /--task-lease-expected-version/,
  );
  assert.equal(
    (missingLease.recovery as Record<string, unknown> | undefined)?.command,
    "loopx todo claim",
  );
  assert.deepEqual(
    (missingLease.recovery as Record<string, unknown> | undefined)?.requires_flags,
    ["--task-lease-idempotency-key"],
  );

  const dryRun = await claimLocalCoordinationTodo({
    ...base,
    claimed_by: "agent-a",
    dry_run: true,
  });
  assert.equal(dryRun.reason_code, "handoff_mode_requires_lease");
  const unchanged = await readLocalCoordinationTodo({
    schema_version: LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
    todo_id: "todo_a",
  });
  assert.equal((unchanged.todo as Record<string, unknown>).claimed_by, undefined);
});

test("provider-first Todo claim atomically acquires its canonical hard lease", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-local-authority-claim-lease-"));
  const store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
  assert.equal((await store.commitAuthority({
    expected_provider_revision: null,
    operation_id: "promote:claim-with-lease",
    events: [{ schema_version: "promotion_v0" }],
    next_projection: withTodoReadModel({
      goal_id: "goal-a",
      handoff_mode: "hard_lease",
      todos: [todoRecord({ required_write_scopes: ["loopx/control_plane/**"] })],
      leases: [],
    }),
    receipts: [],
  })).status, "applied");
  const request = {
    schema_version: LOCAL_COORDINATION_TODO_CLAIM_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
    todo_id: "todo_a",
    role: "agent",
    claimed_by: "agent-a",
    actor_agent_id: "agent-a",
    registered_agents: ["agent-a", "agent-b"],
    operation_id: "todo-claim:goal-a:todo_a:with-lease",
    observed_at: "2026-09-05T04:30:00Z",
    dry_run: false,
    lease_request: {
      idempotency_key: "turn:claim-with-lease",
      expected_version: 0,
      ttl_seconds: 2_700,
    },
  };

  const preview = await claimLocalCoordinationTodo({...request, dry_run: true});
  assert.equal(preview.status, "planned");
  assert.equal(preview.todo_changed, true);
  assert.equal(preview.lease_changed, true);
  const before = await store.loadAuthority();
  assert.equal(before.status, "loaded");
  if (before.status !== "loaded") return;
  assert.deepEqual(before.head.leases, []);
  assert.equal((await store.readReceipt(request.operation_id)).status, "missing");

  const applied = await claimLocalCoordinationTodo(request);
  assert.equal(applied.status, "applied", JSON.stringify(applied));
  assert.equal(applied.todo_changed, true);
  assert.equal(applied.lease_changed, true);
  assert.equal(applied.lease_idempotent, false);
  const after = await store.loadAuthority();
  assert.equal(after.status, "loaded");
  if (after.status !== "loaded") return;
  assert.equal((after.head.todos as Record<string, unknown>[])[0]?.claimed_by, "agent-a");
  assert.deepEqual(after.head.leases, [applied.lease]);
  const lease = applied.lease as Record<string, unknown>;
  assert.equal(lease.schema_version, "task_lease_v0");
  assert.equal(lease.owner, "agent-a");
  assert.equal(lease.idempotency_key, "turn:claim-with-lease");
  assert.deepEqual(lease.write_scopes, ["loopx/control_plane/**"]);
  assert.equal(lease.version, 1);
  assert.equal(lease.lease_epoch, 1);

  const replay = await claimLocalCoordinationTodo({
    ...request,
    observed_at: "2026-09-05T06:00:00Z",
  });
  assert.equal(replay.status, "replayed");
  assert.deepEqual(replay.original_receipt, applied.original_receipt);
  const retiredGeneration = await claimLocalCoordinationTodo({
    ...request,
    operation_id: "todo-claim:goal-a:todo_a:fresh-after-expiry",
    observed_at: "2026-09-05T06:00:00Z",
    lease_request: {...request.lease_request, expected_version: 1},
  });
  assert.equal(retiredGeneration.reason_code, "idempotency_key_reuse");
  assert.deepEqual(await store.loadAuthority(), after);
});

test("local canonical runtime never falls back when provider state is missing", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-local-authority-missing-"));
  const result = await readLocalCoordinationTodo({
    schema_version: LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
    todo_id: "todo_a",
  });
  assert.equal(result.status, "missing");
  assert.equal(result.decision_read_from_provider, true);
  assert.equal(result.legacy_fallback_used, false);
});

test("terminal and archive wire adapters reject coercible numeric values", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "loopx-local-authority-strict-numbers-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const terminalRequest = (leaseExpectedVersion: unknown) => ({
    schema_version: LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
    todo_id: "todo-a",
    role: "agent",
    command: "complete",
    actor_agent_id: "agent-a",
    registered_agents: ["agent-a"],
    lifecycle_grants: [],
    authority_reason: null,
    decision_outcome: null,
    operation_id: "terminal-strict-number",
    lease_idempotency_key: null,
    lease_expected_version: leaseExpectedVersion,
    allow_user_gate_auto_acquire: false,
    requested_no_followup: true,
    requested_completion_turn_key: null,
    requested_completion_identity_source: null,
    linked_successor_todo_ids: [],
    successor_intents: [],
    note: null,
    evidence: "strict wire validation",
    reason: null,
    clear_claim: false,
    validation_declaration: null,
    validation_receipt: null,
    completion_policy_request: null,
    dry_run: false,
    observed_at: "2026-09-07T12:00:00Z",
  });
  const archiveRequest = (maxActiveDone: unknown) => ({
    schema_version: LOCAL_COORDINATION_TODO_ARCHIVE_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
    role: "agent",
    max_active_done: maxActiveDone,
    operation_id: "archive-strict-number",
    dry_run: false,
    observed_at: "2026-09-07T12:00:00Z",
  });

  for (const invalid of [true, "1", 1.5]) {
    let terminalOpened = 0;
    const terminal = await terminalLifecycleLocalCoordinationTodo(
      terminalRequest(invalid),
      {createStore: (directory, goalId) => {
        terminalOpened += 1;
        return new FileAuthorityStore(directory, goalId, {existingOnly: true});
      }},
    );
    assert.equal(terminal.status, "failed");
    assert.equal(
      terminal.reason_code,
      "invalid_local_coordination_todo_terminal_lifecycle_request",
    );
    assert.match(String(terminal.reason), /lease_expected_version.*safe integer/);
    assert.equal(terminalOpened, 0);

    let archiveOpened = 0;
    const archive = await archiveLocalCoordinationTodos(
      archiveRequest(invalid),
      {createStore: (directory, goalId) => {
        archiveOpened += 1;
        return new FileAuthorityStore(directory, goalId, {existingOnly: true});
      }},
    );
    assert.equal(archive.status, "failed");
    assert.equal(archive.reason_code, "invalid_local_coordination_todo_archive_request");
    assert.match(String(archive.reason), /max_active_done.*safe integer/);
    assert.equal(archiveOpened, 0);
  }

  let opened = 0;
  const terminal = await terminalLifecycleLocalCoordinationTodo(
    terminalRequest(1),
    {createStore: (directory, goalId) => {
      opened += 1;
      return new FileAuthorityStore(directory, goalId, {existingOnly: true});
    }},
  );
  const archive = await archiveLocalCoordinationTodos(
    archiveRequest(1),
    {createStore: (directory, goalId) => {
      opened += 1;
      return new FileAuthorityStore(directory, goalId, {existingOnly: true});
    }},
  );
  assert.equal(terminal.status, "missing");
  assert.equal(archive.status, "missing");
  assert.equal(opened, 2, "legal integers must cross the wire boundary unchanged");
});

test("terminal wire preserves legacy optional prose semantics", async (t) => {
  const cases = [
    {field: "note", value: null, expected: "existing-note"},
    {field: "note", value: "", expected: "existing-note"},
    {field: "note", value: "ordinary note", expected: "ordinary note"},
    {field: "note", value: " \u0085 ", expected: "existing-note"},
    {field: "note", value: " first\u0085  second ", expected: "first second"},
    {field: "evidence", value: null, expected: "existing-evidence"},
    {field: "evidence", value: "", expected: "existing-evidence"},
    {field: "evidence", value: "ordinary evidence", expected: "ordinary evidence"},
    {field: "evidence", value: " \u0085 ", expected: "existing-evidence"},
    {field: "evidence", value: " first\u0085  second ", expected: "first second"},
    {field: "reason", value: null, expected: "existing-reason", command: "supersede"},
    {field: "reason", value: "", expected: "existing-reason", command: "supersede"},
    {field: "reason", value: "ordinary reason", expected: "ordinary reason", command: "supersede"},
    {field: "reason", value: " \u0085 ", expected: "existing-reason", command: "supersede"},
    {field: "reason", value: " first\u0085  second ", expected: "first second", command: "supersede"},
  ] as const;

  for (const [index, item] of cases.entries()) {
    const root = await mkdtemp(join(tmpdir(), `loopx-terminal-prose-${index}-`));
    t.after(() => rm(root, {recursive: true, force: true}));
    const store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
    assert.equal((await store.commitAuthority({
      expected_provider_revision: null,
      operation_id: `seed-prose-${index}`,
      events: [],
      next_projection: withTodoReadModel({
        goal_id: "goal-a",
        handoff_mode: "soft_claim",
        todos: [todoRecord({
          claimed_by: "agent-a",
          note: "existing-note",
          evidence: "existing-evidence",
          reason: "existing-reason",
        })],
        leases: [],
      }),
      receipts: [],
    })).status, "applied");
    const request = {
      schema_version: LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA,
      runtime_root: root,
      goal_id: "goal-a",
      todo_id: "todo_a",
      role: "agent",
      command: "command" in item ? item.command : "complete",
      actor_agent_id: "agent-a",
      registered_agents: ["agent-a"],
      lifecycle_grants: [],
      authority_reason: null,
      decision_outcome: null,
      operation_id: `terminal-prose-${index}`,
      lease_idempotency_key: null,
      lease_expected_version: null,
      allow_user_gate_auto_acquire: false,
      requested_no_followup: true,
      requested_completion_turn_key: null,
      requested_completion_identity_source: null,
      linked_successor_todo_ids: [],
      successor_intents: [],
      note: item.field === "note" ? item.value : null,
      evidence: item.field === "evidence" ? item.value : null,
      reason: item.field === "reason" ? item.value : null,
      clear_claim: false,
      validation_declaration: null,
      validation_receipt: null,
      completion_policy_request: null,
      dry_run: false,
      observed_at: "2026-09-08T04:00:00Z",
    };
    const result = await terminalLifecycleLocalCoordinationTodo(request);
    assert.equal(result.status, "applied", `${item.field}=${JSON.stringify(item.value)}: ${JSON.stringify(result)}`);
    const loaded = await store.loadAuthority();
    assert.equal(loaded.status, "loaded");
    if (loaded.status !== "loaded") continue;
    const todo = (loaded.head.todos as Record<string, unknown>[])[0]!;
    assert.equal(todo[item.field], item.expected, `${item.field}=${JSON.stringify(item.value)}`);
  }

  for (const field of ["note", "evidence", "reason"] as const) {
    const invalid = await terminalLifecycleLocalCoordinationTodo({
      schema_version: LOCAL_COORDINATION_TODO_TERMINAL_LIFECYCLE_REQUEST_SCHEMA,
      runtime_root: join(tmpdir(), "loopx-invalid-terminal-prose"),
      goal_id: "goal-a",
      todo_id: "todo-a",
      role: "agent",
      command: field === "reason" ? "supersede" : "complete",
      actor_agent_id: "agent-a",
      registered_agents: ["agent-a"],
      lifecycle_grants: [],
      authority_reason: null,
      decision_outcome: null,
      operation_id: `terminal-invalid-${field}`,
      lease_idempotency_key: null,
      lease_expected_version: null,
      allow_user_gate_auto_acquire: false,
      requested_no_followup: true,
      requested_completion_turn_key: null,
      requested_completion_identity_source: null,
      linked_successor_todo_ids: [],
      successor_intents: [],
      note: field === "note" ? 1 : null,
      evidence: field === "evidence" ? 1 : null,
      reason: field === "reason" ? 1 : null,
      clear_claim: false,
      validation_declaration: null,
      validation_receipt: null,
      completion_policy_request: null,
      dry_run: false,
      observed_at: "2026-09-08T04:00:00Z",
    }, {createStore: (directory, goalId) =>
      new FileAuthorityStore(directory, goalId, {existingOnly: true})});
    assert.equal(invalid.status, "failed");
    assert.match(String(invalid.reason), new RegExp(`${field} must be a string or null`));
  }
});

test("engaged promotion fence blocks every native legacy task-lease writer", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-local-authority-lease-fence-"));
  const shadow = await qualifiedShadow(root);
  const request = promotionRequest(root, shadow.projection, shadow.providerRevision);
  await engageFence(request);

  const authorityPath = join(root, "authority-source.json");
  const authorityContent = "authority-v1";
  await writeFile(authorityPath, authorityContent, "utf8");
  const authority = {
    handoff_mode: "hard_lease",
    registered_agent_candidates: [["agent-a"]],
    todos: [{
      todo_id: "todo_abc",
      status: "open",
      claimed_by: "agent-a",
      role: "agent",
      task_class: "advancement_task",
    }],
    todo_projection_error: null,
    source_receipts: [{
      source_id: "authority",
      path: authorityPath,
      state: "file",
      sha256: createHash("sha256").update(authorityContent).digest("hex"),
    }],
  };
  const acquire = await executeTaskLeaseAcquire({
    schema_version: "loopx_task_lease_acquire_native_v0",
    runtime_root: root,
    goal_id: "goal-a",
    todo_id: "todo_abc",
    owner: "agent-a",
    idempotency_key: "lease:fenced-acquire",
    write_scopes: [],
    ttl_seconds: 60,
    expected_version: null,
    authority,
  });
  assert.equal(acquire.ok, false);
  assert.equal(acquire.error_code, "legacy_coordination_writer_fenced");

  const renew = await executeTaskLeaseLifecycle({
    schema_version: TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION,
    operation: "renew",
    runtime_root: root,
    goal_id: "goal-a",
    todo_id: "todo_abc",
    owner: "agent-a",
    idempotency_key: "lease:fenced-renew",
    expected_version: 1,
    ttl_seconds: 60,
    new_owner: null,
    new_idempotency_key: null,
    authority,
  });
  assert.equal(renew.ok, false);
  assert.equal(renew.error_code, "legacy_coordination_writer_fenced");
});
