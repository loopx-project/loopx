import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { resolveTestPython } from "../../scripts/test-python.mjs";
import { outputDir, repoRoot } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

export const confirmedOperationsScenario = {
  id: "confirmed-operations",
  async run({ browser, url }) {
    const fixtureEnv = { ...process.env };
    // An explicitly selected release interpreter must exercise its installed
    // LoopX, not silently shadow the wheel with this source checkout.
    if (!["LOOPX_TEST_PYTHON", "LOOPX_PYTHON_BIN", "LOOPX_PYTHON"].some(key => process.env[key])) {
      fixtureEnv.PYTHONPATH = repoRoot;
    }
    const fixtures = JSON.parse(execFileSync(resolveTestPython(), [
      resolve(repoRoot, "examples/personal-workspace-browser/confirmed-operation-fixtures.py"),
    ], { cwd: repoRoot, env: fixtureEnv, encoding: "utf8" }));
    const states = {
      confirmed: { en: "Confirmed; waiting for the bound managed Turn", "zh-CN": "已确认，等待绑定的受管回合" },
      waiting: { en: "Authorization consumed; waiting for the real result", "zh-CN": "授权已消费，等待真实结果" },
      unknown: { en: "Result unknown; reconcile the original operation, do not resubmit", "zh-CN": "结果未知；核对原操作，不可重复提交" },
      reconciled: { en: "Result card delivery pending", "zh-CN": "等待回传并核验原群卡片" },
    };
    for (const locale of ["en", "zh-CN"]) for (const width of [1512, 390]) {
      for (const [state, labels] of Object.entries(states)) {
        const proposal = fixtures[state];
        const ui = await openWorkspacePage(browser, url, {
          viewport: { width, height: 982 },
          apiOptions: { initialActionProposals: [proposal] },
          beforeGoto: async (_api, page) => page.addInitScript(value => localStorage.setItem("loopx-pw-locale", value), locale),
        });
        try {
          const { page } = ui;
          if (width < 640) await page.locator(".personal-mobile-menu").click();
          await page.locator(".personal-goal-link", { hasText: "Product Release" }).click();
          await page.getByRole("navigation", { name: locale === "en" ? "Goal view" : "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
          await page.locator(".personal-proposal-row").filter({ hasText: proposal.normalized_parameters.projection.title }).click();
          const drawer = page.locator('.personal-context-drawer[data-context-kind="proposal"]');
          await drawer.getByText(labels[locale], { exact: true }).first().waitFor({ state: "visible" });
          assert.ok((await drawer.innerText()).includes("test-model@xhigh"));
          assert.ok((await drawer.innerText()).includes("todo-managed"));
          if (state === "confirmed" || state === "waiting") {
            assert.ok(!/Execution is in progress|正在执行/u.test(await drawer.innerText()), "Approval and consumption are not proof of execution");
          }
          assert.equal(await drawer.getByRole("button", { name: /确认并应用|拒绝|重新生成|Confirm and apply|Reject|Regenerate/ }).count(), 0);
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1), false);
          assert.equal(ui.errors.length, 0, ui.errors.join(" | "));
          assert.equal(ui.api.durableWriteCount, 0, "Inspection must never execute an operation");
          if (state === "confirmed") await page.screenshot({
            path: resolve(outputDir, `managed-operation-${locale}-${width}.png`), animations: "disabled",
          });
        } finally {
          await ui.close();
        }
      }
      const ui = await openWorkspacePage(browser, url, {
        viewport: {width, height: 982},
        beforeGoto: async (_api, page) => page.addInitScript(value => localStorage.setItem("loopx-pw-locale", value), locale),
      });
      try {
        const {page} = ui;
        const proposals = page.__loopxRuntime.actionProposals;
        await page.waitForFunction(() => document.querySelectorAll('.personal-home-goal-card').length === 5);
        assert.equal(await page.getByTestId('personal-brief-operations').count(), 0,
          'No pending card must leave no empty confirmation-delivery column');
        proposals.set(fixtures.prepared.proposal_id, fixtures.prepared);
        const tile = page.getByTestId('personal-brief-operations');
        await tile.waitFor({state: 'attached', timeout: 12_000});
        const row = tile.locator(`[data-operation-id="${fixtures.prepared.proposal_id}"]`);
        await row.click();
        const drawer = page.locator('.personal-context-drawer[data-context-kind="proposal"]');
        const deliveryPending = locale === 'en' ? 'Prepared; confirmation card not delivered yet' : '请求已准备，确认卡尚未投递';
        await drawer.getByText(deliveryPending, {exact: true}).first().waitFor();
        proposals.set(fixtures.prepared.proposal_id, fixtures.delivered);
        const confirmInGroup = locale === 'en' ? 'Confirm in Feishu group' : '前往飞书群确认';
        await drawer.getByText(confirmInGroup, {exact: true}).first().waitFor({timeout: 12_000});
        await tile.waitFor({state: 'detached', timeout: 12_000});
        assert.equal(await drawer.getByRole('button', {name: /Confirm and apply|Reject|确认并应用|拒绝/}).count(), 0);
        // Advance only the presentation clock. Canonical proposal bytes stay
        // unchanged, including across equal-data readback responses.
        await page.evaluate(expiresAt => {
          Date.now = () => Date.parse(expiresAt) + 1;
        }, fixtures.delivered.operation.expires_at);
        const expired = locale === 'en' ? 'Confirmation request expired; ask the Agent for a fresh request'
          : '确认请求已过期，请 Agent 重新准备请求';
        await drawer.getByText(expired, {exact: true}).first().waitFor({timeout: 12_000});
        assert.equal(await page.locator(`[data-testid="personal-brief-needs"] [data-operation-id="${fixtures.delivered.proposal_id}"]`).count(), 0,
          'Expired delivered cards must not send the owner to an unusable confirmation');
        assert.equal(await page.getByTestId('personal-brief-operations').count(), 0);
        assert.equal(await drawer.getByRole('button', {name: /Confirm and apply|Reject|确认并应用|拒绝/}).count(), 0);
        await drawer.getByRole('button', {name: /Close|关闭/}).first().click();
        proposals.clear();
        await page.waitForFunction(() => document.querySelectorAll('.personal-brief-tile').length === 3,
          null, {timeout: 12_000});
        const geometry = await page.locator('.personal-home-goal-card').evaluateAll(cards => cards.map(card => {
          const footer = card.querySelector('footer');
          const count = footer.querySelector('small');
          const label = footer.querySelector('.personal-goal-activity-chip');
          return {cardRight: card.getBoundingClientRect().right, footerRight: footer.getBoundingClientRect().right,
            labelRight: label.getBoundingClientRect().right, countLeft: count.getBoundingClientRect().left,
            countRight: count.getBoundingClientRect().right, fullLabel: label.title};
        }));
        for (const box of geometry) {
          assert.ok(box.footerRight <= box.cardRight + 1 && box.countRight <= box.footerRight + 1,
            'Long Host state must fit the Goal card without pushing its task count out');
          assert.ok(box.labelRight <= box.countLeft - 7 && box.fullLabel.length > 0,
            'Ellipsis must preserve the full status hint and a distinct task-count space');
        }
        assert.equal(ui.api.durableWriteCount, 0);
        assert.equal(ui.errors.length, 0, ui.errors.join(' | '));
      } finally {await ui.close();}
    }
    return { coverageEntries: [], note: "Canonical backend fixtures survived packaged EN/ZH desktop/mobile readback; inspection remained read-only" };
  },
};
