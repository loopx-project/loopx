import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {resolveTestPython} from "../../../../scripts/test-python.mjs";

const goalId = "research-verification-fixture";
const result = {ref: "artifact:result", sha256: "b".repeat(64)};
const proof = {ref: "receipt:external-verifier", sha256: "c".repeat(64)};
const refs = [result.ref, proof.ref];
const contract = {
  goal_ref: {goal_id: goalId, goal_instance_id: "verification-original-instance"},
  revision: 1, digest: "a".repeat(64), objective: "Read candidate verification", status: "accepted",
  tasks: [], non_goals: [], held_todo_ids: [],
  criteria: [{id: "verify", description: "Inspect the candidate result", oracle: {
    kind: "owner_pinned_oracle", source_id: "fixture-oracle", source_digest: "d".repeat(64),
    input_refs: [], output_refs: refs, state: "current",
  }}],
  verification: {operation_id: "saved-receipt", contract_revision: 1, contract_digest: "a".repeat(64), todo_id: null,
    results: [{criterion_id: "verify", passed: true, exit_code: 0, oracle_evidence: {
      source_id: "fixture-oracle", source_digest: "d".repeat(64), checked_at: "2026-11-21T13:00:00Z",
      input_versions: [], output_versions: [result, proof],
    }}]},
};
const saved = {
  schema_version: "goal_acceptance_observation_projection_v0", goal_id: goalId, read_only: true,
  acceptance_assessed: false, coverage: "partial", missing_sources: [], truncated: false,
  historical_progress: [], acceptance_gaps: [], guards: [], next_action: null, next_action_source: null,
  goal_acceptance_contract: contract,
};
export async function verifyResearchAcceptanceReadback(browser, start) {
  const repoRoot = fileURLToPath(new URL("../../../..", import.meta.url));
  const builder = fileURLToPath(new URL("./research-verification-fixture.py", import.meta.url));
  const fixture = JSON.parse(execFileSync(resolveTestPython({repoRoot}), [builder], {cwd: repoRoot, encoding: "utf8",
    env: {...process.env, PYTHONPATH: repoRoot}}));
  const url = await start("verification", fixture);
  const historyUrl = new URL("/api/chat/explore-results", url);
  historyUrl.search = new URLSearchParams({goal_id: goalId, view: "history"});
  const historyResponse = await fetch(historyUrl);
  assert.equal(historyResponse.status, 200);
  const history = (await historyResponse.json()).items;
  const matched = history.find(row => row.result_id === "matched-candidate");
  assert.deepEqual(matched.yardstick_measurements.map(row => row.value), [9, 5, 4, 8]);
  assert.equal(matched.work_yardstick.source_snapshot, undefined);
  const revision = history.filter(row => row.result_id === "matched-baseline").at(-1);
  assert.deepEqual(revision.measurements.map(row => row.value), [2, 10], "The later measurement exists in history");
  for (const id of ["foreign-candidate", "late-candidate"]) {
    const record = history.find(row => row.result_id === id);
    assert.equal(record.yardstick_measurements, undefined);
    assert.equal(record.work_yardstick.decision.authoritative.metric, "loss");
  }
  const page = await browser.newPage({locale: "en-US", viewport: {width: 1200, height: 900}});
  let mode = "unavailable";
  let mutations = 0;
  let suppliedHistoricalPass = false;
  const reads = [];
  try {
    await page.addInitScript(() => localStorage.setItem("loopx-pw-locale", "en"));
    page.on("request", request => {if (request.method() !== "GET") mutations++;});
    await page.route(/\/status\.json(?:\?|$)/, async route => {
      const response = await route.fetch();
      const data = await response.json();
      const goal = data.run_history?.goals?.find(row => row.id === goalId);
      if (goal) {
        goal.acceptance_observation = saved;
        suppliedHistoricalPass = true;
      }
      await route.fulfill({response, json: data});
    });
    await page.route(/\/api\/chat\/goal-acceptance\?/, async route => {
      const query = new URL(route.request().url()).searchParams;
      assert.equal(query.get("goal_id"), goalId);
      assert.equal(route.request().method(), "GET");
      reads.push(mode);
      if (mode === "unavailable") return route.fulfill({status: 503, json: {ok: false}});
      const live = structuredClone(contract);
      if (mode === "other-instance") live.goal_ref.goal_instance_id = "verification-new-instance";
      if (mode === "unrelated-result") {
        live.criteria[0].oracle.output_refs[0] = "artifact:other-result";
        live.verification.results[0].oracle_evidence.output_versions[0].ref = "artifact:other-result";
      }
      return route.fulfill({json: {ok: true, goal_id: goalId, provider_revision: "fixture-revision",
        goal_acceptance_contract: live}});
    });
    const entry = page.locator(".goal-verification-entry > summary");
    const panel = page.locator(".goal-verification-body");
    const verified = panel.getByText("Authoritative check includes this artifact", {exact: true});
    const retry = panel.getByRole("button", {name: "Retry verifier read", exact: true});
    const tier = panel.locator("dl > div").filter({has: page.getByText("Verified tier", {exact: true})}).locator("dd");
    const unknown = async () => {
      await tier.getByText("Unknown", {exact: true}).waitFor();
      assert.equal(await verified.count(), 0);
      assert.equal(await panel.getByText("Artifact checks passed", {exact: true}).count(), 0);
    };

    const read = async activate => {
      const response = page.waitForResponse(item => new URL(item.url()).pathname === "/api/chat/goal-acceptance");
      await activate();
      await response;
      await page.locator('.goal-verification-body[aria-busy="false"]').waitFor();
    };

    await page.goto(url);
    await entry.waitFor();
    assert(suppliedHistoricalPass, "The case must include a passing historical receipt");
    assert.deepEqual(reads, [], "The collapsed packet must leave the live acceptance service idle");
    await read(() => entry.press("Enter"));
    await retry.waitFor();
    await unknown();
    assert.equal(await panel.getByText(result.sha256, {exact: true}).count(), 1, "Keep the original candidate readable");
    mode = "other-instance";
    await read(() => retry.press("Enter"));
    await retry.waitFor();
    await unknown();
    mode = "current";
    await read(() => retry.press("Enter"));
    await verified.waitFor();
    assert.equal(await tier.textContent(), "L0");
    const selected = panel.getByRole("combobox", {name: "Research record"});
    const fidelity = panel.locator("dl > div").filter({has: page.getByText("Proxy fidelity", {exact: true})}).locator("dd");
    await selected.selectOption({label: "Original matched measurements"});
    assert.equal(await fidelity.textContent(), "Different ordering on the matched pair");
    assert.equal(await panel.getByText("9 → 5 points", {exact: false}).count(), 1);
    assert.equal(await panel.getByText("4 → 8 points", {exact: false}).count(), 1);

    await page.setViewportSize({width: 390, height: 900});
    for (const label of ["Reference from another worker", "Reference received later"]) {
      await selected.selectOption({label});
      assert.equal(await fidelity.textContent(), "Unknown");
      assert.equal(await panel.getByText("9 → 5 points", {exact: false}).count(), 0);
      assert.equal(await verified.count(), 1, "The separate authoritative proof remains available");
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    }
    mode = "unrelated-result";
    await page.goto(url);
    await entry.waitFor();
    await read(() => entry.press("Enter"));
    await panel.getByText("Authoritative receipt for this candidate is unknown", {exact: true}).waitFor();
    await unknown();
    assert.equal(await retry.count(), 0, "A readable check of another result is a scope mismatch");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(reads, ["unavailable", "other-instance", "current", "unrelated-result"]);
    assert.equal(mutations, 0);
    const backend = await (await fetch(new URL("/fixture/readback", url))).json();
    assert(backend.history_reads > 0 && backend.source_unchanged && backend.mutation_attempts === 0);
    console.log("research verification: original pair survives revision; foreign and delayed measurements stay unknown; verifier retry restores proof");
  } finally {
    await page.close();
  }
}
