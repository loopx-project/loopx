/**
 * Initialize a brand-new disposable example, never promote an existing Goal.
 * Domain records, binding digests and mutation authority remain production TS.
 */
import {readFile, mkdir} from "node:fs/promises";
import {resolve, join, isAbsolute} from "node:path";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {canonicalAuthoritySha256, authorityUnicodeCompare} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {canonicalTodoDomainRecord, TODO_DOMAIN_ITEM_SCHEMA, TODO_DOMAIN_READ_RECORD_SCHEMA}
  from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {openLocalAuthorityStore, selectLocalSqliteAuthority}
  from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {engageLegacyCoordinationWriterFence}
  from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {configureGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";

const [directory, provider = "file"] = process.argv.slice(2);
if (!directory || !isAbsolute(directory) || !["file", "sqlite"].includes(provider)) {
  throw new Error("absolute disposable directory and file|sqlite provider required");
}
const root = resolve(directory);
const setup = JSON.parse(await readFile(join(root, "bootstrap.json"), "utf8"));
const goalId = "synthetic-managed-research";
// Exclusive creation is the safety gate. Existing runtime state is never opened.
const runtime = join(root, "runtime");
await mkdir(runtime);
if (provider === "sqlite") await selectLocalSqliteAuthority(runtime, goalId, true);
const store = await openLocalAuthorityStore(runtime, goalId);
{
  const todos = (setup.tasks as JsonObject[]).map(task => canonicalTodoDomainRecord({
    ...task, schema_version: TODO_DOMAIN_ITEM_SCHEMA, role: "agent", status: "open",
    done: false, archive_state: "active", task_class: "advancement_task", action_kind: "implement",
  }, "disposable research task")).sort((a, b) => authorityUnicodeCompare(String(a.todo_id), String(b.todo_id)));
  const projection = {goal_id: goalId, todos, leases: [], handoff_mode: "soft_claim",
    todo_read_model: coordinationTodoReadModel(todos, TODO_DOMAIN_READ_RECORD_SCHEMA)};
  const created = await store.commitAuthority({expected_provider_revision: null,
    operation_id: "research-demo-initialize", events: [], receipts: [], next_projection: projection});
  if (created.status !== "applied") throw new Error(JSON.stringify(created));
  const fence = await engageLegacyCoordinationWriterFence({
    schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
    runtime_root: runtime, goal_id: goalId, state_path: join(root, "project", "ACTIVE_GOAL_STATE.md"),
    fence: {schema_version: "loopx_legacy_coordination_writer_fence_v0", state: "engaged",
      goal_id: goalId, fence_id: "research-demo-initialize", source_version: "new-disposable-goal",
      source_projection_sha256: canonicalAuthoritySha256(projection),
      expected_shadow_provider_revision: created.provider_revision},
  });
  if (fence.status !== "applied") throw new Error(JSON.stringify(fence));
  const configured = await configureGoalAcceptance(store, {
    goal_id: goalId, operation_id: "research-demo-owner-acceptance", actor_agent_id: null,
    expected_provider_revision: created.provider_revision, document: setup.document,
  });
  if (configured.status !== "applied") throw new Error(JSON.stringify(configured));
  process.stdout.write(JSON.stringify(configured));
}
