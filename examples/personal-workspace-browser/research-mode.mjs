import assert from "node:assert/strict";
import {resolve} from "node:path";
import {startCadenceAuthority} from "./automation-cadence.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";
import {outputDir} from "./fixture.mjs";

// The native preview/apply/readback transaction is real and isolated. Browser
// discovery is synthetic; no operator Goal or model execution is involved.
export const researchModeScenario = {
  id: "research-mode",
  async run({browser, collectCoverage, url}) {
    const owner = await startCadenceAuthority();
    const goalId = "multi-agent-projection";
    const apiPath = "/api/chat/goal-configuration";
    let context, denyRead = false, denyApply = false, badIdentity = false, applies = 0;
    const writes = [];
    async function request(path, body) {
      const response = await fetch(owner.url + path, body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : undefined);
      const packet = await response.json();
      assert(response.ok, JSON.stringify(packet));
      return packet;
    }
    const read = () => request(`${apiPath}?goal_id=${goalId}`);
    const capability = packet => packet.capability_catalog.capabilities.find(row => row.capability_id === "explore_harness");
    async function configure(configuration) {
      const body = {goal_id: goalId, capability_id: "explore_harness", configuration};
      const preview = await request(`${apiPath}/preview`, body);
      return request(`${apiPath}/apply`, {...body, expected_plan_revision: preview.plan_revision});
    }
    try {
      const original = await read();
      context = await openWorkspacePage(browser, url, {collectCoverage, beforeGoto: async (_, page) => {
        page.on("request", request => {if (request.method() === "POST") writes.push(new URL(request.url()).pathname);});
        await page.route("**/api/chat/goal-configuration**", async route => {
          const path = new URL(route.request().url()).pathname;
          if (path === apiPath && denyRead) return route.fulfill({status: 503, json: {error: "Isolated read unavailable"}});
          if (path.endsWith("/apply")) {
            if (denyApply) return route.fulfill({status: 403, json: {error: "Isolated configuration permission denied"}});
            applies++;
          }
          const response = await fetchWorkspaceBackend(route, owner.url);
          if (path === apiPath && badIdentity) {
            const json = await response.json(); json.goal_id = "another-goal";
            return route.fulfill({response, json});
          }
          return route.fulfill({response});
        });
      }});
      const {page} = context;
      await page.locator('.personal-goal-link').filter({hasText: 'Multi Agent Projection'}).click();
      const tabs = page.locator('.personal-goal-tabs');
      await tabs.getByRole('button', {name: '研究', exact: true}).click();
      const control = page.getByTestId('goal-research-mode');
      async function refreshMode() {
        const response = page.waitForResponse(value => new URL(value.url()).pathname === apiPath && value.request().method() === 'GET');
        await control.getByRole('button', {name: '刷新', exact: true}).click();
        await response;
      }
      const enable = control.getByRole('button', {name: '开启研究模式', exact: true});
      await enable.waitFor();
      assert.equal((await read()).revision, original.revision, "Reading cannot enable research");
assert.equal(await page.getByTestId('goal-vitals').count(), 0, 'Reading views do not mount the Overview stage bar');
      assert.equal(await page.locator('.goal-stagebar').count(), 0, 'Reading views do not mount the stage bar at all');
      assert.equal(await page.locator('.personal-goal-navigation .personal-agent-select').isVisible(), false);
      await enable.click();
      await control.getByRole('button', {name: '关闭研究模式', exact: true}).waitFor();
      assert.equal(capability(await read()).current.mode, 'evidence');
      assert.equal(applies, 1);
      await page.screenshot({path: resolve(outputDir, 'research-mode-enabled.png'), animations: 'disabled'});
      await control.getByRole('button', {name: '关闭研究模式', exact: true}).click();
      await enable.waitFor();
      assert.equal(capability(await read()).current.mode, 'off');
      // A concurrent settings edit must not be overwritten by the old toggle.
      const preserved = {...capability(await read()).current, mode: 'planning', composition_mode: 'explicit_only', composition_scope_id: 'shared-evidence'};
      await configure(preserved);
      await enable.click();
      await control.getByRole('alert').filter({hasText: '配置已变化'}).waitFor();
      assert.equal(applies, 2);
      await refreshMode();
      await control.getByRole('button', {name: '关闭研究模式', exact: true}).waitFor();
      await control.getByRole('button', {name: '关闭研究模式', exact: true}).click();
      await enable.waitFor();
      const saved = capability(await read()).current;
      assert.equal(saved.mode, 'off');
      assert.equal(saved.profile, preserved.profile);
      assert.equal(saved.composition_mode, 'explicit_only');
      assert.equal(saved.composition_scope_id, 'shared-evidence');
      denyApply = true;
      await enable.click();
      await control.getByRole('alert').waitFor();
      assert.equal(capability(await read()).current.mode, 'off');
      denyApply = false; denyRead = true;
      await refreshMode();
      await control.getByRole('alert').filter({hasText: '未能确认'}).waitFor();
      assert.equal(await control.getByRole('button', {name: '研究模式不可用', exact: true}).isDisabled(), true);
      denyRead = false; badIdentity = true;
      await refreshMode();
      await control.getByRole('alert').filter({hasText: '未能确认'}).waitFor();
      assert.equal(await control.getByRole('button', {name: '研究模式不可用', exact: true}).isDisabled(), true);
      badIdentity = false;
      await refreshMode();
      await enable.waitFor();
      for (const name of ['任务', '成果']) {
        await tabs.getByRole('button', {name, exact: true}).click();
        await page.getByTestId('overview-composer-pill').waitFor();
assert.equal(await page.getByTestId('goal-vitals').count(), 0, 'Reading views do not mount the Overview stage bar');
        assert.equal(await page.locator('.goal-stagebar').count(), 0, 'Reading views do not mount the stage bar at all');
      }
      await tabs.getByRole('button', {name: '研究', exact: true}).click();
      await enable.waitFor();
      await page.setViewportSize({width: 390, height: 844});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path: resolve(outputDir, 'research-mode-narrow.png'), animations: 'disabled'});
      assert(writes.every(path => path.startsWith(apiPath) || ['/api/chat/sessions', '/api/chat/content-media'].includes(path)), `Unexpected dispatch: ${writes.join(',')}`);
      assert.equal(applies, 3);
      return {note: 'Native isolated Goal research enable/disable, concurrency fence, preservation, permission/read failure recovery; no model dispatch; tab hierarchy and narrow layout.', coverageEntries: context.coverageEntries};
    } finally {if (context) await context.close(); await owner.close();}
  },
};
