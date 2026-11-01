import assert from "node:assert/strict";
import test from "node:test";
import {
  comparisonWriteScopes,
  evaluateTaskLeaseAcquireDecision,
  evaluateTaskLeaseWriteScopesOverlap,
} from "../../loopx/control_plane/work_items/task_lease_acquire_decision.ts";

const overlaps = (left: string, right: string) =>
  evaluateTaskLeaseWriteScopesOverlap({left: [left], right: [right]}).overlap;

// Each positive row names a concrete path in both sets, independently of how
// the matcher decides whether an arbitrary descendant can exist.
for (const [directory, pattern, witness] of [
  ["src/", "src/components/*.ts", "src/components/button.ts"],
  ["src/", "src/main.*", "src/main.ts"],
  ["src/", "**/*.ts", "src/components/button.ts"],
  ["src/components/", "src/*/*.ts", "src/components/button.ts"],
  ["src/", "src/components/?.ts", "src/components/a.ts"],
  ["src/a/", "src/[ab]/*.ts", "src/a/button.ts"],
  ["src/b/", "src/[!a]/*.ts", "src/b/button.ts"],
  ["src/SS/", "src/[ß]/*.ts", "src/SS/button.ts"],
  ["src/SS/", "src/S?/*.ts", "src/SS/button.ts"],
  ["src/ς/", "src/[Σ]/*.ts", "src/ς/button.ts"],
  ["src/😀/", "src/?/*.ts", "src/😀/button.ts"],
  ["src/", "src/generated/[A-Z].ts", "src/generated/a.ts"],
  ["src/", "src/generated/[!z-a].ts", "src/generated/a.ts"],
  ["./src//", "src/./components/*.ts", "src/components/button.ts"],
]) {
  test(`directory scope overlaps ${pattern} under ${directory}`, () => {
    assert.equal(overlaps(directory, witness), true);
    assert.equal(overlaps(pattern, witness), true);
    assert.equal(overlaps(directory, pattern), true);
    assert.equal(overlaps(pattern, directory), true);
  });
}

for (const [directory, pattern] of [
  ["src/", "docs/*.ts"],
  ["src/a/", "src/ab/*.ts"],
  ["src/z/", "src/[ab]/*.ts"],
  ["src/z/", "src/[!z]/*.ts"],
  ["src/long/", "src/?/main.ts"],
  ["src/Σ/", "src/[!ς]/*.ts"],
  ["src/SS/", "src/[!ß]/*.ts"],
  ["src/", "src/generated/[z-a].ts"],
  ["src/", "src/generated/[!\u0000-\u{10ffff}].ts"],
  // The only code point outside this class is A, whose fold alias a is inside.
  ["src/", "src/generated/[!\u0000-@B-\u{10ffff}].ts"],
  ["src/main.ts", "src/test.ts"],
]) {
  test(`directory comparison preserves disjoint ${JSON.stringify([directory, pattern])}`, () => {
    assert.equal(overlaps(directory, pattern), false);
    assert.equal(overlaps(pattern, directory), false);
  });
}

test("directory matching retains stored spelling and existing authority boundaries", () => {
  const repository = "git:github.com/example/project";
  const workspace = {host: "1".repeat(64), common_directory: "2".repeat(64),
    worktree: "3".repeat(64), repository};
  const request = {
    handoff_mode: "hard_lease", registered_agents: ["agent-a", "agent-b"],
    todo: {todo_id: "todo_b", status: "open", claimed_by: null, excluded_agents: [],
      task_repository: repository},
    lease: null,
    other_leases: [{todo_id: "todo_a", active: true, effective: true,
      write_scopes: ["./src//"], write_repository: repository, write_workspace: workspace}],
    command: {owner: "agent-b", idempotency_key: "second", expected_version: 0,
      ttl_seconds: 60, write_scopes: ["src/components/*.ts"]},
  };
  const original = structuredClone(request);
  assert.equal(evaluateTaskLeaseAcquireDecision(request).code, "write_scope_conflict");
  const differentRepository = structuredClone(request);
  differentRepository.todo.task_repository = "git:github.com/example/other";
  assert.equal(evaluateTaskLeaseAcquireDecision(differentRepository).outcome, "apply");
  const inactive = structuredClone(request);
  inactive.other_leases[0].active = false;
  assert.equal(evaluateTaskLeaseAcquireDecision(inactive).outcome, "apply");
  const sameCheckout = {...request, command: {...request.command, write_workspace: workspace}};
  assert.equal(evaluateTaskLeaseAcquireDecision(sameCheckout).code, "write_scope_conflict");
  const independent = {...request, command: {...request.command,
    write_workspace: {...workspace, worktree: "4".repeat(64)}}};
  const advisory = evaluateTaskLeaseAcquireDecision(independent);
  assert.equal(advisory.outcome, "apply");
  assert.deepEqual(advisory.overlap_advisory_indexes, [0]);
  assert.deepEqual(request, original);
  assert.deepEqual(comparisonWriteScopes(["src/"]), ["src/"]);
});
