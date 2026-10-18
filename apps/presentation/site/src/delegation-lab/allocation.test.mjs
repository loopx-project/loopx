import assert from "node:assert/strict";
import { test } from "node:test";
import { canReturn, enumerateBest, fixturePlans, inspectPlan, policies, reviewIsCurrent, reviewPlan, vectorCount } from "./allocation.ts";

const original = fixturePlans[0].map(String), corrected = fixturePlans[1].map(String);
const request = { revision: 2, policy: policies[1] };

test("maxima match the independently specified public case, including no solution", () => {
  // Independent expectations from examples/collaboration-delivery/README.md
  // and REQUIREMENTS.md, not generated from this browser implementation.
  const expected = [
    { quantities: [1, 1, 3, 2, 0, 2], value: 84, cost: 1000 },
    { quantities: [2, 1, 3, 1, 0, 2], value: 83, cost: 990 },
    null,
  ];
  for (let i = 0; i < policies.length; i++) {
    assert.deepEqual(enumerateBest(policies[i]), expected[i]);
  }
  assert.equal(vectorCount, 2400);
});

test("owner correction breaks the old stock allocation; receipt cannot repair it", () => {
  assert.deepEqual(inspectPlan(original, policies[1]).violations, [{ subject: "y", used: 5, limit: 4 }]);
  const review = reviewPlan(request, original);
  assert.equal(review.accepted, false);
  assert.equal(canReturn(request, 1, 2, original, review), false);
  assert.equal(reviewPlan({ revision: 1, policy: policies[0] }, original).accepted, true);
  assert.equal(reviewPlan(request, corrected).accepted, true);
});

test("return needs the current receipt, adopted input and exact artifact check", () => {
  const review = reviewPlan(request, corrected);
  assert.equal(canReturn(request, 2, 2, corrected, review), true);
  assert.equal(canReturn(request, 2, null, corrected, review), false);
  assert.equal(canReturn(request, 1, 2, corrected, review), false);
  assert.equal(canReturn(request, 2, 1, corrected, review), false);
  assert.equal(canReturn(request, 2, 2, corrected, null), false);
  const edited = [...corrected]; edited[0] = "1";
  assert.equal(reviewIsCurrent(request, edited, review), false);
  assert.equal(canReturn(request, 2, 2, edited, review), false);
  assert.equal(reviewIsCurrent({ ...request, revision: 3 }, corrected, review), false);
  assert.equal(canReturn({ ...request, revision: 3 }, 3, 3, corrected, review), false);
  assert.deepEqual(review.draft, corrected, "old review remains a historical snapshot");
});

test("blank, fractional, negative, over-demand and zero-demand quantities fail", () => {
  for (const [index, value] of [[0, ""], [0, "0.5"], [0, "-1"], [0, "5"], [4, "1"], [0, "Infinity"]]) {
    const invalid = [...corrected]; invalid[index] = value;
    assert.equal(inspectPlan(invalid, policies[1]).invalid.length, 1);
    assert.equal(reviewPlan(request, invalid).accepted, false);
  }
  assert.equal(inspectPlan([], policies[0]).invalid.length, 6);
});

test("feasibility is not optimality, and impossible requests do not return success", () => {
  const zero = Array(6).fill("0");
  assert.deepEqual(inspectPlan(zero, policies[0]).violations, []);
  assert.equal(reviewPlan({ revision: 1, policy: policies[0] }, zero).accepted, false);
  const tied = [3, 0, 2, 2, 0, 2].map(String);
  assert.equal(inspectPlan(tied, policies[1]).value, 83);
  assert.deepEqual(inspectPlan(tied, policies[1]).violations, []);
  assert.equal(reviewPlan(request, tied).accepted, false, "equal value must still obey the lexicographic rule");
  const impossible = { revision: 3, policy: policies[2] };
  const review = reviewPlan(impossible, corrected);
  assert.equal(review.best, null);
  assert.equal(review.accepted, false);
  assert.equal(canReturn(impossible, 3, 3, corrected, review), false);
});
