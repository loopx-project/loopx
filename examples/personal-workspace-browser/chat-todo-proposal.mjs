import { openWorkspacePage } from "./scenario-context.mjs";

// An Agent answers with a Todo proposal, the shape its turn prompt asks for.
// In a Goal conversation the proposal must become a typed todo.create preview
// the owner confirms; without a target Goal it must not create anything.
const GOAL_PROMPT = "请给出一个下一步任务建议。";
const MANAGER_PROMPT = "请为全局给出一个任务建议。";
const PROPOSAL_TEXT = "[P1] 核对发布清单并补齐缺失的验证记录";
// The fixture keeps a "刷新恢复" Turn running long enough to reload into it.
const RECOVERY_PROMPT = "刷新恢复：请再给出一个任务建议。";
const RECOVERY_TEXT = "[P2] 复核恢复回合给出的下一步";
// The fixture answers this exact message with a protected merge action.
const COMBINED_PROMPT = "请合并 PR #123";
const COMBINED_TEXT = "[P1] 合并前补齐 PR #123 的发布说明";
// The owner leaves this Goal conversation before the answer arrives.
const DEPARTED_PROMPT = "请给出一个任务建议，我先去看看别处。";
const DEPARTED_TEXT = "[P2] 补充发布回滚预案";
// A Turn the owner reloads into recovery and then leaves before it completes.
// The fixture answers a resumed "刷新恢复" Turn after 5s, which is the window the
// owner leaves in.
const DEPARTED_RECOVERY_PROMPT = "刷新恢复：离开后仍会完成的回合。";
const RECOVERED_DEPARTURE_TEXT = "[P2] 离开后仍完成的恢复草稿";
const proposalAnswer = {
  message: "我找到一个可评审的步骤。",
  proposals: [{ kind: "todo", priority: "P1", rationale: "发布前需要可核对的证据。", text: PROPOSAL_TEXT }],
};

async function waitFor(predicate, message) {
  const deadline = Date.now() + 10_000;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error(message);
    await new Promise((resolveWait) => setTimeout(resolveWait, 50));
  }
}

export const chatTodoProposalScenario = {
  id: "chat-todo-proposal",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, { collectCoverage });
    const { api, page } = context;
    api.answerForMessage = (message) => (message === GOAL_PROMPT || message === MANAGER_PROMPT ? proposalAnswer
      : message === DEPARTED_PROMPT ? { message: "离开后给出一个步骤。", proposals: [{ kind: "todo", priority: "P2", rationale: "离开对话不改变建议归属。", text: DEPARTED_TEXT }] }
      : message === COMBINED_PROMPT ? { message: "我识别到一个明确的合并请求，并建议先补齐发布说明。", proposals: [{ kind: "todo", priority: "P1", rationale: "合并前需要可核对的说明。", text: COMBINED_TEXT }] }
      : message === RECOVERY_PROMPT ? { message: "恢复后给出一个步骤。", proposals: [{ kind: "todo", priority: "P2", rationale: "恢复回合同样需要可确认的草稿。", text: RECOVERY_TEXT }] }
      : message === DEPARTED_RECOVERY_PROMPT ? { message: "离开后仍然完成的恢复回合。", proposals: [{ kind: "todo", priority: "P2", rationale: "离开 Goal 不改变恢复回合草稿的归属。", text: RECOVERED_DEPARTURE_TEXT }] }
      : null);
    const previewsWithText = (text) => api.actionPreviews.filter((preview) => preview.action_kind === "todo.create"
      && preview.normalized_parameters?.text === text);
    const todoPreviews = () => previewsWithText(PROPOSAL_TEXT);
    const composer = page.getByLabel("向 LoopX 发送消息");
    try {
      // Manager channel: no target Goal, so the proposal cannot become a write.
      await composer.fill(MANAGER_PROMPT);
      await page.getByRole("button", { name: "发送", exact: true }).click();
      await page.getByText("我找到一个可评审的步骤。", { exact: true }).first().waitFor({ state: "visible", timeout: 10_000 });
      await page.waitForTimeout(500);
      if (todoPreviews().length) throw new Error("A manager-channel proposal created a Todo preview without a target Goal");

      const openGoalChat = async () => {
        await page.locator(".personal-goal-link", { hasText: "Product Release" }).click();
        await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      };
      const openManagerChat = async () => {
        await page.locator(".personal-manager-link").first().click();
        const managerChatTab = page.getByRole("navigation", { name: /Manager|管家/ }).getByRole("button", { name: /^(Chat|对话)$/ });
        await managerChatTab.click();
        if (await managerChatTab.getAttribute("aria-current") !== "page") throw new Error("Manager Chat did not open");
      };
      await openGoalChat();
      await composer.fill(GOAL_PROMPT);
      await page.getByRole("button", { name: "发送", exact: true }).click();
      const card = page.locator(".personal-proposal-row", { hasText: PROPOSAL_TEXT });
      await card.waitFor({ state: "visible", timeout: 10_000 });
      await waitFor(() => todoPreviews().length === 1, "The Goal proposal did not create exactly one Todo preview");
      const [preview] = todoPreviews();
      if (preview.normalized_parameters.goal_id !== "product-release" || preview.normalized_parameters.priority !== "P1") {
        throw new Error(`Todo preview lost its Goal or priority: ${JSON.stringify(preview.normalized_parameters)}`);
      }
      if (!String(preview.idempotency_key).startsWith("chat-todo-proposal:")) {
        throw new Error(`Todo preview key is not derived from its Turn: ${preview.idempotency_key}`);
      }
      if (await page.getByRole("dialog").count()) throw new Error("A proposal card opened the drawer without the owner asking");

      // The card belongs to the Goal conversation that offered it: switching
      // to Manager Chat or to another Goal on the same page must not show it,
      // and returning to its Goal still does.
      await openManagerChat();
      await page.getByText("我找到一个可评审的步骤。", { exact: true }).first().waitFor({ state: "visible", timeout: 10_000 });
      if (await card.count()) throw new Error("A Goal Todo proposal leaked into Manager Chat");
      await page.locator(".personal-goal-link", { hasText: "Research Monitor" }).click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await page.waitForTimeout(300);
      if (await card.count()) throw new Error("A Goal Todo proposal leaked into another Goal's conversation");
      await openGoalChat();
      await card.waitFor({ state: "visible", timeout: 10_000 });

      // An answer that lands after the owner left for Manager Chat still
      // belongs to the Goal conversation that asked for it.
      await composer.fill(DEPARTED_PROMPT);
      await page.getByRole("button", { name: "发送", exact: true }).click();
      await openManagerChat();
      if (previewsWithText(DEPARTED_TEXT).length) throw new Error("The departed answer arrived before the owner left its Goal");
      await waitFor(() => previewsWithText(DEPARTED_TEXT).length === 1, "The departed Goal answer did not create its Todo preview");
      await page.waitForTimeout(300);
      const departedCard = page.locator(".personal-proposal-row", { hasText: DEPARTED_TEXT });
      if (await departedCard.count()) throw new Error("A Goal answer that arrived after leaving showed its Todo in Manager Chat");
      await openGoalChat();
      await departedCard.waitFor({ state: "visible", timeout: 10_000 });

      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      await openGoalChat();
      // The newer departed card leads; the first one waits in the backlog.
      await page.locator(".personal-proposal-backlog > summary").click();
      await card.waitFor({ state: "visible", timeout: 10_000 });

      await card.click();
      await page.getByRole("dialog").getByRole("button", { name: "确认并应用", exact: true }).click();
      await waitFor(() => api.actionApplies.map(decodeURIComponent).includes(preview.proposalId), "Confirming the proposal did not apply its preview");

      // A Turn recovered after a reload must offer its proposal card as soon
      // as the recovery completes, not only after another reload.
      const turnsBeforeRecovery = api.turnRequests.length;
      await composer.fill(RECOVERY_PROMPT);
      await page.getByRole("button", { name: "发送", exact: true }).click();
      await waitFor(() => api.turnRequests.length > turnsBeforeRecovery, "The recovery prompt was not sent");
      // Arm the transient failure before reload: on a slow page, recovery can
      // finish while the Goal Chat tab is reopening.
      api.failNextActionPreview = true;
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      await openGoalChat();
      await page.locator(".personal-channel-timeline").getByText("恢复后给出一个步骤。", { exact: true }).waitFor({ state: "visible", timeout: 15_000 });
      const recoveredCard = page.locator(".personal-proposal-row", { hasText: RECOVERY_TEXT });
      await recoveredCard.waitFor({ state: "visible", timeout: 15_000 });
      if (api.failNextActionPreview) throw new Error("Recovery did not exercise the transient preview failure");
      await waitFor(() => new Set(previewsWithText(RECOVERY_TEXT).map((item) => item.idempotency_key)).size === 1,
        "The recovered proposal did not map to exactly one Todo preview");
      const [recoveredPreview] = previewsWithText(RECOVERY_TEXT);
      if (recoveredPreview.normalized_parameters.goal_id !== "product-release") {
        throw new Error(`Recovered Todo preview lost its Goal: ${JSON.stringify(recoveredPreview.normalized_parameters)}`);
      }
      await recoveredCard.click();
      await page.getByRole("dialog").getByRole("button", { name: "确认并应用", exact: true }).click();
      await waitFor(() => api.actionApplies.map(decodeURIComponent).includes(recoveredPreview.proposalId), "Confirming the recovered proposal did not apply its preview");
      const appliesAfterRecovery = api.actionApplies.length;
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      await openGoalChat();
      await page.waitForTimeout(1_000);
      if (api.actionApplies.length !== appliesAfterRecovery) throw new Error("Reloading after the recovered Turn applied a preview again");

      // A recovery Turn whose completion lands after the owner has left its Goal
      // must still give its Todo draft a card in the Goal that owns it. The
      // fixture answers a resumed "刷新恢复" Turn after 5s, which is the window
      // the owner leaves in.
      const turnsBeforeDeparture = api.turnRequests.length;
      await composer.fill(DEPARTED_RECOVERY_PROMPT);
      await page.getByRole("button", { name: "发送", exact: true }).click();
      await waitFor(() => api.turnRequests.length > turnsBeforeDeparture, "The departure recovery prompt was not sent");
      // Reload so the still-running Turn is owned by recovery rather than the
      // original send, then leave for Manager Chat before it completes.
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      await openGoalChat();
      const departedRecoveryCard = page.locator(".personal-proposal-row", { hasText: RECOVERED_DEPARTURE_TEXT });
      await openManagerChat();
      // The recovery stream is aborted by the switch; the worker still finishes.
      if (await departedRecoveryCard.count()) throw new Error("A recovery Turn's Todo leaked into Manager Chat");
      await page.waitForTimeout(6_000);
      if (api.actionPreviews.filter((item) => item.normalized_parameters?.text === RECOVERED_DEPARTURE_TEXT).length) {
        throw new Error("The departed recovery Turn wrote its preview while its Goal was not mounted");
      }

      // A new page has no in-memory recovery claim; persisted completion must suffice.
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });

      // Returning to the owning Goal must show the card without another reload.
      // A transient preview failure must leave the stored completion retryable.
      api.failNextActionPreview = true;
      await openGoalChat();
      await waitFor(() => api.actionPreviews.filter((item) => item.normalized_parameters?.text === RECOVERED_DEPARTURE_TEXT).length === 1,
        "Returning to the Goal did not replay the completed recovery's Todo preview");
      await departedRecoveryCard.waitFor({ state: "visible", timeout: 5_000 });
      // Re-entering and reloading must not add a second card or write a Todo.
      const appliesBeforeDepartureReplay = api.actionApplies.length;
      await openManagerChat();
      await openGoalChat();
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      await openGoalChat();
      await departedRecoveryCard.waitFor({ state: "visible", timeout: 10_000 });
      await page.waitForTimeout(1_000);
      await waitFor(() => new Set(
        api.actionPreviews.filter((item) => item.normalized_parameters?.text === RECOVERED_DEPARTURE_TEXT).map((item) => item.idempotency_key),
      ).size === 1, "Replaying the departed recovery mapped to more than one Todo preview");
      if (api.actionApplies.length !== appliesBeforeDepartureReplay) {
        throw new Error("A departed recovery applied a preview without the owner confirming it");
      }

      // One answer may carry a protected action and Todo proposals together:
      // the protected decision keeps the drawer and the Todo still becomes a card.
      await composer.fill(COMBINED_PROMPT);
      await page.getByRole("button", { name: "发送", exact: true }).click();
      await page.getByText("确认执行").waitFor({ state: "visible", timeout: 10_000 });
      await waitFor(() => api.actionPreviews.some((item) => item.action_kind === "goal.update" && item.summary.includes("PR #123")),
        "The combined answer lost its protected preview");
      await waitFor(() => previewsWithText(COMBINED_TEXT).length === 1, "The combined answer dropped its Todo proposal");
      // The newest pending draft (the protected decision) leads; the Todo waits
      // one step behind it in the backlog, as for any other older draft.
      await page.locator(".personal-proposal-backlog > summary").click();
      await page.locator(".personal-proposal-row", { hasText: COMBINED_TEXT }).waitFor({ state: "visible", timeout: 10_000 });
    } finally {
      await context.close();
    }
    return {
      coverageEntries: context.coverageEntries,
      note: "An Agent Todo proposal becomes a persisted typed preview in its Goal conversation, including one from a Turn recovered after a reload; it stays out of Manager Chat and other Goals, and none is created without a target Goal.",
    };
  },
};
