import assert from "node:assert/strict";
import {test} from "node:test";
import {renderQualificationSummary, type JobEvidence, type QualificationSummary} from "./qualification_summary.ts";

const job = (name: string, conclusion: string | null = "failure", overrides: Partial<JobEvidence> = {}): JobEvidence => ({
  id: 10, run_id: 42, run_attempt: 2, name, status: "completed", conclusion,
  steps: [{name: "Run tests", conclusion: "failure"}], ...overrides,
});
const input = (overrides: Partial<QualificationSummary> = {}): QualificationSummary => ({
  repository: "example/project", serverUrl: "https://github.com", runId: 42, runAttempt: 2,
  sourceSha: "a".repeat(40), checkoutSha: "b".repeat(40), baseSha: "c".repeat(40),
  gateResult: "failure", needs: {changes: {result: "success", outputs: {change_kind: "full"}},
    pytest: {result: "failure"}}, jobs: [], ...overrides,
});

test("shows failed steps ahead of downstream rollups without inventing a cause", () => {
  const result = renderQualificationSummary(input({jobs: [
    job("pytest", "failure", {id: 11}), job("test-shard (2)"),
    job("checks", "failure", {id: 12}), job("test-shard (3)", "cancelled", {id: 13}),
  ]}));
  assert.match(result, /Gate step: failure/);
  assert.ok(result.indexOf("test-shard") < result.indexOf("Aggregate failures"));
  assert.match(result, /Run tests/);
  assert.match(result, /https:\/\/github.com\/example\/project\/actions\/runs\/42\/job\/10/);
  assert.match(result, /Aggregate failures[\s\S]*pytest/);
  assert.match(result, /cancelled/);
  for (const sha of ["a", "b", "c"]) assert.ok(result.includes(sha.repeat(40)));
  assert.doesNotMatch(result, /inherited|flaky|safe to merge/i);
});

test("coverage or aggregation failures remain visible even without a failing test job", () => {
  const result = renderQualificationSummary(input({jobs: [job("pytest", "failure", {
    steps: [{name: "Combine complete coverage", conclusion: "failure"}],
  })]}));
  assert.match(result, /Aggregate failures[\s\S]*Combine complete coverage/);
  assert.match(result, /Gate step: failure/);
});

test("exempt checks and unavailable evidence cannot become passing test claims", () => {
  const docs = renderQualificationSummary(input({gateResult: "success", needs: {
    changes: {result: "success", outputs: {change_kind: "docs"}}, pytest: {result: "skipped"},
  }, jobs: [job("test-shard", "skipped")]}));
  assert.match(docs, /Gate step: success/);
  assert.match(docs, /pytest \| skipped/);
  assert.match(docs, /Skipped jobs are not passing test evidence/);
  const missing = renderQualificationSummary(input({jobs: null}));
  assert.match(missing, /Job evidence unavailable/);
  assert.match(missing, /Gate step: failure/);
  assert.doesNotMatch(missing, /all.*pass/i);
});

test("does not mix earlier attempts or other runs into the current attempt", () => {
  const result = renderQualificationSummary(input({jobs: [
    job("old green", "success", {run_attempt: 1}),
    job("unrelated workflow", "success", {run_id: 41}), job("current failure"),
  ]}));
  assert.doesNotMatch(result, /old green|unrelated workflow/);
  assert.match(result, /Excluded 2 jobs from another run or attempt/);
  assert.match(result, /current failure/);
});

test("reports unfinished jobs without calling them assertion failures", () => {
  const result = renderQualificationSummary(input({jobs: [job("waiting", null, {
    status: "queued", steps: [],
  })]}));
  assert.match(result, /Incomplete jobs[\s\S]*waiting[\s\S]*queued/);
  assert.doesNotMatch(result, /Failing jobs/);
});

test("renders metadata as bounded text and constructs links from trusted run identity", () => {
  const result = renderQualificationSummary(input({jobs: [job("<img src=x>\n| [click](javascript:x) " + "a".repeat(400), "failure", {
    steps: [{name: "<script>\n| oops", conclusion: "failure"}],
  })]}));
  assert.doesNotMatch(result, /<img|<script>|\n\| oops|\]\(javascript:/);
  assert.match(result, /&lt;img/);
  assert.ok(result.length < 2000);
});
