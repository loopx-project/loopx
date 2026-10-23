import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { createInterface } from "node:readline";
import { resolveTestPython } from "../../scripts/test-python.mjs";
import { outputDir, repoRoot } from "./fixture.mjs";
import { fetchWorkspaceBackend, openWorkspacePage } from "./scenario-context.mjs";

const GOAL = "research-monitor";
const AGENT = "research-agent";

// A disposable registry, Explore log and real Chat configuration owner. Only the
// Goal configuration routes reach it; the rest of the workspace stays synthetic.
async function startConfigurationOwner() {
  const root = await mkdtemp(resolve(tmpdir(), "loopx-explore-composition-"));
  const env = { ...process.env, TMPDIR: root, TEMP: root, TMP: root, LOOPX_USAGE_PING: "0" };
  const child = spawn(resolveTestPython(), ["-u", "-c", `
import json, runpy, signal, subprocess, sys
from pathlib import Path
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.effect_runtime import restart_effect_runtime
root = Path(sys.argv[1]); goal, agent = sys.argv[2], sys.argv[3]
repo, runtime = root / "repo", root / "runtime"
registry = repo / ".loopx" / "registry.json"
registry.parent.mkdir(parents=True)
(repo / "ACTIVE_GOAL_STATE.md").write_text('---\\nstatus: active\\nowner_mode: goal\\nobjective: "Synthetic research editor."\\n'
    'updated_at: 2026-10-11T00:00:00Z\\n---\\n\\n# Synthetic research\\n\\n## Objective\\n\\nSynthetic research editor.\\n\\n'
    '## Next Action\\n\\nInspect the current evidence.\\n\\n## Agent Todo\\n\\n- [ ] Observe the synthetic fixture.\\n'
    f'  <!-- loopx:todo todo_id=todo_monitor status=open task_class=continuous_monitor claimed_by={agent} '
    'target_key=synthetic-monitor cadence=1d next_due_at=2999-01-01T00%3A00%3A00Z -->\\n')
registry.write_text(json.dumps({"schema_version": "0.1", "common_runtime_root": str(runtime), "goals": [{
    "id": goal, "repo": str(repo), "state_file": "ACTIVE_GOAL_STATE.md", "status": "active", "domain": "research-fixture",
    "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"}, "authority_sources": [],
    "quota": {"compute": 1.0, "window_hours": 24, "allowed_slots": 20},
    "coordination": {"agent_model": "peer_v1", "registered_agents": [agent]},
    "spawn_policy": {"explore_harness": {"enabled": False}}}]}))
# Finish authoring this disposable source before ordinary status/configuration
# reads. Markdown alone deliberately cannot supply canonical work authority.
fixtures = runpy.run_path('tests/control_plane/canonical_authority_fixture.py')
fixtures['initialize_canonical_source_fixture'](
    runtime, goal, state_path=repo / 'ACTIVE_GOAL_STATE.md', provider='file')
base = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry), "--runtime-root", str(runtime)]
def cli(*args):
    result = subprocess.run(base + list(args), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)
def observation(node, target=None):
    return {"schema_version": "typed_research_observation_v0", "explore_node_id": node,
        "progress": {"schema_version": "typed_progress_observation_v0", "work_item_id": f"todo_{node}",
            "result_class": "exploration_exhausted", "coverage_scope_id": f"scope-{node}", "coverage_complete": True,
            "evidence_ids": [f"ev-{node}"]},
        "closure_basis": {"schema_version": "research_closure_basis_v0", "disposition": "bounded",
            "constraints": [{"kind": "invariant", "id": "boundary", "role": "decisive"}], "evidence_ids": [f"ev-{node}"]},
        "composition_candidates": [{"basis": "explicit", "target_node_id": target, "interaction_kind": "state_interference",
            "evidence_ids": [f"ev-{node}", f"ev-{target}"]}] if target else []}
try:
    for name in ["a", "b", "joint"]:
        cli("explore", "node", "--goal-id", goal, "--node-id", name, "--title", f"Synthetic {name}",
            "--kind", "experiment" if name == "joint" else "hypothesis", "--status", "open" if name == "joint" else "resolved")
    for name, target in [("b", None), ("a", "b")]:
        path = root / f"obs-{name}.json"; path.write_text(json.dumps(observation(name, target)))
        cli("explore", "observe", "--goal-id", goal, "--observation-json", str(path))
    for name in ["a", "b"]:
        cli("explore", "edge", "--goal-id", goal, "--from", "joint", "--to", name, "--type", "depends_on")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root, server.verbose = registry, runtime, False
    server.runtime_root_override, server.selected_goal_id, server.scan_roots, server.limit = runtime, goal, [], 10
    print(json.dumps({"port": server.server_address[1], "registry": str(registry), "runtime": str(runtime)}), flush=True)
    def terminate(*_): raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    server.serve_forever()
finally:
    restart_effect_runtime()
`, root, GOAL, AGENT], { cwd: repoRoot, env, stdio: ["ignore", "pipe", "pipe"] });
  let diagnostic = "";
  child.stderr.on("data", (chunk) => { diagnostic = (diagnostic + chunk).slice(-3000); });
  const lines = createInterface({ input: child.stdout });
  const close = async () => {
    lines.close();
    if (child.exitCode === null && child.signalCode === null) {
      const exited = new Promise((accept) => child.once("exit", accept));
      child.kill("SIGTERM");
      await exited;
    }
    await rm(root, { recursive: true, force: true });
  };
  try {
    const ready = await new Promise((accept, reject) => {
      const timeout = setTimeout(() => reject(new Error(`Configuration owner startup timed out: ${diagnostic}`)), 120_000);
      child.once("error", (error) => { clearTimeout(timeout); reject(error); });
      child.once("exit", () => { clearTimeout(timeout); reject(new Error(`Configuration owner exited: ${diagnostic}`)); });
      lines.once("line", (line) => { clearTimeout(timeout); accept(JSON.parse(line)); });
    });
    // Independent readback uses the shipped CLI against the same registry, never the HTTP owner.
    const cli = (...args) => {
      const result = spawnSync(resolveTestPython(), ["-m", "loopx.cli", "--format", "json", "--registry", ready.registry,
        "--runtime-root", ready.runtime, ...args], { cwd: repoRoot, env, encoding: "utf8" });
      return { exit: result.status, payload: JSON.parse(result.stdout || "{}") };
    };
    return { ...ready, url: `http://127.0.0.1:${ready.port}`, cli, close };
  } catch (error) { await close(); throw error; }
}

// Read-only projections; no Turn is admitted, so disabling later is not masked by a pinned guard.
function researchReadback(owner) {
  const summary = owner.cli("explore", "summary", "--goal-id", GOAL, "--agent-id", AGENT);
  const status = owner.cli("status", "--goal-id", GOAL, "--agent-id", AGENT);
  assert.equal(summary.exit, 0, JSON.stringify(summary.payload).slice(0, 400));
  assert.equal(status.exit, 0, JSON.stringify(status.payload).slice(0, 400));
  const item = status.payload.attention_queue.items.find((entry) => entry.goal_id === GOAL);
  return {
    summaryFrontier: summary.payload.research_execution_frontier,
    statusFrontier: item?.project_asset?.bounded_research_frontier,
    obligation: item?.project_asset?.autonomous_replan_obligation,
  };
}

async function sourcePolicy(owner) {
  return JSON.parse(await readFile(owner.registry, "utf8")).goals[0].spawn_policy.explore_harness;
}

export const exploreCompositionOwnerScenario = {
  id: "explore-composition-owner",
  async run({ browser, collectCoverage, url }) {
    const owner = await startConfigurationOwner();
    const ownerResponses = [];
    let context;
    try {
      const off = researchReadback(owner);
      assert.equal(off.summaryFrontier, undefined, "Inactive policy exposed a live research frontier");
      assert.equal(off.obligation, undefined, "Inactive policy admitted a research obligation");

      context = await openWorkspacePage(browser, url, {
        collectCoverage,
        beforeGoto: async (_api, page) => {
          await page.route("**/api/chat/goal-configuration**", async (route) => {
            const target = new URL(route.request().url());
            if (!target.pathname.startsWith("/api/chat/goal-configuration")) { await route.fallback(); return; }
            const response = await fetchWorkspaceBackend(route, owner.url);
            ownerResponses.push({ method: route.request().method(), path: target.pathname, status: response.status() });
            await route.fulfill({ response });
          });
        },
      });
      const { page, errors } = context;
      await page.getByRole("button", { name: "设置", exact: true }).click();
      await page.locator(".personal-settings-tabs").getByRole("button", { name: "能力中心", exact: true }).click();
      await page.getByRole("radio", { name: "Goal 设置", exact: true }).check();
      await page.getByRole("combobox", { name: "目标 Goal", exact: true }).selectOption(GOAL);
      const catalog = page.getByRole("navigation", { name: "Goal 能力目录" });
      const openExplore = async () => {
        await catalog.getByRole("button", { name: /探索 Harness/ }).click();
        return {
          mode: page.getByRole("combobox", { name: /^探索模式/u }),
          composition: page.getByRole("combobox", { name: /^组合策略/u }),
          scope: page.getByLabel(/^研究覆盖范围/u),
        };
      };
      const previewButton = page.getByRole("button", { name: "预览变更", exact: true });
      const previewReady = page.getByText("锁定 revision 的变更预览", { exact: true });
      const applyPreview = () => Promise.all([
        page.waitForResponse((response) => new URL(response.url()).pathname === "/api/chat/goal-configuration/apply"),
        page.getByRole("button", { name: "应用此预览", exact: true }).click(),
      ]);
      const alert = page.locator(".personal-machine-error[role=alert]");
      let fields = await openExplore();

      // Refusal 1: the real owner rejects an unsafe scope at preview; nothing is written.
      const untouched = await readFile(owner.registry);
      await fields.mode.selectOption("planning");
      await fields.composition.selectOption("explicit_only");
      await fields.scope.fill("joint scope/../x");
      await previewButton.click();
      await alert.waitFor();
      assert.match(await alert.innerText(), /opaque public-safe identifier/u);
      assert.deepEqual(await readFile(owner.registry), untouched, "A refused preview wrote the registry");

      // Correction, then refusal 2: an out-of-band change makes the locked preview stale.
      await fields.scope.fill("joint-scope");
      await previewButton.click();
      await previewReady.waitFor();
      assert.equal((await alert.count()), 0, "Correction kept the refused preview error");
      const concurrent = owner.cli("configure-goal", "--goal-id", GOAL, "--explore-harness-enabled", "--explore-graph-enabled", "--execute");
      assert.equal(concurrent.exit, 0, JSON.stringify(concurrent.payload).slice(0, 400));
      const beforeStaleApply = await readFile(owner.registry);
      await applyPreview();
      await alert.waitFor();
      assert.deepEqual(await readFile(owner.registry), beforeStaleApply, "A stale apply wrote the registry");
      assert.notEqual((await sourcePolicy(owner)).composition_mode, "explicit_only");
      assert.equal(researchReadback(owner).summaryFrontier, undefined, "A refused apply activated the policy");
      await page.screenshot({ path: resolve(outputDir, "explore-composition-owner-refused.png"), animations: "disabled" });

      // Recovery: re-preview against the current revision and apply.
      await previewButton.click();
      await previewReady.waitFor();
      const [applied] = await applyPreview();
      assert.equal(applied.status(), 200);
      fields = await openExplore();
      assert.equal(await fields.composition.inputValue(), "explicit_only");
      assert.equal(await fields.scope.inputValue(), "joint-scope");
      assert.equal(await fields.mode.inputValue(), "planning");
      const source = await sourcePolicy(owner);
      assert.equal(source.composition_mode, "explicit_only");
      assert.equal(source.composition_scope_id, "joint-scope");
      assert.equal(source.enabled, true);

      // Independent CLI readback adopts the same source: a pending gap and its obligation.
      const on = researchReadback(owner);
      assert.ok(on.summaryFrontier, "Applied policy did not reach Explore summary");
      assert.deepEqual(on.statusFrontier, on.summaryFrontier, "Status and Explore disagree on the live frontier");
      assert.equal(on.summaryFrontier.pending_count, 1);
      assert.equal(on.summaryFrontier.grants_execution_authority, false);
      assert.ok(on.obligation?.obligation_id, "Status did not expose the admitted research duty");
      await page.screenshot({ path: resolve(outputDir, "explore-composition-owner-applied.png"), animations: "disabled" });

      // Disable only the composition policy; activation and scope stay with the Goal.
      await fields.composition.selectOption("disabled");
      await previewButton.click();
      await previewReady.waitFor();
      await applyPreview();
      fields = await openExplore();
      assert.equal(await fields.composition.inputValue(), "disabled");
      assert.equal(await fields.scope.inputValue(), "joint-scope");
      const disabled = researchReadback(owner);
      assert.equal(disabled.summaryFrontier, undefined, "Disabled policy still exposed a live research frontier");
      assert.equal(disabled.obligation, undefined, "Disabled policy still admitted a research duty");
      assert.equal((await sourcePolicy(owner)).composition_mode, "disabled");

      // The browser logs exactly the two intended owner refusals; anything else is a defect.
      assert.deepEqual(errors, [400, 409].map((code) => `Failed to load resource: the server responded with a status of ${code} (${code === 400 ? "Bad Request" : "Conflict"})`));
      const applies = ownerResponses.filter((entry) => entry.path.endsWith("/apply"));
      assert.deepEqual(applies.map((entry) => entry.status >= 400), [true, false, false], JSON.stringify(applies));
      await writeFile(resolve(outputDir, "explore-composition-owner-receipt.json"), JSON.stringify({
        synthetic: true, real_configuration_owner: true, independent_cli_readback: true, owner_responses: ownerResponses,
        on: { pending_count: on.summaryFrontier.pending_count, gap_id: on.summaryFrontier.selected_gap?.gap_id ?? null },
      }, null, 2));
      return {
        coverageEntries: await context.close(),
        note: "Packaged editor → real configuration owner; unsafe scope and stale revision refused without writes; corrected apply → CLI Explore/status readback; disable restores the inactive projection.",
      };
    } catch (error) {
      if (context) {
        await context.page.screenshot({ path: resolve(outputDir, "explore-composition-owner-failure.png"), animations: "disabled" }).catch(() => {});
        await context.close();
      }
      throw error;
    } finally { await owner.close(); }
  },
};
