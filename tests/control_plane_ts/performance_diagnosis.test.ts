import assert from "node:assert/strict";
import test from "node:test";
import {planPerformanceDiagnosis as plan, summarizePerformanceProfile as inspect} from "../../loopx/control_plane/capabilities/performance_diagnosis.ts";

const sampled = () => ({$schema: "https://www.speedscope.app/file-format-schema.json",
  shared: {frames: [{name: "caller"}, {name: "work"}, {name: "wait"}]},
  profiles: [{type: "sampled", name: "main", unit: "seconds", startValue: 0, endValue: 1,
    samples: [[0, 1], [0, 0, 2]], weights: [.25, .75]}]});

test("plans preserve literal argv and never claim execution, elevation or readiness", () => {
  const command = ["python", "-m", "example", "a b", "$(not-a-shell)", ""];
  const result = plan({tool: "pyinstrument", command, output_directory: ".local/run", platform: "darwin"});
  assert.deepEqual(result.baseline_argv, command);
  assert.deepEqual(result.profile_argv.slice(-(command.length - 1)), command.slice(1));
  assert.equal(result.execution_performed, false);
  assert.equal(result.tool_readiness_verified, false);
  assert.equal(result.benchmark_qualified, false);
  assert.equal(result.shell, false);
  const spy = plan({tool: "py-spy", command, output_directory: ".local/run", platform: "darwin"});
  assert.ok(spy.profile_argv.includes("--idle"));
  assert.ok(!spy.profile_argv.includes("sudo"));
  assert.ok(spy.limits.some(value => value.includes("privileges")));
});

test("allocation and V8 recipes target the chosen process; already instrumented input is rejected", () => {
  const node = plan({tool: "node-cpu", command: ["node", "worker.ts"], output_directory: ".local/run", platform: "linux"});
  assert.deepEqual(node.profile_argv, ["node", "--cpu-prof", "--cpu-prof-dir=.local/run", "--cpu-prof-name=profile.cpuprofile", "worker.ts"]);
  const heap = plan({tool: "node-heap", command: ["node", "worker.ts"], output_directory: ".local/run", platform: "linux"});
  assert.ok(heap.profile_argv.includes("--heap-prof"));
  const memory = plan({tool: "memray", command: ["python", "-m", "example"], output_directory: ".local/run", platform: "linux"});
  assert.deepEqual(memory.profile_argv.slice(0, 5), ["python", "-m", "memray", "run", "--native"]);
  assert.throws(() => plan({tool: "node-cpu", command: ["node", "--heap-prof", "worker.ts"], output_directory: ".local/run", platform: "linux"}), /already contains/);
  assert.throws(() => plan({tool: "unknown", command: ["python"], output_directory: ".local/run", platform: "linux"}), /tool must/);
  assert.throws(() => plan({tool: "pyinstrument", command: [], output_directory: ".local/run", platform: "linux"}), /executable/);
});

test("sampled self and inclusive weights are distinct, recursion counted once, threads separate", () => {
  const profile = sampled();
  profile.profiles.push({...profile.profiles[0]!, name: "other", samples: [[1]], weights: [.5]});
  const result = inspect({profile});
  assert.equal(result.profiles.length, 2);
  const main = result.profiles[0]!;
  assert.equal(main.observed_weight_ms, 1000);
  assert.equal(main.self_hotspots[0]!.name, "wait");
  assert.equal(main.self_hotspots[0]!.self_ms, 750);
  assert.equal(main.inclusive_hotspots[0]!.name, "caller");
  assert.equal(main.inclusive_hotspots[0]!.inclusive_ms, 1000);
  assert.equal(main.inclusive_hotspots[0]!.self_ms, 0);
  assert.equal(result.profiles[1]!.observed_weight_ms, 500);
  assert.equal(result.root_cause_proven, false);
  assert.equal(result.benchmark_qualified, false);
});

test("evented profiles integrate stack intervals rather than counting opens", () => {
  const profile = {$schema: sampled().$schema, shared: sampled().shared,
    profiles: [{type: "evented", unit: "milliseconds", startValue: 0, endValue: 8,
      events: [{type: "O", frame: 0, at: 1}, {type: "O", frame: 1, at: 3},
        {type: "C", frame: 1, at: 5}, {type: "C", frame: 0, at: 7}]}]};
  const main = inspect({profile}).profiles[0]!;
  assert.equal(main.observed_weight_ms, 8);
  assert.equal(main.stack_weight_ms, 6);
  assert.equal(main.self_hotspots[0]!.self_ms, 4);
  assert.equal(main.inclusive_hotspots[0]!.inclusive_ms, 6);
});

test("shared long names cannot multiply a small capture into an unbounded summary", () => {
  const name = "x".repeat(8192);
  const profile = {$schema: sampled().$schema, shared: {frames: [{name}]},
    profiles: Array.from({length: 65}, () => ({type: "sampled", name: "thread",
      unit: "milliseconds", startValue: 0, endValue: 1, samples: [[0]], weights: [1]}))};
  assert.ok(JSON.stringify(profile).length < 20_000);
  assert.throws(() => inspect({profile}), /summary text exceeds/);
  profile.profiles.length = 1;
  assert.equal(inspect({profile}).profiles[0]!.self_hotspots[0]!.name, name);
});

test("event ordering, balanced exits and temporal units remain required", () => {
  const profile = {$schema: sampled().$schema, shared: sampled().shared,
    profiles: [{type: "evented", unit: "milliseconds", startValue: 0, endValue: 8,
      events: [{type: "O", frame: 0, at: 1}, {type: "C", frame: 0, at: 7}]}]};
  const wrongClose = structuredClone(profile); wrongClose.profiles[0]!.events[1]!.frame = 1;
  assert.throws(() => inspect({profile: wrongClose}), /unbalanced/);
  const unclosed = structuredClone(profile); unclosed.profiles[0]!.events.pop();
  assert.throws(() => inspect({profile: unclosed}), /unclosed/);
  const unordered = structuredClone(profile); unordered.profiles[0]!.events[1]!.at = 0;
  assert.throws(() => inspect({profile: unordered}), /ordered/);
  const allocations = sampled(); allocations.profiles[0]!.unit = "bytes";
  assert.throws(() => inspect({profile: allocations}), /unsupported time unit/);
});

const v8 = () => ({startTime: 100, endTime: 5100,
  nodes: [{id: 10, callFrame: {functionName: "caller"}, children: [20]},
    {id: 20, callFrame: {functionName: "work", url: "worker.ts", lineNumber: 6}}],
  samples: [20, 10], timeDeltas: [3000, 2000]});

test("V8 sparse node ids and microseconds map to real callsites", () => {
  const main = inspect({profile: v8()}).profiles[0]!;
  assert.equal(main.observed_weight_ms, 5);
  assert.equal(main.self_hotspots[0]!.name, "work");
  assert.equal(main.self_hotspots[0]!.line, 7);
  assert.equal(main.inclusive_hotspots[0]!.inclusive_ms, 5);
});

test("malformed, empty, unknown and cyclic observations never become clean evidence", () => {
  assert.throws(() => inspect({profile: {}}), /unsupported profile/);
  const badWeight = sampled(); badWeight.profiles[0]!.weights[0] = -1;
  assert.throws(() => inspect({profile: badWeight}), /nonnegative/);
  const badRef = sampled(); badRef.profiles[0]!.samples[0] = [99];
  assert.throws(() => inspect({profile: badRef}), /unknown frame/);
  const count = sampled(); count.profiles[0]!.weights.pop();
  assert.throws(() => inspect({profile: count}), /counts differ/);
  const tooLong = sampled(); tooLong.profiles[0]!.weights[0] = 10;
  assert.throws(() => inspect({profile: tooLong}), /exceed/);
  const empty = sampled(); empty.profiles[0]!.weights = [0, 0];
  assert.throws(() => inspect({profile: empty}), /positive-duration/);
  assert.throws(() => inspect({profile: sampled(), top: 0}), /top must/);
  const cycle = v8(); cycle.nodes[1]!.children = [10];
  assert.throws(() => inspect({profile: cycle}), /cyclic/);
  const unknown = v8(); unknown.samples[0] = 999;
  assert.throws(() => inspect({profile: unknown}), /unknown V8/);
});
