import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, readdir, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {agentCapabilityMemory} from "../../loopx/control_plane/agents/capability_memory.ts";
import {evaluateCapabilityGate, projectCapabilityAvailability} from "../../loopx/control_plane/agents/capability_gate.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

async function fixture(t: {after(fn: () => Promise<void>): void}) {
  const root = await mkdtemp(join(tmpdir(), "agent-capabilities-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const request: JsonObject = {schema_version: "agent_runtime_capability_request_v0", runtime_root: root, registry: "fixture-registry", goal_id: "goal",
    agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"], execute: false};
  return {root, call: (fields: JsonObject = {}) => agentCapabilityMemory({...request, ...fields})};
}

test("missing observations are read-only; previews do not create memory", async t => {
  const {root, call} = await fixture(t);
  assert.deepEqual((await call()).available, []);
  const preview = await call({available: ["network"]});
  assert.equal(preview.written, false);
  assert.deepEqual(preview.proposed, {available: ["network"], unavailable: []});
  assert.deepEqual(await readdir(root), []);
});

test("observations persist idempotently and never promote authority or capability enablement", async t => {
  const {root, call} = await fixture(t);
  const supplied = ["network", "credentials", "production_access", "material_lifecycle", "custom_permission"];
  const first = await call({available: supplied, execute: true});
  assert.deepEqual(first.available, ["network"]);
  assert.deepEqual(first.invocation_only, supplied.slice(1).sort());
  assert.equal(first.durable_grant_written, false);
  const path = join(root, "agent-runtime-capabilities", (await readdir(join(root, "agent-runtime-capabilities")))[0]!);
  const bytes = await readFile(path, "utf8");
  assert.equal((await call({available: ["network"], execute: true})).written, false);
  assert.equal(await readFile(path, "utf8"), bytes);
  assert.deepEqual((await call()).available, ["network"]);
  for (const fields of [{agent_id: "agent-b"}, {goal_id: "other"}, {registry: "other"}]) {
    assert.deepEqual((await call(fields)).available, []);
  }
});

test("local failures override inheritance until recovery or explicit forget", async t => {
  const {call} = await fixture(t);
  const agent = await call({unavailable: ["network"], execute: true});
  const resolve = (state: JsonObject, runtime: string[] = []) => projectCapabilityAvailability({
    goal: ["network", "credentials"], runtime, agent: state,
  });
  assert.deepEqual(resolve(agent).effective, ["credentials"]);
  assert.deepEqual(resolve(agent, ["network"]).effective, ["network", "credentials"]);
  const recovered = await call({available: ["network"], execute: true});
  assert.deepEqual(recovered.unavailable, []);
  const cleared = await call({forget: ["network"], execute: true});
  assert.deepEqual(cleared.available, []);
  assert.deepEqual(resolve(cleared).effective, ["network", "credentials"]);
});

test("concurrent observations merge under the existing mutation lock", async t => {
  const {call} = await fixture(t);
  await Promise.all(["network", "cli_bridge", "worker_bridge", "external_evidence_poll"].map(c =>
    call({available: [c], execute: true})));
  assert.deepEqual((await call()).available, ["cli_bridge", "external_evidence_poll", "network", "worker_bridge"]);
});

test("corruption, foreign scopes and invalid updates fail without overwriting evidence", async t => {
  const {root, call} = await fixture(t);
  await assert.rejects(call({schema_version: "unknown"}), /schema mismatch/);
  await assert.rejects(call({agent_id: "unknown", available: ["network"], execute: true}), /registered/);
  await assert.rejects(call({available: ["network"], unavailable: ["network"], execute: true}), /contradict/);
  await assert.rejects(call({unavailable: ["credentials"], execute: true}), /observable/);
  assert.deepEqual(await readdir(root), []);
  await call({available: ["network"], execute: true});
  const dir = join(root, "agent-runtime-capabilities");
  const path = join(dir, (await readdir(dir))[0]!);
  const valid = JSON.parse(await readFile(path, "utf8"));
  for (const corrupt of ["{", JSON.stringify({...valid, agent_id: "agent-b"}),
    JSON.stringify({...valid, observations: {credentials: "available"}})]) {
    await writeFile(path, corrupt);
    await assert.rejects(call({available: ["cli_bridge"], execute: true}));
    assert.equal(await readFile(path, "utf8"), corrupt);
  }
});

test("legacy global-registry alias merges into canonical source_registry and preserve_unavailable honors revocations", async t => {
  const {call} = await fixture(t);
  await call({registry: "global-registry", available: ["network"], unavailable: ["external_evidence_poll"], execute: true});
  const inherited = await call({registry: "source-registry", legacy_registries: ["global-registry"]});
  assert.deepEqual(inherited.available, ["network"]);
  assert.deepEqual(inherited.unavailable, ["external_evidence_poll"]);

  const passive = await call({
    registry: "source-registry",
    legacy_registries: ["global-registry"],
    available: ["external_evidence_poll", "cli_bridge"],
    preserve_unavailable: true,
    execute: true,
  });
  assert.equal(passive.written, true);
  assert.deepEqual(passive.available, ["cli_bridge", "network"]);
  assert.deepEqual(passive.unavailable, ["external_evidence_poll"]);

  const canonicalRead = await call({registry: "source-registry"});
  assert.deepEqual(canonicalRead.available, ["cli_bridge", "network"]);
  assert.deepEqual(canonicalRead.unavailable, ["external_evidence_poll"]);

  const explicit = await call({
    registry: "source-registry",
    available: ["external_evidence_poll"],
    execute: true,
  });
  assert.deepEqual(explicit.available, ["cli_bridge", "external_evidence_poll", "network"]);
  assert.deepEqual(explicit.unavailable, []);
});

test("auto-probed host capabilities are effective by default and yield to explicit unavailable revocations", () => {
  const fresh = evaluateCapabilityGate({
    schema_version: "capability_gate_request_v0",
    operation: "availability",
    goal: [],
    runtime: [],
    probed: ["external_evidence_poll", "credentials"],
    agent: {},
  }).result as Record<string, unknown>;
  assert.deepEqual(fresh.probed, ["external_evidence_poll"]);
  assert.deepEqual(fresh.runtime_available, ["external_evidence_poll"]);
  assert.deepEqual(fresh.effective, ["external_evidence_poll"]);

  const revoked = evaluateCapabilityGate({
    schema_version: "capability_gate_request_v0",
    operation: "availability",
    goal: ["external_evidence_poll"],
    runtime: [],
    probed: ["external_evidence_poll"],
    agent: {available: [], unavailable: ["external_evidence_poll"]},
  }).result as Record<string, unknown>;
  assert.deepEqual(revoked.probed, ["external_evidence_poll"]);
  assert.deepEqual(revoked.runtime_available, []);
  assert.deepEqual(revoked.effective, []);

  const recovered = evaluateCapabilityGate({
    schema_version: "capability_gate_request_v0",
    operation: "availability",
    goal: [],
    runtime: ["external_evidence_poll"],
    probed: ["external_evidence_poll"],
    agent: {available: [], unavailable: ["external_evidence_poll"]},
  }).result as Record<string, unknown>;
  assert.deepEqual(recovered.effective, ["external_evidence_poll"]);
});
