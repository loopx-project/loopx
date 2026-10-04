import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { resolveTestPython } from "../../scripts/test-python.mjs";
import { outputDir, repoRoot } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

export const externalEvidenceReadbackScenario = {
  id: "external-evidence-readback",
  async run({browser, collectCoverage, url}) {
    // Exercise the product's typed plan/admission and actual downstream ledger,
    // then hand the resulting shared readback to the existing answer surface.
    const result = spawnSync(resolveTestPython(), ["-X", "utf8", "-c", `
import tempfile
from pathlib import Path
from loopx.control_plane.effect_runtime import effect_runtime_result as effect
from loopx.capabilities.deep_research.runtime import start_research
from loopx.capabilities.external_research.projection import readback, render_readback
provider = {"provider_id":"method:public-github", "provider_kind":"method", "protocol":"external_evidence_research_v0", "declared":True, "installed":True, "enabled":True, "ready":True, "unavailable_reason":None}
ref = "https://github.com/example/public/blob/" + "a"*40 + "/README.md"
plan = effect("external_evidence.plan", {"request":{"objective":"Inspect public fixture", "user_activity":"Choose a source", "decision":"Whether to use the fixture", "evidence_kinds":["literal_match"], "source_refs":[ref]}, "providers":[provider]})
receipt = {"schema_version":"loopx_external_evidence_receipt_v0", "plan_id":plan["plan_id"], "request_id":plan["request"]["request_id"], "provider_id":provider["provider_id"], "provider_kind":"method", "status":"succeeded", "summary":"Read pinned public fixture", "completed_at":"2026-10-02T00:00:00Z", "sources":[{"source_ref":ref, "source_family":"github_repository_file", "basis":"observed", "finding":"Literal fixture marker observed at line 1", "content_digest":"sha256:"+"b"*64, "accessed_at":"2026-10-02T00:00:00Z", "limitation":"Retrieval only; completeness unverified"}]}
admission = effect("external_evidence.admit", {"plan":plan, "receipt":receipt, "decision":{"disposition":"admit", "reason":"Synthetic parent checked the direct source", "admitted_source_refs":[ref]}})
with tempfile.TemporaryDirectory(prefix="lxe-ui-") as folder:
    project=Path(folder)
    start_research(project, question=plan["request"]["objective"], max_sources=8, max_subquestions=4)
    print(render_readback(readback(plan, receipt, admission, project=project, execute=True)))
`], {cwd:repoRoot, encoding:"utf8", env:{...process.env, PYTHONPATH:repoRoot}, timeout:45000});
    assert.equal(result.status, 0, result.stderr);
    const markdown = result.stdout;
    const context = await openWorkspacePage(browser, url, {collectCoverage});
    const {api, page} = context;
    try {
      api.answerForMessage = () => markdown;
      await page.getByRole("navigation", {name:"管家视图"}).getByRole("button", {name:/^(Chat|对话)$/}).click();
      await page.getByLabel("向 LoopX 发送消息").fill("Show the external evidence readback");
      await page.getByRole("button", {name:"发送",exact:true}).click();
      const answer = page.locator(".personal-channel-timeline .personal-message.is-assistant", {hasText:"External evidence"});
      await answer.waitFor();
      assert.match(await answer.innerText(), /retire_ready/u);
      assert.match(await answer.innerText(), /Downstream coverage.*1 admitted/u);
      assert.match(await answer.innerText(), /Evidence completeness is unverified/u);
      await answer.scrollIntoViewIfNeeded();
      await page.screenshot({path:resolve(outputDir,"external-evidence-desktop.png"),fullPage:false,animations:"disabled"});
      await page.setViewportSize({width:390,height:844});
      await answer.scrollIntoViewIfNeeded();
      assert.equal(await answer.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
      await page.screenshot({path:resolve(outputDir,"external-evidence-mobile.png"),fullPage:false,animations:"disabled"});
      await page.reload({waitUntil:"networkidle"});
      await page.getByRole("navigation", {name:"管家视图"}).getByRole("button", {name:/^(Chat|对话)$/}).click();
      await answer.waitFor();
      assert.match(await answer.innerText(), /retire_ready/u);
      assert.equal(context.errors.length, 0);
      return {coverageEntries:context.coverageEntries, note:"Shared typed evidence readback survives conversation reload and fits desktop/mobile."};
    } finally { await context.close(); }
  },
};
