import assert from "node:assert/strict";
import { deliveryReviewMarkdown, parseDeliveryReview } from "../node_modules/.cache/delivery-review/data/delivery-review.js";
import { deliveryReviewCopy } from "../node_modules/.cache/delivery-review/features/personal-workspace/delivery-review-copy.js";
import { acceptance, contract, oracleContract, snapshot, verifiedContract } from "./goal-acceptance-contract-fixture.mjs";

const withContract = value => ({ ...snapshot, acceptance: { ...acceptance, goal_acceptance_contract: value } });
const before = structuredClone(contract);
for (const copy of Object.values(deliveryReviewCopy)) {
  const baseline = deliveryReviewMarkdown(parseDeliveryReview(snapshot, snapshot.goal_id), copy);
  for (const value of [undefined, { enabled: false }, { ...verifiedContract("accepted"), enabled: false }]) {
    assert.equal(deliveryReviewMarkdown(parseDeliveryReview(withContract(value), snapshot.goal_id), copy), baseline,
      "Missing/disabled contract must preserve baseline export even with retained success data");
  }
  for (const status of ["unverified", "failed", "stale", "accepted", "partial", "held"]) {
    const source = withContract(verifiedContract(status));
    const parsed = parseDeliveryReview(source, snapshot.goal_id);
    assert.deepEqual(parsed.acceptance.goal_acceptance_contract, source.acceptance.goal_acceptance_contract);
    const markdown = deliveryReviewMarkdown(parsed, copy);
    assert.ok(markdown.includes(`- recovery: ${contract.criteria[0].description}\n`), "Ordinary criterion export retains its existing shape");
    for (const text of [copy.contract.boundary, contract.objective, contract.digest, `${copy.contract.revision}: 7`,
      `${copy.contract.source}: release-demo`, copy.contract.taskState.ready, copy.contract.taskState.stale,
      copy.contract.notApplicable, copy.contract.verificationState[status]]) {
      assert.ok(markdown.includes(text), `Missing readback: ${text}`);
    }
    if (status !== "accepted") assert.ok(!markdown.includes(copy.contract.verificationState.accepted), "Association readiness must not imply passing checks");
    if (status === "held") assert.ok(markdown.includes(copy.contract.taskState.unbound));
    if (status === "stale") assert.ok(markdown.includes("c".repeat(64)) && markdown.includes(`${copy.contract.revision}: 6`), "Historical verification must retain its distinct basis");
    if (status === "partial") assert.ok(markdown.includes(`${copy.contract.verificationScope}: todo\\_confirmed`));
  }
  const selected = parseDeliveryReview(withContract({ ...contract,
    scope: { kind: "selected_work", todo_ids: ["todo_confirmed"] } }), snapshot.goal_id);
  assert.ok(deliveryReviewMarkdown(selected, copy).includes(copy.contract.selectedWork));
  assert.ok(deliveryReviewMarkdown(parseDeliveryReview(withContract(contract), snapshot.goal_id), copy).includes(copy.contract.allWork));
  const empty = parseDeliveryReview(withContract({ ...contract, criteria: [], tasks: [] }), snapshot.goal_id);
  assert.ok(deliveryReviewMarkdown(empty, copy).includes(copy.contract.noTasks));
  assert.ok(deliveryReviewMarkdown(empty, copy).includes(copy.contract.noCriteria));
  for (const [state, expected] of Object.entries({current: "accepted", stale: "stale", failed: "failed", recorded: "unverified", unavailable: "unverified", unverified: "unverified"})) {
    const value = oracleContract(state), original = structuredClone(value);
    const markdown = deliveryReviewMarkdown(parseDeliveryReview(withContract(value), snapshot.goal_id), copy);
    for (const evidence of ["recovery-checker", "d".repeat(64), "2026-09-01T01:02:03Z", "expected.txt", "e".repeat(64), "recovery.txt", "f".repeat(64)]) {
      assert.ok(markdown.includes(evidence), `Downloaded checks must preserve their actual source and tested versions: ${evidence}`);
    }
    assert.ok(markdown.includes(copy.contract.verificationState[expected]));
    if (expected !== "accepted") assert.ok(!markdown.includes(copy.contract.verificationState.accepted), "A retained pass is not a current pass");
    assert.deepEqual(value, original, "Export cannot update current authority or historical receipts");
  }
  const missing = oracleContract("unavailable");
  delete missing.verification.results[0].oracle_evidence;
  const markdown = deliveryReviewMarkdown(parseDeliveryReview(withContract(missing), snapshot.goal_id), copy);
  assert.ok(!markdown.includes("2026-09-01T01:02:03Z") && !markdown.includes("f".repeat(64)), "Missing execution evidence cannot acquire invented time or versions");
  assert.ok(!markdown.includes(copy.contract.verificationState.accepted));
  const replaced = oracleContract("stale");
  replaced.criteria[0].oracle.source_id = "replacement-checker";
  replaced.criteria[0].oracle.source_digest = "1".repeat(64);
  const changed = deliveryReviewMarkdown(parseDeliveryReview(withContract(replaced), snapshot.goal_id), copy);
  const [currentBasis, retainedWitness] = changed.split(`### ${copy.contract.receipt}`);
  assert.ok(currentBasis.includes("replacement-checker") && currentBasis.includes("1".repeat(64)));
  assert.ok(retainedWitness.includes("recovery-checker") && retainedWitness.includes("d".repeat(64)), "A new checker cannot rewrite the saved witness's source");
}
for (const mutation of [
  { ...contract, enabled: "true" }, { ...contract, revision: "7" }, { ...contract, revision: 1.5 },
  { ...contract, digest: "" }, { ...contract, tasks: [{ ...contract.tasks[0], state: "approved" }] },
  { ...contract, status: "approved" }, { ...contract, verification: { status: "passed" } },
]) assert.throws(() => parseDeliveryReview(withContract(mutation), snapshot.goal_id));
assert.throws(() => parseDeliveryReview(withContract(contract), "other-goal"));
assert.throws(() => parseDeliveryReview({ ...withContract(contract), acceptance: { ...acceptance, goal_id: "other-goal", goal_acceptance_contract: contract } }, snapshot.goal_id));
const next = parseDeliveryReview(withContract({ ...contract, revision: 8, digest: "b".repeat(64) }), snapshot.goal_id);
assert.equal(next.acceptance.goal_acceptance_contract.revision, 8);
assert.notEqual(next.acceptance.goal_acceptance_contract.digest, contract.digest);
assert.deepEqual(contract, before, "Rendering must not mutate the supplied contract");
console.log("Goal acceptance contract API/export: off parity, association/verification distinction, source/revision and negative cases passed");

const {goalAcceptanceDisplayStatus, projectGoalAcceptanceCriteria} = await import("../node_modules/.cache/delivery-review/data/goal-acceptance-observation.js");
for (const [observation, expected] of Object.entries({current: "accepted", stale: "stale", failed: "failed", recorded: "unverified", unavailable: "unverified"})) {
  const value = structuredClone(verifiedContract("accepted"));
  value.criteria[0].oracle = {state: observation};
  const before = structuredClone(value);
  assert.equal(goalAcceptanceDisplayStatus(value), expected, "Historical success cannot override current file observations");
  assert.equal(projectGoalAcceptanceCriteria(value)[0].state, expected);
  assert.deepEqual(value, before, "Display freshness cannot mutate canonical acceptance");
}
for (const status of ["stale", "held", "partial"]) {
  const value = verifiedContract(status);
  value.verification.contract_revision = value.revision;
  value.verification.contract_digest = value.digest;
  const before = structuredClone(value);
  assert.deepEqual(projectGoalAcceptanceCriteria(value).map(row => row.state), [status],
    "Equal revisions cannot upgrade the owner's currentness or scope");
  assert.deepEqual(value, before, "Historical evidence stays intact");
}
const current = verifiedContract("accepted");
current.criteria.push({id: "other", description: "Another commitment"});
assert.deepEqual(projectGoalAcceptanceCriteria(current).map(row => row.state), ["accepted", "unverified"]);
current.verification.results.push({criterion_id: "other", passed: false, exit_code: 1});
current.status = "failed";
assert.deepEqual(projectGoalAcceptanceCriteria(current).map(row => row.state), ["accepted", "failed"]);
current.verification.contract_digest = "b".repeat(64);
assert.deepEqual(projectGoalAcceptanceCriteria(current).map(row => row.state), ["stale", "stale"]);
current.verification = null;
assert.deepEqual(projectGoalAcceptanceCriteria(current).map(row => row.state), ["unverified", "unverified"]);
console.log("Acceptance criteria: current work, scope, digest and retained missing criteria passed");
