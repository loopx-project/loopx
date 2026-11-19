import assert from "node:assert/strict";
import { writeFile, mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import {tmpdir} from "node:os";
import type {TestContext} from "node:test";
import { FileAuthorityStore } from "../../loopx/control_plane/coordination/file_authority_store.ts";
import { canonicalAuthoritySha256 as sha256 } from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {
  LOCAL_COORDINATION_PROMOTION_REQUEST_SCHEMA,
  localCoordinationPromotionPlanSha256,
} from "../../loopx/control_plane/coordination/local_authority_runtime.ts";
import { engageLegacyCoordinationWriterFence, LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
  LEGACY_COORDINATION_WRITER_FENCE_SCHEMA } from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import { bootstrapCoordinationRuntimeShadow, COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA } from "../../loopx/control_plane/coordination/runtime_shadow.ts";
import { projection as fileProjection, sourceRequest, pendingEntry, settleFiles } from "./shadow_file_fixture.ts";
import {deliverShadowEntry} from "../../loopx/control_plane/coordination/shadow_entry_delivery.ts";
import {entrySelection} from "./shadow_file_fixture.ts";
import {atomicWriteJson} from "../../loopx/control_plane/effect_runtime_io.ts";
import {legacyCoordinationWriterFencePath} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";

/** Synthetic completed route for tests that seed a provider directly. */
export async function engageCanonicalRoute(root: string, goalId = "goal-a"): Promise<void> {
  await atomicWriteJson(legacyCoordinationWriterFencePath(root, goalId), {
    schema_version: LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
    state: "engaged", goal_id: goalId, fence_id: "canonical-test-route",
    source_version: "state:test", source_projection_sha256: "a".repeat(64),
    expected_shadow_provider_revision: `file:1:${"a".repeat(24)}`,
  });
}

export async function canonicalReadRoot(context: TestContext, goalId: string): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "canonical-read-route-"));
  context.after(() => rm(root, {recursive: true, force: true}));
  await engageCanonicalRoute(root, goalId);
  return root;
}

function todoRecord(overrides: Record<string, unknown> = {}) {
  return {schema_version: "todo_item_v0", todo_id: "todo_a", role: "agent", status: "open",
    done: false, text: "Qualify canonical Todo semantics", archive_state: "active", source_section: "Agent Todo", ...overrides};
}

// Build a mirrored shadow fixture; this does not authorize promotion.
export async function qualifiedShadow(root: string, handoffMode = "soft_claim", operationCount = 1) {
  const baseline = fileProjection([todoRecord()], [], handoffMode);
  const statePath = join(root, "ACTIVE_GOAL_STATE.md");
  await writeFile(statePath, `---\ngoal_id: goal-a\nhandoff_mode: ${handoffMode}\n---\n\n## Agent Todo\n\n`);
  const store = new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a");
  const f = {root, statePath, baseline, store};
  const bootstrapped = await bootstrapCoordinationRuntimeShadow({
    ...await sourceRequest(f, baseline),
    schema_version: COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA,
    operation_id: "bootstrap:goal-a:state-0", source_version: "state:0",
  });
  assert.equal(bootstrapped.status, "applied", JSON.stringify(bootstrapped));
  for (let sequence = 1; sequence <= operationCount; sequence += 1) {
    const entry = await pendingEntry(
      f,
      sequence,
      {handoff_mode: handoffMode, todos: [todoRecord({
        claimed_by: "agent-a",
        ...(sequence === 1 ? {} : {text: `Qualify canonical Todo semantics ${sequence}`}),
      })]},
      {writeClass: sequence === 1 ? "todo_claim" : "todo_update"},
    );
    const mirrored = await deliverShadowEntry(entrySelection(entry));
    assert.equal(mirrored.outcome, "delivered", JSON.stringify(mirrored));
    await settleFiles(f, entry, mirrored);
  }
  const loaded = await store.loadAuthority();
  assert.equal(loaded.status, "loaded");
  if (loaded.status !== "loaded") throw new Error("fixture head missing");
  return { projection: loaded.head, providerRevision: loaded.provider_revision };
}

export function promotionRequest(
  root: string,
  projection: Record<string, unknown>,
  providerRevision: string,
  canonicalAuthority = "file_v0",
) {
  const digest = sha256(projection);
  const plan = {
    goal_id: "goal-a",
    operation_id: "promote:goal-a:state-1",
    canonical_authority: canonicalAuthority,
    expected_shadow_provider_revision: providerRevision,
    expected_shadow_projection_sha256: digest,
    minimum_operations: 1,
    required_event_kinds: ["todo_claim"],
  };
  return {
    schema_version: LOCAL_COORDINATION_PROMOTION_REQUEST_SCHEMA,
    runtime_root: root,
    ...plan,
    writer_fence: {
      schema_version: LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
      state: "engaged",
      goal_id: "goal-a",
      fence_id: "legacy-writer-fence:goal-a:state-1",
      source_version: "state:1",
      source_projection_sha256: digest,
      expected_shadow_provider_revision: providerRevision,
      promotion_plan_sha256: localCoordinationPromotionPlanSha256(plan),
    },
  };
}

export async function engageFence(request: ReturnType<typeof promotionRequest>) {
  const result = await engageLegacyCoordinationWriterFence({
    schema_version: LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
    runtime_root: request.runtime_root,
    goal_id: request.goal_id,
    state_path: join(request.runtime_root, "ACTIVE_GOAL_STATE.md"),
    fence: request.writer_fence,
  });
  assert.equal(result.status, "applied");
}
