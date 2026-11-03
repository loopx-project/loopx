import assert from "node:assert/strict";
import test from "node:test";
import fixture from "../fixtures/control_plane/completion_evidence_read.json" with {type: "json"};
import {readTodo, TODO_READ_REQUEST} from "../../loopx/control_plane/todos/read_projection.ts";

for (const sample of fixture.cases) {
  test(`native complete evidence: ${sample.name}`, async () => {
    const expected = JSON.parse(sample.bytes);
    const input = {schema_version: TODO_READ_REQUEST, kind: "completion_evidence",
      operation: sample.operation, todo: sample.todo,
      expected_todo_id: "expected" in sample ? sample.expected : "todo_fixture0001",
      existing_todo_ids: "existing" in sample ? sample.existing : null,
      ...(sample.operation === "terminal" ? {completion_turn_key: "turn-a"} : {})};
    if (expected.error_type) await assert.rejects(readTodo(input), {message: expected.message});
    else assert.deepEqual((await readTodo(input)).completion, expected.result);
  });
}
