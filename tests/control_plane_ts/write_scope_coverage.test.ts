import assert from "node:assert/strict";
import test from "node:test";
import {spawnSync} from "node:child_process";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} from "../../loopx/control_plane/effect_runtime_handlers.ts";
import {schedulingWriteScopeAllowed} from "../../loopx/control_plane/work_items/task_lease_acquire_decision.ts";

const cases: [string, string[], boolean][] = [
  ["src/*.ts", ["src/?.ts"], false],
  ["src/[ab].ts", ["src/?.ts"], true],
  ["src/a.ts", ["src/?.ts"], true],
  ["src/b.ts", ["src/[z-a!b].ts"], false],
  ["src/c.ts", ["src/[z-a!b].ts"], true],
  ["src/!.ts", ["src/[z-a!b].ts"], true],
  ["src/[b].ts", ["src/[z-a!b].ts"], false],
  ["src/[ac].ts", ["src/[z-a!b].ts"], true],
  ["src/[z-a!b].ts", ["src/[!b].ts"], true],
  ["src/[!b].ts", ["src/[z-a!b].ts"], true],
  ["src/c.ts", ["src/[!z-a].ts"], true],
  ["src/long.ts", ["src/?.ts"], false],
  ["src/?.ts", ["src/*.ts"], true],
  ["src/*.ts", ["src/[!x]*.ts"], false],
  ["src/[ab].ts", ["src/a.ts", "src/b.ts"], true],
  ["src/[a-c].ts", ["src/[ab].ts", "src/c.ts"], true],
  ["src/[a-c].ts", ["src/a.ts", "src/c.ts"], false],
  ["src/[!a].ts", ["src/?.ts"], true],
  ["src/[!a].ts", ["src/[!b].ts"], false],
  ["src/?.ts", ["src/a.ts", "src/[!a].ts"], true],
  ["src/??.ts", ["src/?.ts"], false],
  ["src/😀?.ts", ["src/??.ts"], true],
  ["src/[😀-🙏].ts", ["src/?.ts"], true],
  ["src/[A-Z].ts", ["src/[a-z].ts"], false],
  ["src/SS.ts", ["src/[ß].ts"], false],
  ["src/**/*.ts", ["src/*.ts"], true],
  ["src/*.ts", ["src/**/*.ts"], false],
  ["src/[[]*.ts", ["src/*.ts"], true],
  ["src/[*].ts", ["src/?.ts"], true],
  ["src/*.ts", ["src/[*].ts"], false],
  ["materials/run/**", ["material*/**"], true],
  ["reports/result.md", ["reports/*.md"], true],
  ["src/a.ts", ["src/"], false],
  ["src/[.ts", ["src/[.ts"], true],
  ["src/[]].ts", ["src/?.ts"], true],
  ["src/[a-z].ts", [], false],
  ["", [], true],
];

for (const [required, allowed, expected] of cases) {
  test(`scope coverage ${required} within ${JSON.stringify(allowed)}`, () => {
    assert.equal(schedulingWriteScopeAllowed(required, allowed), expected);
  });
}

test("ambiguous coverage remains bounded and never grants unproved authority", {timeout: 5_000}, () => {
  const required = `src/${"?".repeat(24)}`;
  const allowed = Array.from({length: 24}, (_, index) => `src/${"?".repeat(index)}a${"?".repeat(23 - index)}`);
  // An all-b filename is outside every grant. Subset construction must not
  // become an unbounded admission task while looking for that counterexample.
  assert.equal(schedulingWriteScopeAllowed(required, allowed), false);
  assert.equal(schedulingWriteScopeAllowed(required, [required, ...allowed]), true);
});

test("registered child admission checks the paths denoted by a required glob", async () => {
  const handlers = createEffectRuntimeHandlers({fingerprint: "synthetic", requestShutdown() {}});
  const lane = (id: string, scopes: string[]): JsonObject => ({
    todo_id: id, dependency_todo_id: id, task_class: "advancement_task", done: false,
    status: "open", resume_when: null, claimed_by: null, excluded_agents: [], task_domain: "code",
    task_repository: null, required_write_scopes: scopes, required_capabilities: [], target_capabilities: [],
    objective: "Implement synthetic change", action_kind: scopes.length ? "implement" : "inspect",
    continuation_policy: null, target_key: null, validation_label: null,
  });
  for (const [required, expected] of [["src/*.ts", false], ["src/[ab].ts", true]] as const) {
    const result = await dispatchEffectRuntimeMethod(handlers, "quota.task_orchestration.admit", {
      agent_id: "agent-a", registered_agents: ["agent-a"], goal_repository: null,
      goal_write_scope: ["src/?.ts"], allowed_domains: null, max_children: 2, user_items: [],
      available_capabilities: ["subagent_spawn"], items: [lane("todo_primary", []), lane("todo_child", [required])],
    }) as JsonObject | null;
    assert.equal(result !== null, expected, required);
    if (result) assert.equal((result.eligible_child_lanes as JsonObject[])[0].todo_id, "todo_child");
  }
});

test("finite required languages agree with Python fnmatch's independent oracle", () => {
  const probe = spawnSync(resolveTestPython(), ["-c", `
import fnmatch, itertools, json
required = ['[ab]', '[a-c]', '[a-b-c]', '[a--c]', '[a-b-c-d]', '[--a]',
            '[]a]', '[[]', '[*]', '[?]', '[z-a]', '[😀-😃]', '[ab][bc]']
grants = ['?', '*', '[!a]', '[!b]', '[a-c]', '[a-b-c]', '[a--c]', '[--a]',
          '[z-a]', 'a*', '*b', '[ab][bc]', '[😀-🙏]', '[*?]', '[]a]']
alphabet = [chr(i) for i in range(128)] + [chr(i) for i in range(0x1f600, 0x1f604)]
names = [''] + alphabet + [''.join(p) for p in itertools.product('abc', repeat=2)]
rows = []
for r in required:
    language = [s for s in names if fnmatch.fnmatchcase(s, r)]
    for allowed in [[g] for g in grants] + [['a', '[!a]'], ['[ab]', 'c']]:
        rows.append([r, allowed, all(any(fnmatch.fnmatchcase(s, g) for g in allowed) for s in language)])
print(json.dumps(rows))
`], {encoding: "utf8", timeout: 10_000});
  assert.equal(probe.status, 0, probe.stderr);
  for (const [required, allowed, expected] of JSON.parse(probe.stdout) as [string, string[], boolean][]) {
    assert.equal(schedulingWriteScopeAllowed(required, allowed), expected, `${required} in ${allowed.join(",")}`);
  }
});

test("coverage transport batches decisions and rejects malformed facts", async () => {
  const handlers = createEffectRuntimeHandlers({fingerprint: "synthetic", requestShutdown() {}});
  const result = await dispatchEffectRuntimeMethod(handlers, "quota.write_scope_coverage", {
    checks: cases.map(([required, allowed]) => ({required, allowed})),
  });
  assert.deepEqual(result, {covered: cases.map(([, , expected]) => expected)});
  for (const params of [{checks: null}, {checks: [{required: 1, allowed: []}]},
    {checks: [{required: "src/*.ts", allowed: [null]}]}]) {
    await assert.rejects(dispatchEffectRuntimeMethod(handlers, "quota.write_scope_coverage", params));
  }
});

test("literal and singleton-glob authorization preserve Python character-class normalization", () => {
  const probe = spawnSync(resolveTestPython(), ["-c", `
import fnmatch, itertools, json
classes = [''.join(p) for size in range(1, 6) for p in itertools.product('!az-', repeat=size)]
classes += ['z-a!b', '!z-a!b', 'z-a^b', 'z-a[b', 'z-a]b', 'z-a\\\\b',
            'a-b-c-d', '😀-a!b', '!😀-a', '&~|', '[]', '[]!']
names = list('abcz!-^]&|~') + [chr(92), '😀']
print(json.dumps([[body, [fnmatch.fnmatchcase('src/' + name + '.ts', 'src/[' + body + '].ts')
                         for name in names]] for body in classes]))
`], {encoding: "utf8", timeout: 10_000});
  assert.equal(probe.status, 0, probe.stderr);
  const names = [..."abcz!-^]&|~", "\\", "😀"];
  for (const [body, expected] of JSON.parse(probe.stdout) as [string, boolean[]][]) {
    const allowed = [`src/[${body}].ts`];
    for (const [index, name] of names.entries()) {
      assert.equal(schedulingWriteScopeAllowed(`src/${name}.ts`, allowed), expected[index],
        `${name} within ${allowed[0]}`);
      // A leading ! denotes negation rather than a singleton class.
      if (name === "!") continue;
      const singleton = `[${name}]`;
      assert.equal(schedulingWriteScopeAllowed(`src/${singleton}.ts`, allowed), expected[index],
        `${singleton} within ${allowed[0]}`);
    }
  }
});

test("star coverage never admits a counterexample from an independent bounded path set", () => {
  const probe = spawnSync(resolveTestPython(), ["-c", `
import fnmatch, itertools, json
patterns = ['*', '?', 'a*', '*a', '*b*', '?*', '*?*', '[ab]*', '[!a]*',
            'a*b', 'a?b', '*/*', '**/a', '[ab][ab]*', '*a*b*', '*b*a*']
names = [''.join(p) for size in range(5) for p in itertools.product('ab/😀', repeat=size)]
print(json.dumps([[r, [a], next((s for s in names if fnmatch.fnmatchcase(s, r)
    and not fnmatch.fnmatchcase(s, a)), None)] for r in patterns for a in patterns]))
`], {encoding: "utf8", timeout: 10_000});
  assert.equal(probe.status, 0, probe.stderr);
  for (const [required, allowed, witness] of JSON.parse(probe.stdout) as [string, string[], string | null][]) {
    if (witness !== null) assert.equal(schedulingWriteScopeAllowed(required, allowed), false,
      `${JSON.stringify(witness)} escapes ${required} in ${allowed.join(",")}`);
  }
});
