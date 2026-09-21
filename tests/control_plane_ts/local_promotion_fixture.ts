import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { FileAuthorityStore } from "../../loopx/control_plane/coordination/file_authority_store.ts";
import { canonicalAuthoritySha256 as sha256 } from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import { LOCAL_COORDINATION_PROMOTION_REQUEST_SCHEMA } from "../../loopx/control_plane/coordination/local_authority_runtime.ts";
import { engageLegacyCoordinationWriterFence, LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
  LEGACY_COORDINATION_WRITER_FENCE_SCHEMA } from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import { bootstrapCoordinationRuntimeShadow, COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA } from "../../loopx/control_plane/coordination/runtime_shadow.ts";
import { projection as fileProjection, sourceRequest, pendingEntry, settleFiles } from "./shadow_file_fixture.ts";
import { commitLocalAuthorityShadowEntry } from "../../loopx/control_plane/coordination/local_authority_shadow.ts";

function todoRecord(overrides: Record<string, unknown> = {}) {
  return {schema_version: "todo_item_v0", todo_id: "todo_a", role: "agent", status: "open",
    done: false, text: "Qualify canonical Todo semantics", archive_state: "active", source_section: "Agent Todo", ...overrides};
}

// Build a mirrored shadow fixture; this does not authorize promotion.
export async function qualifiedShadow(root: string, handoffMode = "soft_claim") {
  const baseline = fileProjection([todoRecord()], [], handoffMode);
  const statePath = join(root, "ACTIVE_GOAL_STATE.md");
  await writeFile(statePath, "---\ngoal_id: goal-a\nhandoff_mode: soft_claim\n---\n\n## Agent Todo\n\n");
  const store = new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a");
  const f = {root, statePath, baseline, store};
  const bootstrapped = await bootstrapCoordinationRuntimeShadow({
    ...await sourceRequest(f, baseline),
    schema_version: COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA,
    operation_id: "bootstrap:goal-a:state-0", source_version: "state:0",
  });
  assert.equal(bootstrapped.status, "applied", JSON.stringify(bootstrapped));
  const entry = await pendingEntry(f, 1, {handoff_mode: handoffMode, todos: [todoRecord({claimed_by: "agent-a"})]},
    {writeClass: "todo_claim"});
  const mirrored = await commitLocalAuthorityShadowEntry(entry);
  assert.equal(mirrored.outcome, "delivered", JSON.stringify(mirrored));
  await settleFiles(f, entry, mirrored);
  const loaded = await store.loadAuthority();
  assert.equal(loaded.status, "loaded");
  if (loaded.status !== "loaded") throw new Error("fixture head missing");
  return { projection: loaded.head, providerRevision: loaded.provider_revision };
}

export function promotionRequest(
  root: string,
  projection: Record<string, unknown>,
  providerRevision: string,
) {
  const digest = sha256(projection);
  return {
    schema_version: LOCAL_COORDINATION_PROMOTION_REQUEST_SCHEMA,
    runtime_root: root,
    goal_id: "goal-a",
    operation_id: "promote:goal-a:state-1",
    expected_shadow_provider_revision: providerRevision,
    expected_shadow_projection_sha256: digest,
    minimum_operations: 1,
    required_event_kinds: ["todo_claim"],
    writer_fence: {
      schema_version: LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
      state: "engaged",
      goal_id: "goal-a",
      fence_id: "legacy-writer-fence:goal-a:state-1",
      source_version: "state:1",
      source_projection_sha256: digest,
      expected_shadow_provider_revision: providerRevision,
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
