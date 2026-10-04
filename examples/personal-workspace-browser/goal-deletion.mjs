import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

export const goalDeletionScenario = {
  id: "goal-deletion",
  async run({ browser, url }) {
    for (const width of [1512, 390]) {
      const ui = await openWorkspacePage(browser, url, { viewport: { width, height: 982 } });
      const { page, api } = ui;
      try {
        async function requestDeletion() {
          if (width < 640) await page.locator(".personal-mobile-menu").click();
          const directory = page.locator(".personal-stopped-goals");
          if (await directory.getAttribute("open") === null) await directory.locator("summary").click();
          await page.getByRole("button", { name: "删除 Legacy Benchmark", exact: true }).click();
          await page.getByText("确认执行", { exact: true }).waitFor({ state: "visible" });
        }
        const writes = api.durableWriteCount;
        await requestDeletion();
        const drawer = page.locator('.personal-context-drawer[data-context-kind="proposal"]');
        await drawer.getByText(/项目文件、历史状态文件和备份不会被删除/).waitFor();
        assert.equal(api.durableWriteCount, writes, "preview must not delete the Goal");
        assert.equal(api.actionApplies.length, 0);
        await page.screenshot({ path: resolve(outputDir, `goal-delete-confirm-${width}.png`), animations: "disabled" });
        await drawer.getByRole("button", { name: "关闭", exact: true }).click();
        assert.equal(api.goalActivationStates.get("legacy-benchmark"), "stopped");

        await requestDeletion();
        api.nextLifecycleApplyOutcome = "stale";
        await drawer.getByRole("button", { name: "删除 Goal", exact: true }).click();
        await page.locator('[data-action-review="refresh"]').waitFor({ state: "visible" });
        assert.ok(await drawer.getByRole("button", { name: "删除 Goal", exact: true }).isDisabled());
        assert.equal(api.durableWriteCount, writes, "stale confirmation must not delete");
        assert.equal(api.goalActivationStates.get("legacy-benchmark"), "stopped");
        await drawer.getByRole("button", { name: "关闭", exact: true }).click();

        await requestDeletion();
        const proposal = api.actionPreviews.at(-1);
        assert.equal(proposal.normalized_parameters.operation, "delete");
        assert.equal(proposal.normalized_parameters.goal_id, "legacy-benchmark");
        await drawer.getByRole("button", { name: "删除 Goal", exact: true }).click();
        await page.getByText(/已完成：删除 Goal/).waitFor();
        assert.equal(api.durableWriteCount, writes + 1);
        const stored = page.__loopxRuntime.actionProposals.get(proposal.proposalId);
        assert.equal(stored.status, "applied");
        assert.equal(stored.receipt.projection_verified, true);
        await page.reload({ waitUntil: "networkidle" });
        await page.getByTestId("personal-goal-home").waitFor();
        const response = await page.evaluate(async () => (await fetch("/status.json")).json());
        assert.equal(response.run_history.goals.some(goal => goal.id === "legacy-benchmark"), false);
        assert.equal(api.goalActivationStates.has("legacy-benchmark"), false);
        assert.deepEqual(ui.errors.filter(error => !error.includes("409 (Conflict)")), []);
      } finally {
        await ui.close();
      }
    }
    return { coverageEntries: [], note: "Desktop/mobile deletion preview, cancellation, stale rejection, explicit confirmation and reload readback passed; effects use disposable API fixtures, real backend/parser coverage runs separately." };
  },
};
