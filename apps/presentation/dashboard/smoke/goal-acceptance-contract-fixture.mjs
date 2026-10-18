// Synthetic server projection. No acceptance state is inferred by the client.
export const acceptance = {
  schema_version: "goal_acceptance_observation_projection_v0", goal_id: "release-demo",
  read_only: true, acceptance_assessed: false, coverage: "partial", missing_sources: [],
  truncated: false, historical_progress: [], acceptance_gaps: [], guards: [], next_action: null, next_action_source: null,
};
export const contract = {
  enabled: true, revision: 7, digest: "a".repeat(64), objective: "Deliver a recoverable release",
  non_goals: ["Publishing the release"], status: "held", held_todo_ids: ["todo_unbound", "todo_stale"], verification: null,
  criteria: [{ id: "recovery", description: "An independent recovery check passes." }],
  tasks: [
    { todo_id: "todo_confirmed", state: "ready", criterion_ids: ["recovery"], applicable: true },
    { todo_id: "todo_unbound", state: "unbound", criterion_ids: [], reason: "No criterion is associated.", applicable: true },
    { todo_id: "todo_stale", state: "stale", criterion_ids: ["recovery"], reason: "Association refers to a prior contract revision.", applicable: true },
    { todo_id: "todo_retired", state: "stale", criterion_ids: ["recovery"], applicable: false },
  ],
};
export function verifiedContract(status) {
  return {
    ...contract, status,
    tasks: status === "held" ? contract.tasks : [contract.tasks[0], contract.tasks[3]],
    held_todo_ids: status === "held" ? contract.held_todo_ids : [],
    verification: status === "unverified" ? null : {
      operation_id: "verify-release-7", contract_revision: status === "stale" ? 6 : 7,
      contract_digest: status === "stale" ? "c".repeat(64) : contract.digest,
      todo_id: status === "partial" ? "todo_confirmed" : null,
      results: [{ criterion_id: "recovery", passed: status !== "failed", exit_code: status === "failed" ? 1 : 0 }],
    },
  };
}
export function oracleContract(state = "current") {
  const value = structuredClone(verifiedContract("accepted"));
  value.criteria[0].oracle = {
    kind: "owner_pinned_oracle", source_id: "recovery-checker", source_digest: "d".repeat(64),
    input_refs: ["expected.txt"], output_refs: ["recovery.txt"], state,
  };
  value.verification.results[0].oracle_evidence = {
    source_id: "recovery-checker", source_digest: "d".repeat(64), checked_at: "2026-09-01T01:02:03Z",
    input_versions: [{ref: "expected.txt", sha256: "e".repeat(64)}],
    output_versions: [{ref: "recovery.txt", sha256: "f".repeat(64)}],
  };
  return value;
}
export const snapshot = {
  ok: true, goal_id: "release-demo", observed_at: "2026-09-01T00:00:00Z", acceptance,
  graph: {
    schema_version: "task_graph_projection_v0", mode: "read_only", goal_id: "release-demo", generated_at: null,
    truth_contract: { projection_is_writable: false, write_api: false },
    limits: { user_gate_node_limit: 2, user_gate_open_count: 0, user_gate_truncated_count: 0, topology_complete: true },
    nodes: [{ node_id: "current", kind: "deliverable", title: "Integrate the verified release package", state: "open", refs: { todo_ids: ["todo_integrate"] } }],
    edges: [],
  },
};
