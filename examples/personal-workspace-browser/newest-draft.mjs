import { resolve } from "node:path";

import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

const OLDER_SUMMARY = "Older pending draft";
const NEWEST_SUMMARY = "Newest pending draft";

// A stored draft the owner has not confirmed yet. `ChatActionStore.list` orders
// these by (`updated_at`, `proposal_id`) newest first, and the fixture's list
// endpoint now serves exactly that order.
function pendingDraft({ proposalId, summary, updatedAt }) {
  return {
    schema_version: "loopx_chat_action_proposal_v1",
    proposal_id: proposalId,
    action_kind: "todo.create",
    summary,
    normalized_parameters: { goal_id: "product-release", text: summary },
    context: { kind: "goal", goal_id: "product-release" },
    expected_state_fingerprint: `fixture-${proposalId}`,
    permission_classification: "durable_write",
    validation_evidence: ["Synthetic pending draft fixture"],
    available_transitions: ["apply", "cancel", "regenerate"],
    status: "preview_ready",
    receipt: null,
    stale: null,
    created_at: updatedAt,
    updated_at: updatedAt,
  };
}

const older = pendingDraft({ proposalId: "draft-older", summary: OLDER_SUMMARY, updatedAt: "2026-09-14T01:00:00Z" });
const newer = pendingDraft({ proposalId: "draft-newer", summary: NEWEST_SUMMARY, updatedAt: "2026-09-14T03:00:00Z" });

export const newestDraftScenario = {
  id: "newest-draft",
  async run({ browser, collectCoverage, url }) {
    async function openGoalChat(options = {}) {
      const ui = await openWorkspacePage(browser, url, { collectCoverage, ...options });
      await ui.page.locator(".personal-goal-link", { hasText: "Product Release" }).click();
      await ui.page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      return ui;
    }

    // The newest unconfirmed draft keeps the conversation; the older one stays
    // one keyboard step away instead of being the card the owner is offered first.
    async function expectNewestLeads(page, note) {
      const newestRow = page.locator(".personal-proposal-row", { hasText: NEWEST_SUMMARY });
      try {
        await newestRow.waitFor({ state: "visible", timeout: 10_000 });
      } catch (error) {
        throw new Error(`${note}: the newest draft did not lead the conversation; rows=${await page.locator(".personal-proposal-row").allInnerTexts()}; ${error.message}`);
      }
      const summary = page.locator(".personal-proposal-backlog > summary");
      try {
        await summary.waitFor({ state: "visible", timeout: 5_000 });
      } catch (error) {
        throw new Error(`${note}: the older draft was not folded behind the newest one; rows=${await page.locator(".personal-proposal-row").allInnerTexts()}; ${error.message}`);
      }
      if (!(await summary.innerText()).includes("另有 1 个待确认提议")) {
        throw new Error(`${note}: the fold did not report the one withheld draft: ${await summary.innerText()}`);
      }
      const olderRow = page.locator(".personal-proposal-row", { hasText: OLDER_SUMMARY });
      if (await olderRow.isVisible()) throw new Error(`${note}: the older draft stayed on the first screen`);
      await summary.click();
      await olderRow.waitFor({ state: "visible" });
      await olderRow.click();
      const drawer = page.locator('.personal-context-drawer[data-context-kind="proposal"]');
      await drawer.getByText(OLDER_SUMMARY, { exact: false }).first().waitFor({ state: "visible" });
      await page.getByRole("button", { name: /关闭详情/ }).click();
    }

    // A restored workspace, in the order the store really returns.
    const restored = await openGoalChat({ apiOptions: { initialActionProposals: [older, newer] } });
    try {
      await expectNewestLeads(restored.page, "restore");
      await restored.page.screenshot({ path: resolve(outputDir, "newest-draft-first-screen.png"), fullPage: false, animations: "disabled" });
    } finally {
      await restored.close();
    }

    // Re-entering the Goal after a reload reaches the same conclusion.
    const reloaded = await openGoalChat({ apiOptions: { initialActionProposals: [newer, older] } });
    try {
      await reloaded.page.reload({ waitUntil: "networkidle" });
      await reloaded.page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      await reloaded.page.locator(".personal-goal-link", { hasText: "Product Release" }).click();
      await reloaded.page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await expectNewestLeads(reloaded.page, "reload");
    } finally {
      await reloaded.close();
    }

    // A list served out of contract order is still judged by the stored time:
    // the reader must not depend on how the records happened to be assembled.
    const reversed = await openGoalChat({
      beforeGoto: async (_api, page) => {
        await page.unroute("**/api/actions?**");
        await page.route("**/api/actions?**", async (route) => {
          await route.fulfill({
            contentType: "application/json",
            json: { ok: true, schema_version: "loopx_chat_action_list_v1", proposals: [older, newer] },
            status: 200,
          });
        });
      },
    });
    try {
      await expectNewestLeads(reversed.page, "reversed arrival order");
    } finally {
      await reversed.close();
    }

    // A draft created in this session is appended last, the opposite of a
    // restore, so it leads only because its stored time is newer.
    const created = await openGoalChat({ apiOptions: { initialActionProposals: [newer, older] } });
    try {
      // `protected` keeps the stop preview reviewable instead of applying it,
      // and the patch gives it a stored time newer than both restored drafts.
      created.api.nextLifecycleProposalPatch = { permission_classification: "protected", updated_at: "2026-09-14T04:00:00Z" };
      await created.page.getByRole("button", { name: "停止 Product Release", exact: true }).click();
      const drawer = created.page.locator('.personal-context-drawer[data-context-kind="proposal"]');
      await drawer.waitFor({ state: "visible" });
      await created.page.getByRole("button", { name: "关闭", exact: true }).click();
      await drawer.waitFor({ state: "hidden" });
      const createdRow = created.page.locator('.personal-proposal-row[data-action-kind="goal.lifecycle"]');
      await createdRow.waitFor({ state: "visible" });
      const summary = created.page.locator(".personal-proposal-backlog > summary");
      await summary.waitFor({ state: "visible" });
      if (!(await summary.innerText()).includes("另有 2 个待确认提议")) {
        throw new Error(`a draft created in this session did not fold the two stored drafts: ${await summary.innerText()}`);
      }
      if (await created.page.locator(".personal-proposal-row", { hasText: NEWEST_SUMMARY }).isVisible()) {
        throw new Error("a newer session draft left a stored draft on the first screen");
      }
    } finally {
      await created.close();
    }

    return {
      coverageEntries: [],
      note: "The newest unconfirmed draft led the first screen for a restore, a reload, a session-created draft and a reversed list; older drafts stayed one keyboard step away.",
    };
  },
};
