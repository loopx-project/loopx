import assert from "node:assert/strict";
import { parseTodoCompletionProof } from "./todo-completion-feedback";

// Explicit proof input must never be inferred from a task claim or incomplete JSON.
const proof = {agent_id: "agent-a", task_lease_idempotency_key: "original-execution", task_lease_expected_version: 1};
assert.deepEqual(parseTodoCompletionProof(JSON.stringify(proof)), proof);
for (const input of ["", "null", "[]", "{", JSON.stringify({...proof, actor: "agent-a"}),
  JSON.stringify({...proof, agent_id: ""}), JSON.stringify({...proof, task_lease_idempotency_key: " "}),
  JSON.stringify({...proof, task_lease_expected_version: true}),
  JSON.stringify({...proof, task_lease_expected_version: "1"}),
  JSON.stringify({...proof, task_lease_expected_version: -1}),
  JSON.stringify({agent_id: "agent-a", task_lease_expected_version: 1})]) {
  assert.equal(parseTodoCompletionProof(input), null);
}
console.log("Explicit completion proof input: passed");
