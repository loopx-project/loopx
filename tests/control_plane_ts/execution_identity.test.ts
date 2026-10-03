import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {readCodexExecutionIdentity, matchExecutionDeclaration} from "../../loopx/control_plane/runtime/execution_identity.ts";

const record = (type: string, payload: Record<string, unknown>) => JSON.stringify({type, payload});
const meta = (id = "current") => record("session_meta", {id, model_provider: "openai"});
const start = (turn_id = "new") => record("event_msg", {type: "task_started", turn_id});
const context = (turn_id = "new", model = "example-model-2", effort: unknown = "xhigh") =>
  record("turn_context", {turn_id, model, effort});
async function fixture(t: test.TestContext, lines: string[]) {
  const home = await mkdtemp(join(tmpdir(), "loopx-execution-identity-"));
  t.after(() => rm(home, {recursive: true, force: true}));
  const path = join(home, "current.jsonl");
  await writeFile(path, lines.join("\n") + "\n");
  return {home, path, thread_id: "current"};
}

test("current Turn supersedes old model context without reading preferences", async t => {
  const input = await fixture(t, [meta(), start("old"), context("old", "example-model-1", "low"),
    record("event_msg", {type: "task_complete", turn_id: "old"}), start(), context(),
    record("response_item", {text: "private content and endpoint https://private.invalid"})]);
  const before = await readFile(input.path);
  const observed = await readCodexExecutionIdentity(input);
  assert.equal(observed.status, "runtime_reported");
  if (observed.status !== "runtime_reported") assert.fail();
  assert.equal(observed.model, "example-model-2");
  assert.equal(observed.provider, "OpenAI");
  assert.equal(observed.reasoning_effort, "xhigh");
  assert.match(observed.observation_id, /^[a-f0-9]{64}$/);
  assert.equal(JSON.stringify(observed).includes("private"), false);
  assert.deepEqual(await readFile(input.path), before);
  await writeFile(input.path, before.toString() + context() + "\n");
  assert.deepEqual(await readCodexExecutionIdentity(input), observed);
  await writeFile(input.path, before.toString() + context("new", "example-model-3", "high") + "\n");
  assert.notDeepEqual(await readCodexExecutionIdentity(input), observed);
});

for (const [name, lines, reason] of [
  ["wrong thread", [meta("other"), start(), context()], "session_identity_mismatch"],
  ["new Turn lacks context", [meta(), start("old"), context("old"), start()], "turn_identity_unavailable"],
  ["other Turn context", [meta(), start(), context("other")], "turn_identity_unavailable"],
  ["finished Turn", [meta(), start(), context(), record("event_msg", {type: "task_complete", turn_id: "new"})], "no_active_turn"],
  ["router model", [meta(), start(), context("new", "router/model")], "model_not_publicly_identified"],
  ["custom provider", [record("session_meta", {id: "current", model_provider: "private-gateway"}), start(), context()], "provider_not_publicly_identified"],
  ["inherited provider name", [record("session_meta", {id: "current", model_provider: "__proto__"}), start(), context()], "provider_not_publicly_identified"],
  ["missing model", [meta(), start(), record("turn_context", {turn_id: "new", effort: "high"})], "model_not_publicly_identified"],
  ["malformed metadata", [meta(), start(), "{broken"], "host_record_unavailable"],
] as [string, string[], string][]) {
  test(`${name} remains unknown instead of using old identity`, async t => {
    assert.deepEqual(await readCodexExecutionIdentity(await fixture(t, lines)), {status: "unavailable", reason});
  });
}

test("a Turn's recorded provider supersedes the opening provider", async t => {
  const observed = await readCodexExecutionIdentity(await fixture(t, [meta(), start(),
    record("turn_context", {turn_id: "new", model: "example-model-2", effort: "high", model_provider: "anthropic"})]));
  assert.equal(observed.status, "runtime_reported");
  if (observed.status === "runtime_reported") assert.equal(observed.provider, "Anthropic");
});

test("missing effort remains null; source stays in its selected home", async t => {
  const input = await fixture(t, [meta(), start(), context("new", "example-model-2", null)]);
  const observed = await readCodexExecutionIdentity(input);
  assert.equal(observed.status, "runtime_reported");
  if (observed.status === "runtime_reported") assert.equal(observed.reasoning_effort, null);
  const other = await fixture(t, [meta(), start(), context()]);
  assert.deepEqual(await readCodexExecutionIdentity({...input, home: other.home}),
    {status: "unavailable", reason: "source_outside_home"});
});

test("large metadata header is framed and a partial append is not parsed as identity", async t => {
  const input = await fixture(t, [record("session_meta", {id: "current", model_provider: "openai", base_instructions: "x".repeat(70000)}), start(), context()]);
  assert.equal((await readCodexExecutionIdentity(input)).status, "runtime_reported");
  const before = await readFile(input.path);
  await writeFile(input.path, before.toString() + '{"type":"turn_context","payload":');
  assert.equal((await readCodexExecutionIdentity(input)).status, "runtime_reported");
});

test("a long Turn retains recorded identity without inventing activity beyond the read budget", async t => {
  const input = await fixture(t, [meta(), start(), record("response_item", {text: "x".repeat(9 * 1024 * 1024)}), context()]);
  const observed = await readCodexExecutionIdentity(input);
  assert.equal(observed.status, "runtime_reported");
  if (observed.status === "runtime_reported") {
    assert.equal(observed.model, "example-model-2");
    assert.equal(observed.active_turn_verified, false);
  }
});

test("declarations cannot manufacture evidence or alter a packet while keeping its id", () => {
  const observed = {status: "runtime_reported", model: "example-model-2", provider: "OpenAI", reasoning_effort: "xhigh", observation_id: "a".repeat(64)};
  const declaration = {actor_kind: "model_agent", declaration_source: "runtime_reported", declared_model: "example-model-2",
    declared_provider: "OpenAI", declared_reasoning_effort: "xhigh", execution_observation_id: observed.observation_id};
  assert.deepEqual(matchExecutionDeclaration({observation: observed, declaration, current: observed}), {errors: []});
  assert.deepEqual(matchExecutionDeclaration({declaration}), {errors: ["runtime_observation_missing"]});
  assert.ok(matchExecutionDeclaration({observation: observed, declaration, current: {status: "unavailable"}}).errors.includes("runtime_observation_changed"));
  assert.ok(matchExecutionDeclaration({observation: {...observed, model: "Old Model"}, declaration, current: observed}).errors.includes("runtime_observation_changed"));
});
