import { resolve } from "node:path";

import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

export const chatRecoveryScenario = {
  id: "chat-recovery",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, { collectCoverage });
    const { api, checkpointCoverage, page } = context;
    const failures = [];
    const notes = [];
    const observations = [];
    const pass = (criterion, note) => notes.push(`${criterion}: ${note}`);
    const fail = (criterion, note) => failures.push(`${criterion}: ${note}`);
    try {
      if (await page.locator(".personal-manager-conversation-tray").count()) {
        throw new Error("Historical manager messages kept a conversation receipt permanently visible before a new send");
      }
      const managerNavigation = page.getByRole("navigation", { name: "管家视图" });
      await managerNavigation.waitFor({ state: "visible" });
      if (await managerNavigation.getByRole("button", { name: "总览", exact: true }).getAttribute("aria-current") !== "page") {
        throw new Error("Manager overview did not expose its persistent selected tab");
      }
      await managerNavigation.getByRole("button", { name: /^(Chat|对话)$/, exact: true }).click();
      if (await managerNavigation.getByRole("button", { name: /^(Chat|对话)$/, exact: true }).getAttribute("aria-current") !== "page") {
        throw new Error("Manager Chat did not become the selected view");
      }
      if (await page.locator(".personal-home-board").isVisible()) throw new Error("Manager Chat kept the overview board visible");
      await managerNavigation.getByRole("button", { name: "总览", exact: true }).click();
      await page.locator(".personal-home-board").waitFor({ state: "visible" });

      await page.locator(".personal-composer-tools > summary").click();
      await page.getByRole("button", { name: "汇总所有 Goal 进展" }).click();
      const reportDeadline = Date.now() + 5_000;
      while (!api.turnRequests.some((turn) => turn.message.includes("汇总所有活跃 Goal 的最新进展与阻塞")) && Date.now() < reportDeadline) {
        await new Promise((resolveWait) => setTimeout(resolveWait, 50));
      }
      if (!api.turnRequests.some((turn) => turn.message.includes("汇总所有活跃 Goal 的最新进展与阻塞"))) throw new Error("Progress report shortcut did not send a useful scoped request");
      await page.locator(".personal-message-pending").waitFor({ state: "hidden" });
      await page.locator(".personal-manager-conversation-tray").waitFor({ state: "visible" });
      if (!(await page.locator(".personal-home-lanes").isVisible())) throw new Error("Manager send replaced the home lane overview");
      const managerUrlBefore = page.url();
      await page.getByRole("button", { name: "询问全局待办", exact: true }).click();
      // This is a fresh question, not an instruction to the shortcut's active Turn.
      await page.locator(".personal-message-pending").waitFor({ state: "hidden" });
      await page.getByLabel("向 LoopX 发送消息").fill("我现在该做什么？只读回答，不要创建或修改任何状态。");
      await page.getByRole("button", { name: "发送", exact: true }).click();
      await page.getByText("管家已读取当前授权范围的 Goal 证据。", { exact: true }).waitFor({ state: "visible" });
      // The steward answers as the LoopX Manager. The executor that served the
      // turn belongs to the machine-capability chip, so an answer must never be
      // labelled with the CLI brand the agent picker happens to hold.
      const answerIdentity = (
        await page.locator(".personal-manager-conversation-tray article.is-assistant strong").last().innerText()
      ).trim();
      if (answerIdentity !== "LoopX 管家") {
        throw new Error(`Manager answer was labelled as its executor instead of the steward: ${answerIdentity}`);
      }
      if (!api.turnRequests.some((turn) => turn.message.startsWith("我现在该做什么？"))) throw new Error("Manager question bypassed the global runtime");
      await page.getByText("查看完整对话", { exact: true }).waitFor({ state: "visible" });
      if (page.url() !== managerUrlBefore) throw new Error(`Manager send navigated away from the overview: ${managerUrlBefore} -> ${page.url()}`);
      const managerConversationType = await page.locator(".personal-manager-conversation-tray").evaluate((tray) => {
        const message = getComputedStyle(tray.querySelector("article p, article .personal-md"));
        const role = getComputedStyle(tray.querySelector("article > strong"));
        const action = getComputedStyle(tray.querySelector(".personal-manager-conversation-link"));
        return {
          actionFontSize: Number.parseFloat(action.fontSize),
          messageFontSize: Number.parseFloat(message.fontSize),
          messageLineHeight: Number.parseFloat(message.lineHeight),
          roleFontSize: Number.parseFloat(role.fontSize),
        };
      });
      if (managerConversationType.messageFontSize < 14
        || managerConversationType.messageLineHeight < 20
        || managerConversationType.roleFontSize < 12
        || managerConversationType.actionFontSize < 14) {
        throw new Error(`Manager conversation receipt typography is below the readable UI scale: ${JSON.stringify(managerConversationType)}`);
      }
      await page.screenshot({ path: resolve(outputDir, "manager-conversation-tray-compact.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      const compactTrayOverflow = await page.locator(".personal-manager-conversation-tray").evaluate((tray) => tray.scrollWidth - tray.clientWidth);
      if (compactTrayOverflow > 1) throw new Error(`Manager conversation receipt has ${compactTrayOverflow}px horizontal overflow at mobile width`);
      await page.screenshot({ path: resolve(outputDir, "manager-conversation-tray-mobile.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 1512, height: 982 });
      await page.getByText("查看完整对话", { exact: true }).click();
      await page.getByRole("navigation", { name: "管家视图" }).waitFor({ state: "visible" });
      if (await page.locator(".personal-home-board").isVisible()) throw new Error("Full manager Chat left the Goal overview visible behind the conversation");
      if (await page.locator(".personal-manager-conversation-tray").count()) throw new Error("Full manager Chat kept the compact home tray visible");
      if (await page.locator(".personal-channel-timeline .personal-message").count() < 4) throw new Error("Manager Chat did not show the complete conversation history");
      await page.screenshot({ path: resolve(outputDir, "manager-chat.png"), fullPage: false, animations: "disabled" });
      await page.getByLabel("向 LoopX 发送消息").fill("请把库存方案交给 worker，保留预留两件的修订，并请同伴独立复核后回报。");
      await page.getByRole("button", { name: "发送", exact: true }).click();
      await page.locator(".personal-message-pending").waitFor({ state: "hidden" });
      await page.screenshot({ path: resolve(outputDir, "collaboration-before.png"), fullPage: false, animations: "disabled" });
      const returnSessionId = api.turnRequests.at(-1).sessionId;
      const turnsBeforeReturn = api.turnRequests.length;
      const delegatedMessage = page.__loopxRuntime.messages.get(returnSessionId).findLast((message) => message.role !== "user");
      delegatedMessage.collaboration = {
        schema_version: "collaboration_request_readback_v0", request_id: "a".repeat(64), agent_id: "worker",
        brief: { purpose: "协作验证库存方案", context: "已否决平均分配；新补充是预留两件。",
          constraints: ["不可超预算", "不可下真实订单"], inputs: [{ ref: "inputs/demand.csv", description: "需求数据" }],
          acceptance: ["独立验证库存与预算"], return_requirement: "返回方案和复核结论" },
        read_status: "pending", decision: "pending", returns: [],
      };
      const collaboration = page.getByRole("region", { name: "交办说明" });
      await collaboration.waitFor({ state: "visible", timeout: 10000 });
      await collaboration.getByText("查看交办内容", { exact: true }).click();
      await collaboration.getByText("已否决平均分配；新补充是预留两件。", { exact: true }).waitFor({ state: "visible" });
      if (!(await collaboration.innerText()).includes("不可超预算")) throw new Error("Delegation lost its constraints");
      delegatedMessage.collaboration.read_status = "supplied";
      delegatedMessage.collaboration.decision = "adopt";
      await collaboration.getByText("接收方判断: 已采纳", { exact: true }).waitFor({ state: "visible", timeout: 10000 });
      if (api.turnRequests.length !== turnsBeforeReturn) throw new Error("Collaboration readback started another model turn");
      await page.setViewportSize({ width: 390, height: 844 });
      await collaboration.evaluate((node) => node.scrollIntoView({ block: "start" }));
      if (await collaboration.evaluate((node) => node.scrollWidth > node.clientWidth + 1)) throw new Error("Collaboration brief overflows on mobile");
      await page.screenshot({ path: resolve(outputDir, "collaboration-brief-mobile.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 1512, height: 982 });
      await collaboration.evaluate((node) => node.scrollIntoView({ block: "start" }));
      await page.screenshot({ path: resolve(outputDir, "collaboration-brief-desktop.png"), fullPage: false, animations: "disabled" });
      delegatedMessage.collaboration.goal_id = "community";
      delegatedMessage.collaboration.decision = "defer";
      delegatedMessage.collaboration.decision_reason = "先完成正在进行的交付；问卷尚未制作。";
      delegatedMessage.collaboration.returns = [{ phase: "conclusion", status: "delivered" }];
      await collaboration.getByText("接收方判断: 已暂缓", { exact: true }).waitFor({ state: "visible", timeout: 10000 });
      await collaboration.getByText("原因: 先完成正在进行的交付；问卷尚未制作。", { exact: true }).waitFor({ state: "visible" });
      await collaboration.getByText("回复已送达", { exact: true }).waitFor({ state: "visible" });
      await collaboration.getByText("接收方: community / worker", { exact: true }).waitFor({ state: "visible" });
      if ((await collaboration.innerText()).includes("结论已回传")) throw new Error("Deferred reply was presented as a completed conclusion");
      await collaboration.evaluate((node) => node.scrollIntoView({ block: "start" }));
      await page.screenshot({ path: resolve(outputDir, "collaboration-deferred-desktop.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      if (await collaboration.evaluate((node) => node.scrollWidth > node.clientWidth + 1)) throw new Error("Deferred explanation overflows on mobile");
      await page.screenshot({ path: resolve(outputDir, "collaboration-deferred-mobile.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 1512, height: 982 });
      // Match the production readback when a stored reply/delivery record is
      // unreadable. Native-file + real HTTP tests qualify the recovery itself;
      // this scripted API fixture qualifies only the packaged presentation.
      delegatedMessage.collaboration.returns = [{ phase: "conclusion", status: "explicit_unverified", error: "delivery_state_unreadable" }];
      await collaboration.getByText("回复送达尚未核验", { exact: true }).waitFor({ state: "visible", timeout: 10000 });
      await collaboration.evaluate((node) => node.scrollIntoView({ block: "start" }));
      await page.screenshot({ path: resolve(outputDir, "collaboration-unreadable-desktop.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      if (await collaboration.evaluate((node) => node.scrollWidth > node.clientWidth + 1)) throw new Error("Unreadable return status overflows on mobile");
      await page.screenshot({ path: resolve(outputDir, "collaboration-unreadable-mobile.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 1512, height: 982 });
      if (api.turnRequests.length !== turnsBeforeReturn) throw new Error("Disposition readback started another model turn");
      pass("collaboration-brief", "Original conversation preserves context, constraints, inputs and receiver decision without a new turn");

      const returnText = "处理结论：已核验新约束并关联现有计划，无需再次追问。";
      page.__loopxRuntime.messages.get(returnSessionId).push({
        message_id: "handoff.browser-fixture", turn_id: "original-delegation",
        role: "agent", origin: "manager_followup", text: `${returnText}\n\n- **已完成**：核验新约束\n- 下一步：继续现有计划\n\n1. 核对证据\n2. 汇报结论`,
        created_at: "2026-08-13T01:00:03Z",
        return_delivery: {
          schema_version: "manager_return_delivery_status_v0",
          phase: "conclusion",
          status: "verification_required",
          error: "provider_delivery_unverified",
        },
      });
      await page.getByText(returnText, { exact: true }).waitFor({ state: "visible", timeout: 10_000 });
      await page.getByText("正在核验送达，不会重复发送", { exact: true }).waitFor({ state: "visible" });
      const richConclusion = page.locator(".personal-channel-timeline .personal-message").filter({ hasText: returnText });
      if (await richConclusion.locator("ul > li").count() !== 2
        || await richConclusion.locator(".personal-md strong").innerText() !== "已完成") {
        throw new Error("Worker conclusion displayed raw Markdown instead of a list and emphasis");
      }
      const listStyles = await richConclusion.locator(".personal-md").evaluate((node) => ({
        unordered: getComputedStyle(node.querySelector("ul")).listStyleType,
        ordered: getComputedStyle(node.querySelector("ol")).listStyleType,
        itemDisplay: getComputedStyle(node.querySelector("li")).display,
      }));
      if (listStyles.unordered !== "disc" || listStyles.ordered !== "decimal" || listStyles.itemDisplay !== "list-item") {
        throw new Error(`Markdown list markers were reset by global styles: ${JSON.stringify(listStyles)}`);
      }
      await richConclusion.scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(outputDir, "manager-automatic-conclusion.png"), fullPage: false, animations: "disabled" });
      const returnedMessage = page.__loopxRuntime.messages.get(returnSessionId)
        .find((message) => message.message_id === "handoff.browser-fixture");
      returnedMessage.return_delivery = {
        schema_version: "manager_return_delivery_status_v0",
        phase: "conclusion",
        status: "delivered",
        verification: "reconciled_after_restart",
      };
      await page.getByText("恢复后已核验送达", { exact: true }).waitFor({ state: "visible", timeout: 10_000 });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByText(returnText, { exact: true }).waitFor({ state: "visible" });
      await richConclusion.scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(outputDir, "manager-automatic-conclusion-mobile.png"), fullPage: false, animations: "disabled" });
      await new Promise((resolveWait) => setTimeout(resolveWait, 3500));
      if (await page.getByText(returnText, { exact: true }).count() !== 1) throw new Error("Worker conclusion duplicated on the next transcript refresh");
      if (api.turnRequests.length !== turnsBeforeReturn) throw new Error("Receiving a worker conclusion started another model turn");
      await page.setViewportSize({ width: 1512, height: 982 });
      await page.getByRole("button", { name: "总览", exact: true }).click();
      await page.locator(".personal-home-board").waitFor({ state: "visible" });
      if (await page.locator(".personal-manager-conversation-tray").count()) {
        throw new Error("Manager conversation receipt stayed permanently visible after returning to the overview");
      }

      const [fileChooser] = await Promise.all([
        page.waitForEvent("filechooser"),
        page.getByRole("button", { name: "添加图片" }).click(),
      ]);
      await fileChooser.setFiles({
        buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z8WQAAAAASUVORK5CYII=", "base64"),
        mimeType: "image/png",
        name: "loopx-smoke.png",
      });
      await page.getByRole("img", { name: "loopx-smoke.png" }).waitFor({ state: "visible" });
      if (await page.getByRole("button", { name: "发送", exact: true }).isDisabled()) throw new Error("A valid image attachment did not enable the composer send action");
      await page.getByRole("button", { name: "移除图片 loopx-smoke.png" }).click();
      pass(18, "The visible attachment button opens a file chooser; a valid PNG renders a preview and enables send.");

      await page.getByLabel("向 LoopX 发送消息").evaluate((target) => {
        const png = Uint8Array.from(atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z8WQAAAAASUVORK5CYII="), (char) => char.charCodeAt(0));
        const file = new File([png], "loopx-pasted.png", { type: "image/png" });
        const transfer = new DataTransfer();
        transfer.items.add(file);
        target.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: transfer }));
      });
      await page.getByRole("img", { name: "loopx-pasted.png" }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: "移除图片 loopx-pasted.png" }).click();
      pass(19, "Pasting a clipboard PNG attaches through the same validated composer path.");
      await page.locator(".personal-goal-link").first().click();
      const goalNavigation = page.getByRole("navigation", { name: "Goal 视图" });
      await goalNavigation.getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await page.locator(".personal-goal-link").first().click();
      await goalNavigation.getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await page.locator(".personal-run-row").first().click();
      await page.getByRole("tab", { name: "详情与操作" }).click();
      // The fixture rejects Session deletion. The failed run action must stay
      // visible in the drawer and keep the Session usable for the next action.
      const unhandledRejections = [];
      const recordRejection = (error) => unhandledRejections.push(error.message);
      page.on("pageerror", recordRejection);
      await page.locator(".personal-run-more > summary").click();
      await page.getByRole("button", { name: "关闭 Session", exact: true }).click();
      await page.getByRole("alert").filter({ hasText: "关闭 Session失败" }).waitFor({ state: "visible" });
      page.off("pageerror", recordRejection);
      if (unhandledRejections.length) throw new Error(`Failed run action escaped as an unhandled rejection: ${unhandledRejections.join(" | ")}`);
      pass("run-action-feedback", "A rejected Session action is reported in the drawer instead of failing silently");
      await page.getByLabel("输入纠偏信息").fill("保持运行，用于验证刷新恢复。 ");
      await page.getByRole("button", { name: "发送纠偏" }).click();
      let recoveryTurn;
      for (let attempt = 0; attempt < 40 && !recoveryTurn; attempt += 1) {
        recoveryTurn = api.turnRequests.find((turn) => turn.message.includes("刷新恢复"));
        if (!recoveryTurn) await page.waitForTimeout(50);
      }
      if (!recoveryTurn) throw new Error("Active recovery Turn was not accepted");

      try {
        await checkpointCoverage();
        await page.reload({ waitUntil: "networkidle" });
        await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
        await page.locator(".personal-goal-link").first().click();
        await goalNavigation.getByRole("button", { name: /^(Chat|对话)$/ }).click();
        const recoveredChat = page.locator('[data-goal-panel="chat"]');
        await recoveredChat.getByText("保持运行，用于验证刷新恢复。", { exact: true }).waitFor({ state: "visible", timeout: 10_000 });
        // The live region also contains "<Agent>: 正在整理…" while a reply is
        // pending. Target the visible message placeholder, not both surfaces.
        await recoveredChat.getByText("正在整理…", { exact: true }).waitFor({ state: "hidden", timeout: 10_000 });
        const recovered = page.__loopxRuntime.sessions.get(recoveryTurn.sessionId);
        if (recovered?.active_turn_id !== null && recovered?.active_turn_id !== recoveryTurn.turnId) {
          throw new Error("Recovered Session points at a different active Turn");
        }
        const replayed = await page.evaluate(async ({ sessionId, turnId }) => {
          const url = `/api/chat/sessions/${sessionId}/turns/${turnId}/events`;
          return Promise.all([fetch(url).then((response) => response.text()), fetch(url).then((response) => response.text())]);
        }, recoveryTurn);
        if (replayed.some((body) => !body.includes("event: turn.completed")) || replayed[0] !== replayed[1]) {
          throw new Error("Completed Turn did not replay identical terminal events to reconnecting clients");
        }
        const assistantCount = (page.__loopxRuntime.messages.get(recoveryTurn.sessionId) ?? [])
          .filter((message) => message.message_id === `${recoveryTurn.turnId}-assistant`).length;
        if (assistantCount !== 1) throw new Error(`Reconnect duplicated the persisted answer: ${assistantCount}`);
        pass(6, "Reload restored Goal history, resumed the Turn and replayed completion without duplicating its answer.");
      } catch (error) {
        fail(6, `Reload/reconnect acceptance failed: ${error.message}`);
        await page.screenshot({ path: resolve(outputDir, "refresh-recovery-failed.png"), fullPage: true, animations: "disabled" });
        observations.push(`Refresh recovery failure: ${error.message}`);
      }

      // Run action state belongs to the Run that issued it. Session A's late
      // close result must neither report on Session B nor release B's pending
      // guard, and A's own failure must still be there when the user returns.
      const actionGoalId = new URL(page.url()).searchParams.get("goalId");
      const heldDeletes = new Map();
      await page.route("**/api/chat/sessions/session-run-action-*", async (route) => {
        if (route.request().method() !== "DELETE") return route.fallback();
        heldDeletes.set(new URL(route.request().url()).pathname.split("/").at(-1), route);
      });
      for (const suffix of ["a", "b"]) {
        const sessionId = `session-run-action-${suffix}`;
        page.__loopxRuntime.sessions.set(sessionId, {
          session_id: sessionId, goal_id: actionGoalId, agent_id: "codex", adapter_kind: "codex_app_server",
          channel_id: `task.run-action-${suffix}`, status: "ready", active_turn_id: null, last_error_code: null,
          created_at: "2026-08-13T01:00:00Z", updated_at: "2026-08-13T01:00:00Z", last_activity_at: "2026-08-13T01:00:00Z", resumable: true,
        });
        page.__loopxRuntime.messages.set(sessionId, []);
      }
      const actionRows = page.locator(".personal-run-row", { hasText: "Agent 执行任务" });
      await actionRows.nth(1).waitFor({ state: "visible", timeout: 15_000 });
      const closeButton = page.getByRole("button", { name: "关闭 Session", exact: true });
      const waitForHeld = async (count) => {
        for (let attempt = 0; attempt < 100 && heldDeletes.size < count; attempt += 1) await page.waitForTimeout(50);
        if (heldDeletes.size < count) throw new Error("A Session close request never reached the service");
        return [...heldDeletes.keys()].at(-1);
      };
      const selectRun = async (row) => {
        await row.click();
        await page.getByRole("tab", { name: "详情与操作" }).click();
        if (!(await page.locator(".personal-run-more").evaluate((menu) => menu.open))) await page.locator(".personal-run-more > summary").click();
      };
      const closeRun = async (row, heldCount) => {
        await selectRun(row);
        await closeButton.click();
        return waitForHeld(heldCount);
      };
      const settle = (sessionId, response) => {
        const route = heldDeletes.get(sessionId);
        heldDeletes.delete(sessionId);
        return route.fulfill(response);
      };
      const failure = (reason) => ({ contentType: "application/json", json: { ok: false, error: reason }, status: 503 });
      const success = (sessionId) => ({ contentType: "application/json", json: { ok: true, closed: true, session_id: sessionId }, status: 200 });
      const alertWith = (text) => page.getByRole("alert").filter({ hasText: text });

      const sessionA = await closeRun(actionRows.nth(0), 1);
      if (!(await closeButton.isDisabled())) throw new Error("A pending Session close did not guard its own button");
      const sessionB = await closeRun(actionRows.nth(1), 2);
      await settle(sessionA, failure("run-action-a-failed"));
      await page.waitForTimeout(300);
      if (await alertWith("run-action-a-failed").count()) throw new Error("Session A's late close failure was reported on Session B");
      if (!(await closeButton.isDisabled())) throw new Error("Session A's late close failure released Session B's pending guard");
      await settle(sessionB, failure("run-action-b-failed"));
      await alertWith("run-action-b-failed").waitFor({ state: "visible" });
      if (await closeButton.isDisabled()) throw new Error("Session B's own close failure left its button disabled");
      await selectRun(actionRows.nth(0));
      await alertWith("run-action-a-failed").waitFor({ state: "visible" });
      if (await alertWith("run-action-b-failed").count()) throw new Error("Session B's close failure was reported on Session A");

      // A late success from A must not release B's guard either.
      await closeButton.click();
      await waitForHeld(1);
      if (await alertWith("run-action-a-failed").count()) throw new Error("Retrying a Session close kept its previous failure visible");
      await closeRun(actionRows.nth(1), 2);
      await settle(sessionA, success(sessionA));
      await page.waitForTimeout(300);
      if (!(await closeButton.isDisabled())) throw new Error("Session A's late close success released Session B's pending guard");
      await settle(sessionB, success(sessionB));
      await page.waitForFunction(() => !document.querySelector(".personal-run-action-feedback"));
      if (await closeButton.isDisabled()) throw new Error("Session B's own close success left its button disabled");
      pass("run-action-ownership", "Late Session close results report on, and release the guard of, only the Run that issued them");

      // An executor without native steering accepts one Turn at a time. Its
      // recovered and 409-handoff paths must keep Send closed; supported Codex
      // instructions are covered by composer-session-admission.
      const turnsBeforeRunningCheck = api.turnRequests.length;
      await page.getByLabel("向 LoopX 发送消息").fill("刷新后验证中断控制：输入框应等待本轮。");
      await page.getByRole("button", { name: "发送", exact: true }).click();
      while (api.turnRequests.length === turnsBeforeRunningCheck) await page.waitForTimeout(50);
      const unsupportedSessionId = api.turnRequests.at(-1).sessionId;
      page.__loopxRuntime.sessions.set(unsupportedSessionId, { ...page.__loopxRuntime.sessions.get(unsupportedSessionId), adapter_kind: "external" });
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      await page.locator(".personal-goal-link").first().click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      const turnRunningHint = page.locator(".personal-composer-status", { hasText: "本轮回答进行中" });
      await turnRunningHint.waitFor({ state: "visible", timeout: 5_000 });
      await page.getByLabel("向 LoopX 发送消息").fill("回合进行中不应发送");
      if (!await page.getByRole("button", { name: "发送", exact: true }).isDisabled()) {
        throw new Error("Composer stayed sendable while the recovered Turn was running");
      }
      // Leaving cancels the recovery and returning starts a new one for the
      // same Turn. The cancelled recovery must not leave a pending reply that
      // keeps the composer blocked after the Turn completes.
      await page.locator(".personal-goal-link").nth(1).click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await page.getByLabel("向 LoopX 发送消息").waitFor({ state: "visible" });
      if (await turnRunningHint.count()) throw new Error("Another Goal's composer waited for this Goal's running Turn");
      await page.locator(".personal-goal-link").first().click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await turnRunningHint.waitFor({ state: "visible", timeout: 5_000 });
      await page.getByLabel("向 LoopX 发送消息").fill("回合进行中不应发送");
      await turnRunningHint.waitFor({ state: "hidden", timeout: 10_000 });
      if (await page.getByRole("button", { name: "发送", exact: true }).isDisabled()) {
        throw new Error("Composer stayed blocked after the running Turn completed");
      }
      if (api.turnRequests.length !== turnsBeforeRunningCheck + 1) throw new Error("A message was sent while the Turn was running");
      await page.getByLabel("向 LoopX 发送消息").fill("");
      pass("composer-running-turn", "After a reload, and after leaving and returning, the composer waits for the running Turn and reopens when it completes");
      // Another page starts a Turn after this page's last snapshot, so the
      // ordinary POST is the first to learn of it: the service answers 409
      // with the running Turn. The page must adopt that Turn with its
      // controls, keep the draft, and stay blocked until the Turn completes.
      const busySessionId = api.turnRequests.at(-1).sessionId;
      const foreignTurnId = `turn-foreign-${Date.now()}`;
      page.__loopxRuntime.turnMessages.set(foreignTurnId, "另一页面发起的中断控制回合");
      page.__loopxRuntime.sessions.set(busySessionId, { ...page.__loopxRuntime.sessions.get(busySessionId), active_turn_id: foreignTurnId, status: "busy" });
      let rejectedPosts = 0;
      await page.route(`**/api/chat/sessions/${busySessionId}/turns`, async (route) => {
        if (route.request().method() !== "POST") return route.fallback();
        rejectedPosts += 1;
        await route.fulfill({ contentType: "application/json", status: 409,
          json: { ok: false, error: "another turn is already running for this session", active_turn_id: foreignTurnId } });
      });
      // Hold the conversation's own Session re-read that follows the 409, so
      // the check covers the handoff before the recovery adopts the Turn, not
      // only after.
      const heldReads = [];
      let holdReads = false;
      await page.route("**/api/chat/sessions?*", async (route) => {
        if (!holdReads || route.request().method() !== "GET"
          || !new URL(route.request().url()).searchParams.has("channel_id")) return route.fallback();
        heldReads.push(route);
      });
      const draft = "这条消息在另一回合运行时发出";
      const composerInput = page.getByLabel("向 LoopX 发送消息");
      const sendButton = page.getByRole("button", { name: "发送", exact: true });
      // The conversation hides the quick-prompt strip once it has messages, and
      // the rejected send below adds one, so read them while they are still up.
      // The quick-prompt strip renders only while a conversation is empty. This
      // Goal's chat already has history, so the handoff's effect on those
      // prompts is covered where a fresh Goal shows them, not here.
      await composerInput.fill(draft);
      holdReads = true;
      await sendButton.click();
      for (let attempt = 0; attempt < 100 && (!rejectedPosts || !heldReads.length); attempt += 1) await page.waitForTimeout(50);
      if (!heldReads.length) throw new Error("The 409 did not make the page re-read the Session");
      for (let check = 0; check < 10; check += 1) {
        if (!(await sendButton.isDisabled())) throw new Error("Send reopened before the recovery adopted the reported Turn");
        await page.waitForTimeout(100);
      }
      await sendButton.click({ force: true });
      if (rejectedPosts !== 1) throw new Error(`The composer posted ${rejectedPosts} times before the recovery adopted the Turn`);
      holdReads = false;
      for (const route of heldReads.splice(0)) await route.fallback();
      await page.unroute("**/api/chat/sessions?*");
      await turnRunningHint.waitFor({ state: "visible", timeout: 5_000 });
      await page.getByRole("button", { name: "中断本轮" }).waitFor({ state: "visible" });
      if (await page.getByRole("button", { name: "中断本轮" }).count() !== 1) {
        throw new Error("The recovery added a second pending reply instead of adopting the handoff reply");
      }
      await page.getByRole("button", { name: "调整本轮" }).waitFor({ state: "visible" });
      if (await composerInput.inputValue() !== draft) throw new Error("The draft rejected by a running Turn was not kept");
      if (!(await sendButton.isDisabled())) throw new Error("Send stayed enabled after the service reported a running Turn");
      await sendButton.click({ force: true });
      if (await page.locator(".personal-channel-timeline .personal-message").filter({ hasText: draft }).count()) {
        throw new Error("A message the service did not accept stayed in the conversation");
      }
      await turnRunningHint.waitFor({ state: "hidden", timeout: 10_000 });
      if (await sendButton.isDisabled()) throw new Error("Send stayed blocked after the adopted Turn completed");
      if (rejectedPosts !== 1) throw new Error(`The composer posted ${rejectedPosts} times into a running Turn`);
      await page.unroute(`**/api/chat/sessions/${busySessionId}/turns`);
      await composerInput.fill("");
      pass("composer-running-turn-409", "A 409 running-Turn receipt keeps Send closed through the handoff, is adopted with its controls, keeps the draft and blocks Send until completion");
      // A failed Session read after the 409 says nothing about the reported
      // Turn, so the handoff must keep Send closed and read again rather than
      // treat the failure as the Turn having ended.
      const retriedTurnId = `turn-foreign-retry-${Date.now()}`;
      page.__loopxRuntime.turnMessages.set(retriedTurnId, "读取失败后仍在运行的中断控制回合");
      page.__loopxRuntime.sessions.set(busySessionId, { ...page.__loopxRuntime.sessions.get(busySessionId), active_turn_id: retriedTurnId, status: "busy" });
      let retryRejectedPosts = 0;
      await page.route(`**/api/chat/sessions/${busySessionId}/turns`, async (route) => {
        if (route.request().method() !== "POST") return route.fallback();
        retryRejectedPosts += 1;
        await route.fulfill({ contentType: "application/json", status: 409,
          json: { ok: false, error: "another turn is already running for this session", active_turn_id: retriedTurnId } });
      });
      let failedReads = 0;
      const heldRetryReads = [];
      let failReads = false;
      await page.route("**/api/chat/sessions?*", async (route) => {
        if (!failReads || route.request().method() !== "GET"
          || !new URL(route.request().url()).searchParams.has("channel_id")) return route.fallback();
        if (failedReads === 0) {
          failedReads += 1;
          return route.fulfill({ contentType: "application/json", status: 503, json: { ok: false, error: "chat store temporarily unavailable" } });
        }
        heldRetryReads.push(route);
      });
      await composerInput.fill(draft);
      failReads = true;
      await sendButton.click();
      for (let attempt = 0; attempt < 200 && !heldRetryReads.length; attempt += 1) {
        if (failedReads && !(await sendButton.isDisabled())) throw new Error("Send reopened after the Session read following the 409 failed");
        await page.waitForTimeout(50);
      }
      if (!failedReads) throw new Error("The 409 did not make the page re-read the Session");
      if (!heldRetryReads.length) throw new Error("A failed Session read was not retried while the reported Turn could still run");
      if (!(await sendButton.isDisabled())) throw new Error("Send reopened while the retried Session read was pending");
      await turnRunningHint.waitFor({ state: "visible", timeout: 5_000 });
      await page.getByRole("button", { name: "中断本轮" }).waitFor({ state: "visible" });
      if (retryRejectedPosts !== 1) throw new Error(`The composer posted ${retryRejectedPosts} times while the Session read was failing`);
      failReads = false;
      for (const route of heldRetryReads.splice(0)) await route.fallback();
      await page.unroute("**/api/chat/sessions?*");
      if (await page.getByRole("button", { name: "中断本轮" }).count() !== 1) {
        throw new Error("The retried recovery added a second pending reply instead of adopting the handoff reply");
      }
      if (await composerInput.inputValue() !== draft) throw new Error("The draft rejected by a running Turn was not kept across the failed read");
      await turnRunningHint.waitFor({ state: "hidden", timeout: 10_000 });
      if (await sendButton.isDisabled()) throw new Error("Send stayed blocked after the retried recovery saw the Turn complete");
      if (retryRejectedPosts !== 1) throw new Error(`The composer posted ${retryRejectedPosts} times into a running Turn`);
      await page.unroute(`**/api/chat/sessions/${busySessionId}/turns`);
      await composerInput.fill("");
      pass("composer-running-turn-409-read-failure", "A failed Session read after a 409 keeps Send closed with the Turn controls, is retried, adopts the Turn and reopens only once it completes");
      // The pending reply shows Adjust/Interrupt from the 409 on, so before any
      // Session read returns, both must act on the exact reported Turn.
      const controlledTurnId = `turn-foreign-controls-${Date.now()}`;
      page.__loopxRuntime.turnMessages.set(controlledTurnId, "交接期间可调整和中断的中断控制回合");
      page.__loopxRuntime.sessions.set(busySessionId, { ...page.__loopxRuntime.sessions.get(busySessionId), active_turn_id: controlledTurnId, status: "busy" });
      let controlRejectedPosts = 0;
      await page.route(`**/api/chat/sessions/${busySessionId}/turns`, async (route) => {
        if (route.request().method() !== "POST") return route.fallback();
        controlRejectedPosts += 1;
        await route.fulfill({ contentType: "application/json", status: 409,
          json: { ok: false, error: "another turn is already running for this session", active_turn_id: controlledTurnId } });
      });
      const heldControlReads = [];
      await page.route("**/api/chat/sessions?*", async (route) => {
        if (route.request().method() !== "GET") return route.fallback();
        heldControlReads.push(route);
      });
      const steers = [];
      await page.route("**/steer", async (route) => {
        const body = route.request().postDataJSON();
        const [sessionId, turnId] = new URL(route.request().url()).pathname.match(/sessions\/([^/]+)\/turns\/([^/]+)\/steer/).slice(1);
        steers.push({ sessionId, turnId });
        await route.fulfill({ json: { ok: true, session_id: sessionId, turn_id: turnId, client_ingress_id: body.client_ingress_id, status: "delivered" } });
      });
      const interruptsBefore = api.interrupts.length;
      await composerInput.fill(draft);
      await sendButton.click();
      for (let attempt = 0; attempt < 100 && (!controlRejectedPosts || !heldControlReads.length); attempt += 1) await page.waitForTimeout(50);
      if (!heldControlReads.length) throw new Error("The 409 did not make the page re-read the Session");
      const handoffReply = page.locator(".personal-message").filter({ has: page.getByRole("button", { name: "中断本轮", exact: true }) });
      await handoffReply.getByRole("button", { name: "调整本轮", exact: true }).click();
      await handoffReply.getByLabel("追加给本轮的指令").fill("交接期间先核对依赖。");
      await handoffReply.getByRole("button", { name: "发送调整", exact: true }).click();
      await handoffReply.getByText("执行器已接收本轮追加指令。", { exact: true }).waitFor({ timeout: 5_000 });
      if (steers.length !== 1 || steers[0].sessionId !== busySessionId || steers[0].turnId !== controlledTurnId) {
        throw new Error(`Adjust during the handoff did not reach the reported Turn: ${JSON.stringify(steers)}`);
      }
      if (!(await sendButton.isDisabled())) throw new Error("Send reopened after adjusting the handed-off Turn");
      await handoffReply.getByRole("button", { name: "中断本轮", exact: true }).click();
      for (let attempt = 0; attempt < 100 && api.interrupts.length === interruptsBefore; attempt += 1) await page.waitForTimeout(50);
      const interrupted = api.interrupts.slice(interruptsBefore);
      if (interrupted.length !== 1 || interrupted[0].sessionId !== busySessionId || interrupted[0].turnId !== controlledTurnId) {
        throw new Error(`Interrupt during the handoff did not reach the reported Turn: ${JSON.stringify(interrupted)}`);
      }
      await page.locator(".personal-message").filter({ hasText: "已中断。你可以在当前会话继续发送消息。" }).last().waitFor({ timeout: 5_000 });
      await turnRunningHint.waitFor({ state: "hidden", timeout: 5_000 });
      if (await sendButton.isDisabled()) throw new Error("Send stayed blocked after the handed-off Turn was interrupted");
      if (await composerInput.inputValue() !== draft) throw new Error("The draft rejected by a running Turn was not kept through the handoff controls");
      for (const route of heldControlReads.splice(0)) await route.fallback();
      await page.unroute("**/api/chat/sessions?*");
      await page.waitForTimeout(500);
      if (await page.getByRole("button", { name: "中断本轮", exact: true }).count()) {
        throw new Error("A late Session read revived the interrupted handoff Turn");
      }
      if (await sendButton.isDisabled()) throw new Error("A late Session read closed Send after the interrupt");
      if (controlRejectedPosts !== 1) throw new Error(`The composer posted ${controlRejectedPosts} times during the handoff`);
      await page.unroute("**/steer");
      await page.unroute(`**/api/chat/sessions/${busySessionId}/turns`);
      await composerInput.fill("");
      pass("composer-running-turn-409-controls", "Before any Session read returns, Adjust and Interrupt on the 409 handoff reply reach the exact reported Session and Turn, and the interrupt settles the reply and reopens Send");
      if (failures.length) throw new Error(failures.join(" | "));
    } finally {
      await context.close();
    }
    return {
      coverageEntries: context.coverageEntries,
      note: [...notes, ...observations].join(" "),
    };
  },
};