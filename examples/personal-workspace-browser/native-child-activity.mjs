import assert from "node:assert/strict";
import {resolve} from "node:path";
import {outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";

export const nativeChildActivityScenario = {
  id: "native-child-activity",
  async run({browser, collectCoverage, url}) {
    const coverageEntries = [];
    for (const observation of ["host_observed", "coordinator_reported", "mixed", "unknown"]) {
      const context = await openWorkspacePage(browser, url, {collectCoverage,
        beforeGoto(api, page) {
          api.goalSubagentConfigurationEnabled = true;
          page.__loopxRuntime.goalSubagentConfigurations.set("loopx-meta",
            {mode: "multi_subagent", spawn_allowed: true, max_children: 3, allowed_domains: []});
          api.nativeChildActivity = {schema_version: "native_subagent_activity_v0",
            observation, host_attested: observation === "host_observed", configured_limit: 3,
            launched_count: 1, skipped_count: 0, capacity_rejected_count: 0,
            host_failed_count: 1, parent_accepted_count: 1, turn_instance_id: "turn-browser-native"};
        },
      });
      try {
        const {page} = context;
        await page.locator(".personal-goal-link").filter({hasText: "LoopX meta"}).click();
        await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: "概览", exact: true}).click();
        await page.getByRole("button", {name: "Goal 信息", exact: true}).click();
        const drawer = page.locator('.personal-context-drawer[data-context-kind="goal"]');
        await drawer.waitFor();
        const activity = drawer.locator(".personal-native-child-activity");
        if (observation === "unknown") {
          assert.equal(await activity.count(), 0);
        } else {
          await activity.waitFor();
          const text = await activity.innerText();
          assert.match(text, /启动 1 次/);
          assert.match(text, /主 Agent 验收 1 项/);
          assert.match(text, observation === "host_observed" ? /宿主已观察/
            : observation === "mixed" ? /部分决策未经宿主核验/ : /目前没有宿主核验/);
          if (observation === "host_observed") {
            await activity.scrollIntoViewIfNeeded();
            await page.screenshot({path: resolve(outputDir, "native-child-desktop.png"), animations: "disabled"});
            await page.setViewportSize({width: 390, height: 844});
            await activity.scrollIntoViewIfNeeded();
            assert(await activity.evaluate(el => el.scrollWidth <= el.clientWidth));
            await page.screenshot({path: resolve(outputDir, "native-child-mobile.png"), animations: "disabled"});
          }
        }
        assert.deepEqual(context.errors, []);
      } finally {
        coverageEntries.push(...await context.close());
      }
    }
    return {coverageEntries, note: "Host, coordinator, mixed and unknown native child activity preserve provenance in the packaged Goal drawer."};
  },
};
