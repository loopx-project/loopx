import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  buildTodoCompletionMetadataUpdates,
  normalizeTodoCompletionValue,
  requireTodoCompletionMetadataValue,
  selectTodoCompletionState,
  TODO_COMPLETION_STATE_REQUEST_SCHEMA,
} from "../../loopx/control_plane/todos/completion_state.ts";
import {
  createEffectRuntimeHandlers,
  dispatchEffectRuntimeMethod,
} from "../../loopx/control_plane/effect_runtime_handlers.ts";

const fixture = JSON.parse(
  readFileSync(
    new URL(
      "../fixtures/control_plane/todo_completion_state_characterization_v0.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as {
  schema_version: string;
  source_baseline: string;
  cases: Array<{
    name: string;
    method: "normalize" | "require_metadata" |
      "evaluate" | "metadata_updates";
    request: Record<string, unknown>;
    expected_result?: Record<string, unknown>;
    expected_error?: string;
  }>;
};

const methods = {
  normalize: normalizeTodoCompletionValue,
  require_metadata: requireTodoCompletionMetadataValue,
  evaluate: selectTodoCompletionState,
  metadata_updates: buildTodoCompletionMetadataUpdates,
} as const;

test("pinned Python completion-state characterization remains exact", () => {
  assert.equal(
    fixture.schema_version,
    "loopx_todo_completion_state_characterization_v0",
  );
  assert.equal(fixture.source_baseline, "0c59d47f6");
  assert.equal(fixture.cases.length, 10);
  for (const item of fixture.cases) {
    const evaluate = methods[item.method];
    if (item.expected_error) {
      assert.throws(
        () => evaluate(item.request),
        (error: unknown) =>
          error instanceof Error && error.message === item.expected_error,
        item.name,
      );
    } else {
      assert.deepEqual(evaluate(item.request), item.expected_result, item.name);
    }
  }
});

test("whole completion owner selects continuation and rejects contradictory intent", () => {
  const request = {
    schema_version: TODO_COMPLETION_STATE_REQUEST_SCHEMA,
    todo: { status: "open", no_followup: false },
  };
  for (const [noFollowup, hasSuccessor, continuation] of [
    [false, false, "active_goal"],
    [false, true, "successor"],
    [true, false, "no_followup"],
  ] as const) {
    assert.deepEqual(selectTodoCompletionState({
      ...request, requested_no_followup: noFollowup, has_successor: hasSuccessor,
    }), {
      schema_version: "loopx_todo_completion_state_result_v0",
      continuation, recovery: null,
    });
  }
  assert.throws(() => selectTodoCompletionState({
    ...request, requested_no_followup: true, has_successor: true,
  }), /cannot record both no_followup and a successor/);
});

test("retired continuation-only RPC fails closed; live metadata decoding remains", async () => {
  const handlers = createEffectRuntimeHandlers({
    fingerprint: "completion-retirement-test", requestShutdown: () => undefined,
  });
  await assert.rejects(dispatchEffectRuntimeMethod(
    handlers, "todo.completion_state.continuation_for_write", {
      schema_version: TODO_COMPLETION_STATE_REQUEST_SCHEMA,
      no_followup: false, has_successor: false,
    },
  ), /unsupported Effect runtime method/);
  assert.deepEqual(await dispatchEffectRuntimeMethod(
    handlers, "todo.completion_state.require_metadata", {
      schema_version: TODO_COMPLETION_STATE_REQUEST_SCHEMA,
      key: "completion_continuation", value: "SUCCESSOR",
    },
  ), { schema_version: "loopx_todo_completion_state_result_v0", value: "successor" });
});

test("runtime boundary rejects malformed authority input", () => {
  const valid = {
    schema_version: TODO_COMPLETION_STATE_REQUEST_SCHEMA,
    todo: {
      status: "open",
      no_followup: false,
      completion_continuation: "",
    },
    requested_no_followup: false,
    has_successor: false,
  };
  assert.throws(
    () => selectTodoCompletionState({ ...valid, schema_version: "v1" }),
    /schema mismatch/,
  );
  assert.throws(
    () => selectTodoCompletionState({ ...valid, requested_no_followup: "false" }),
    /requested_no_followup must be a boolean/,
  );
  assert.throws(
    () => buildTodoCompletionMetadataUpdates({
      schema_version: TODO_COMPLETION_STATE_REQUEST_SCHEMA,
      block: { successor_todo_ids: [42] },
      target_status: "done",
      normalized_status: "done",
      completion_continuation: null,
      completion_recovery: null,
      no_followup: null,
      successor_todo_ids: null,
    }),
    /array of strings/,
  );
});

test("state and metadata decisions do not mutate caller input", () => {
  const request = fixture.cases.find((item) => item.method === "evaluate")!.request;
  const before = structuredClone(request);
  selectTodoCompletionState(request);
  assert.deepEqual(request, before);

  const metadata = fixture.cases.find(
    (item) => item.method === "metadata_updates",
  )!.request;
  const metadataBefore = structuredClone(metadata);
  buildTodoCompletionMetadataUpdates(metadata);
  assert.deepEqual(metadata, metadataBefore);
});

test("lifecycle reentry emits a distinct terminal recovery", () => {
  assert.deepEqual(
    selectTodoCompletionState({
      schema_version: TODO_COMPLETION_STATE_REQUEST_SCHEMA,
      todo: {
        status: "done",
        no_followup: false,
        completion_continuation: "active_goal",
      },
      requested_no_followup: true,
      has_successor: false,
      completion_identity_source: "lifecycle_reentry",
    }),
    {
      schema_version: "loopx_todo_completion_state_result_v0",
      continuation: "no_followup",
      recovery: "lifecycle_reentry_terminal_closeout",
    },
  );
});
