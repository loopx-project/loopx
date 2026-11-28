import assert from "node:assert/strict";
import {mkdtempSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {
  DECISION_CONTEXT_HOOK_REQUEST_SCHEMA,
  DECISION_CONTEXT_HOOK_VIEW_SCHEMA,
  decisionContextReusableContextPaths,
  executeDecisionContextHook,
} from "../../loopx/control_plane/capabilities/decision_context_hook.ts";
import {reusableTurnContext} from "../../loopx/control_plane/capability_hooks.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

function baseRequest(overrides: JsonObject = {}): JsonObject {
  return {
    schema_version: DECISION_CONTEXT_HOOK_REQUEST_SCHEMA,
    goal_state_ref: "/workspace/goal/ACTIVE_GOAL_STATE.md",
    goal_instance_id: "goal-inst-1",
    goal_id: "personal-ops",
    agent_id: "operations",
    registered_agents: ["operations", "reviewer"],
    runtime_root: "/workspace/runtime",
    action: "read",
    ...overrides,
  };
}

for (const [label, createStore] of [
  [
    "FileAuthorityStore",
    (dir: string): AuthorityStore => new FileAuthorityStore(join(dir, "file"), "binding"),
  ],
  [
    "SqliteAuthorityStore",
    (dir: string): AuthorityStore => new SqliteAuthorityStore(join(dir, "sqlite"), "binding"),
  ],
] as const) {
  test(`decision-context hook lifecycle, CAS, replay and redaction (${label})`, async () => {
    const dir = mkdtempSync(join(tmpdir(), "dc-hook-ts-"));
    try {
      const store = createStore(dir);
      const now = "2026-11-26T15:00:00Z";

      const initialObserve = await executeDecisionContextHook(
        baseRequest({action: "observe"}),
        store,
        now,
      );
      assert.deepEqual(initialObserve, {
        ok: true,
        status: "absent",
        binding_state: "unconfigured",
        observation_count: 0,
      });

      const preview = await executeDecisionContextHook(
        baseRequest({
          action: "bind",
          expected_revision: null,
          operation_id: "op-bind-1",
          source_kind: "operator_configuration",
          profile_path: "/private/secret/ops-profile.json",
          cursor_state_path: "/private/secret/cursors.json",
          mode: "prepare_evidence",
          execute: false,
        }),
        store,
        now,
      );
      assert.equal(preview.ok, true);
      assert.equal(preview.status, "preview");
      assert.equal(JSON.stringify(preview).includes("/private/secret"), false);

      const applied = await executeDecisionContextHook(
        baseRequest({
          action: "bind",
          expected_revision: null,
          operation_id: "op-bind-1",
          source_kind: "operator_configuration",
          profile_path: "/private/secret/ops-profile.json",
          cursor_state_path: "/private/secret/cursors.json",
          mode: "prepare_evidence",
          execute: true,
        }),
        store,
        now,
      );
      assert.equal(applied.ok, true);
      assert.equal(applied.status, "applied");
      assert.equal(JSON.stringify(applied).includes("/private/secret"), false);
      const current1 = applied.current as JsonObject;
      const rev1 = String(current1.revision);
      assert.ok(rev1.length > 0);

      // Idempotent replay with same operation_id returns replayed + fresh state.
      const replayed = await executeDecisionContextHook(
        baseRequest({
          action: "bind",
          expected_revision: null,
          operation_id: "op-bind-1",
          source_kind: "operator_configuration",
          profile_path: "/private/secret/ops-profile.json",
          cursor_state_path: "/private/secret/cursors.json",
          mode: "prepare_evidence",
          execute: true,
        }),
        store,
        now,
      );
      assert.equal(replayed.ok, true);
      assert.equal(replayed.status, "replayed");

      // Conflicting intent under same operation_id fails closed.
      const conflict = await executeDecisionContextHook(
        baseRequest({
          action: "bind",
          expected_revision: null,
          operation_id: "op-bind-1",
          source_kind: "operator_configuration",
          profile_path: "/private/secret/other-profile.json",
          mode: "prepare_evidence",
          execute: true,
        }),
        store,
        now,
      );
      assert.equal(conflict.ok, false);
      assert.equal(conflict.status, "operation_conflict");

      // Trusted resolve_local returns locators; public read & history never do.
      const localResolved = await executeDecisionContextHook(
        baseRequest({action: "resolve_local"}),
        store,
        now,
      );
      assert.equal(
        (localResolved.local_binding as JsonObject).profile_path,
        "/private/secret/ops-profile.json",
      );

      const projected = await executeDecisionContextHook(
        baseRequest({
          action: "project_turn_view",
          expected_revision: rev1,
          observation: {
            status: "available",
            observed_at: "2026-11-26T15:05:00Z",
            source_count: 4,
            enabled_source_count: 4,
            changed_source_ids: ["ops:board", "ops:lark-small"],
            failed_source_ids: [],
            alerted_source_ids: ["ops:lark-small"],
            uncovered_p0_source_ids: [],
            p0_complete: true,
            freshness_counts: {fresh: 3, stale: 1, never_read: 0, not_scanned: 0},
            advisory_recall_status: "completed",
            capture_pending_batch_count: null,
            raw_content_captured: false,
            private_locator_captured: false,
          },
          commands: {
            turn_context:
              "loopx decision-context hook turn-context --goal-id personal-ops --agent-id operations --format json",
            prepare_evidence:
              "loopx decision-context prepare-evidence --decision-id turn-rebase --goal-id personal-ops --agent-id operations --format json",
          },
        }),
        store,
        now,
      );
      assert.equal(projected.ok, true);
      assert.equal(projected.status, "read");
      assert.equal(projected.observation_count, 4);
      assert.equal(JSON.stringify(projected).includes("/private/secret"), false);

      // Reusable turn context exposes only static instructions, keeping live observation fresh.
      assert.deepEqual(decisionContextReusableContextPaths(projected), [
        ["current", "instructions"],
      ]);
      const reusable = reusableTurnContext(
        {
          source: "turn_start_capability_hook",
          hook_id: "decision_context.turn_context",
          capability_id: "decision-context",
          kind: "decision_context",
          content: projected,
        },
        {goal_id: "personal-ops", agent_id: "operations"},
      );
      assert.deepEqual(reusable, {
        identity: [
          "turn_start_capability_hook",
          "decision_context.turn_context",
          "decision-context",
          "personal-ops",
          "operations",
          null,
        ],
        paths: [["current", "instructions"]],
      });
      assert.equal(
        reusableTurnContext(
          {
            source: "turn_start_capability_hook",
            hook_id: "decision_context.turn_context",
            capability_id: "decision-context",
            kind: "decision_context",
            content: projected,
          },
          {goal_id: "personal-ops", agent_id: "reviewer"},
        ),
        null,
      );

      // Private-key smuggling inside observation fails closed.
      await assert.rejects(() =>
        executeDecisionContextHook(
          baseRequest({
            action: "project_turn_view",
            expected_revision: rev1,
            observation: {
              status: "available",
              observed_at: "2026-11-26T15:05:00Z",
              source_count: 1,
              enabled_source_count: 1,
              p0_complete: true,
              private_locator: "/private/secret/leak.md",
            },
            commands: {},
          }),
          store,
          now,
        ),
      );

      // Unbind retires binding, clears private locators, and keeps observation_count=1 to invalidate cache.
      const unbound = await executeDecisionContextHook(
        baseRequest({
          action: "unbind",
          expected_revision: rev1,
          operation_id: "op-unbind-1",
          source_kind: "operator_configuration",
          execute: true,
        }),
        store,
        "2026-11-26T15:10:00Z",
      );
      assert.equal(unbound.ok, true);
      assert.equal(unbound.status, "applied");
      const rev2 = String((unbound.current as JsonObject).revision);

      // Stale observation collected under rev1 is rejected rather than projected onto rev2.
      const staleProjection = await executeDecisionContextHook(
        baseRequest({
          action: "project_turn_view",
          expected_revision: rev1,
          observation: {
            status: "available",
            observed_at: "2026-11-26T15:09:59Z",
            source_count: 1,
            enabled_source_count: 1,
            p0_complete: false,
            uncovered_p0_source_ids: ["ops:stale-a"],
          },
          commands: {},
        }),
        store,
        "2026-11-26T15:10:00Z",
      );
      assert.equal(staleProjection.ok, false);
      assert.equal(staleProjection.status, "revision_conflict");
      assert.equal(staleProjection.error, "observed_binding_revision_mismatch");

      const retiredLocal = await executeDecisionContextHook(
        baseRequest({action: "resolve_local"}),
        store,
        "2026-11-26T15:10:00Z",
      );
      assert.equal(retiredLocal.local_binding, null);
      const retiredTurn = await executeDecisionContextHook(
        baseRequest({
          action: "project_turn_view",
          expected_revision: rev2,
          commands: {},
        }),
        store,
        "2026-11-26T15:10:00Z",
      );
      assert.equal(retiredTurn.ok, true);
      assert.equal(retiredTurn.observation_count, 1);
      assert.equal(
        ((retiredTurn.current as JsonObject).observation as JsonObject).status,
        "retired",
      );

      const history = await executeDecisionContextHook(
        baseRequest({action: "history"}),
        store,
        "2026-11-26T15:10:00Z",
      );
      assert.equal(history.ok, true);
      assert.equal((history.events as unknown[]).length, 2);
      assert.equal(JSON.stringify(history).includes("/private/secret"), false);
      assert.equal(
        ((history.current as JsonObject).schema_version),
        DECISION_CONTEXT_HOOK_VIEW_SCHEMA,
      );
    } finally {
      rmSync(dir, {recursive: true, force: true});
    }
  });
}
