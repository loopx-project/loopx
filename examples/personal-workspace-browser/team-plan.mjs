// Browser acceptance for confirming a validated steward team plan.
//
// The unit smoke proves the transport accepts the kind and that the reducer
// renders the lanes. This scenario proves the click path an owner actually
// takes: a validated multi-lane preview becomes a card, the card shows every
// lane and its gap, and confirming it sends exactly one apply.

import { resolve } from "node:path";

import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

const GOAL_ID = "product-release";
const PROPOSAL_ID = "proposal-team-plan-fixture";
const MANAGER_PROPOSAL_ID = "proposal-team-plan-manager-fixture";
const READBACK_PROPOSAL_ID = "proposal-team-plan-readback-fixture";
const RETRY_PROPOSAL_ID = "proposal-team-plan-retry-fixture";
// The manager-channel card is deliberately a different plan from the Goal-scoped
// one, so a row in the manager conversation cannot be the Goal's card leaking in.
const MANAGER_PROPOSAL_TITLE = "为 product-release 分配 3 项任务";
const READY_TODO = "Implement the bounded intake";
const GAP_TODO = "Independently review the intake";

function teamPlanProposal() {
  return {
    schema_version: "loopx_chat_action_proposal_v1",
    proposal_id: PROPOSAL_ID,
    action_kind: "team.plan",
    summary: "为 product-release 分配 2 项任务",
    normalized_parameters: {
      goal_id: GOAL_ID,
      plan: {
        schema_version: "steward_team_plan_preview_v0",
        kind: "steward_team_plan_preview",
        goal_id: GOAL_ID,
        objective: "Ship the bounded intake",
        lanes: [
          {
            lane_id: "lane_intake",
            agent_id: "agent-backend",
            acceptance: "the bounded Todo is created through the canonical owner",
            staffing: "ready",
            first_todo: {
              text: READY_TODO,
              priority: "P1",
              task_class: "advancement_task",
              action_kind: "implement",
            },
          },
          {
            lane_id: "lane_review",
            agent_id: "agent-reviewer",
            acceptance: "the review receipt is recorded",
            staffing: "gap",
            gap_reason_code: "agent_not_registered",
            declined_first_todo: {
              text: GAP_TODO,
              priority: "P1",
              task_class: "advancement_task",
              action_kind: "validate",
            },
          },
        ],
        gaps: [{ lane_id: "lane_review", reason_code: "agent_not_registered" }],
        quota_envelope: { slots: 4, window: "1d" },
        stop_condition: "every lane reports a typed outcome or a stated gap",
        applies: false,
      },
      requested_by: "owner",
    },
    context: { kind: "goal", goal_id: GOAL_ID },
    expected_state_fingerprint: "fixture-team-plan-r1",
    permission_classification: "durable_write",
    validation_evidence: ["every ready lane names an Agent this Goal registers"],
    available_transitions: ["apply", "cancel"],
    status: "preview_ready",
    receipt: null,
    stale: null,
    created_at: "2026-09-16T01:00:00Z",
    updated_at: "2026-09-16T01:00:01Z",
  };
}

/**
 * The same plan as the manager channel stored it.
 *
 * A plan the steward offers from the manager conversation is stored by that
 * channel, and its card has to be confirmable in the conversation that produced
 * it -- not only under the Goal whose workspace it is scoped to.
 */
function managerTeamPlanProposal({
  proposalId = MANAGER_PROPOSAL_ID,
  lanePrefix = "",
} = {}) {
  const plan = teamPlanProposal();
  const parameters = plan.normalized_parameters;
  return {
    ...plan,
    proposal_id: proposalId,
    summary: MANAGER_PROPOSAL_TITLE,
    context: { kind: "manager", goal_id: GOAL_ID },
    // Two independently stored drafts never share a timestamp, and the Goal view
    // leads with the newest unconfirmed one. The manager-channel plan is the
    // earlier draft, so this Goal keeps offering its own card on the first screen
    // while the manager plan stays reachable in the conversation that stored it.
    created_at: "2026-09-15T18:00:00Z",
    updated_at: "2026-09-15T18:00:01Z",
    normalized_parameters: {
      ...parameters,
      plan: {
        ...parameters.plan,
        objective: "Ship the manager-channel intake",
        lanes: [
          ...parameters.plan.lanes.map((lane) => lanePrefix
            ? { ...lane, lane_id: `${lanePrefix}_${lane.lane_id}` }
            : lane),
          {
            lane_id: lanePrefix ? `${lanePrefix}_manager` : "a1a1a1a1a1a1",
            agent_id: "agent-manager",
            acceptance: "the manager lane reports its receipt",
            staffing: "ready",
            first_todo: {
              text: "Review the steward handoff",
              priority: "P2",
              task_class: "advancement_task",
              action_kind: "validate",
            },
          },
        ],
        gaps: parameters.plan.gaps.map((gap) => lanePrefix
          ? { ...gap, lane_id: `${lanePrefix}_${gap.lane_id}` }
          : gap),
      },
    },
  };
}

export const teamPlanScenario = {
  id: "team-plan",
  async run({ browser, collectCoverage, url }) {
    const failures = [];
    const notes = [];
    const check = (condition, message) => {
      if (condition) notes.push(message);
      else failures.push(message);
    };
    const context = await openWorkspacePage(browser, url, {
      apiOptions: { initialActionProposals: [teamPlanProposal(), managerTeamPlanProposal()] },
      collectCoverage,
    });
    try {
      const { api, page } = context;
      // Only the product's own API calls are asserted here: the development
      // server may refuse to serve an asset it considers outside its root,
      // which is a harness path question rather than a client failure.
      const failedResponses = [];
      page.on("response", (response) => {
        if (response.status() >= 400 && response.url().includes("/api/")) {
          failedResponses.push(`${response.status()} ${response.url()}`);
        }
      });
      await page.locator(".personal-goal-link", { hasText: "Product Release" }).click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();

      const row = page.locator(".personal-proposal-row", { hasText: "分配 2 项任务" });
      try {
        await row.waitFor({ state: "visible", timeout: 15_000 });
      } catch (error) {
        throw new Error(
          `${error.message}; rows=${JSON.stringify(await page.locator(".personal-proposal-row").allInnerTexts())};`
          + ` body=${(await page.locator("body").innerText()).slice(0, 1500)}`,
        );
      }
      check(await row.getAttribute("data-action-kind") === "team.plan" && (await row.innerText()).includes("团队分配"), "the proposal row is a team.plan action labelled for the owner");

      await row.click();
      const drawer = page.locator('.personal-context-drawer[data-context-kind="proposal"]');
      await drawer.getByText("配额包络", { exact: true }).waitFor({ state: "visible", timeout: 15_000 });
      const previewText = await drawer.innerText();
      check(previewText.includes("agent-backend"), "a ready lane names the Agent that runs it");
      check(
        previewText.includes(`P1 · implement · ${READY_TODO}`),
        "a ready lane shows its first bounded Todo with its priority and action kind",
      );
      check(
        previewText.includes("验收参考: the bounded Todo is created through the canonical owner"),
        "a ready lane shows its acceptance signal",
      );
      check(
        previewText.includes("agent-reviewer")
        && previewText.includes("待安排")
        && previewText.includes("尚未加入此目标")
        && previewText.includes(GAP_TODO),
        "a gap lane says it is unstaffed, names the reason, and keeps the work it did not staff",
      );
      check(
        previewText.includes("配额包络") && previewText.includes("slots: 4") && previewText.includes("停止条件"),
        "the quota envelope and the stop condition render",
      );
      check(previewText.includes("确认后分配可安排的任务"), "the card states what confirming does");
      check(
        !/(已创建|已经创建|lanes created|已组建)/u.test(previewText),
        "the card never claims a lane already exists before confirmation",
      );
      await page.screenshot({
        path: resolve(outputDir, "team-plan-preview.png"),
        fullPage: false,
        animations: "disabled",
      });

      const confirm = drawer.getByRole("button", { name: "确认分配", exact: true });
      check(await confirm.count() === 1, "exactly one confirmation control is offered");
      check(await confirm.isEnabled(), "the validated preview is confirmable");
      await confirm.click();

      for (let attempt = 0; attempt < 60 && api.actionApplies.length === 0; attempt += 1) {
        await page.waitForTimeout(50);
      }
      check(
        api.actionApplies.filter((proposalId) => proposalId === PROPOSAL_ID).length === 1,
        "confirming sends exactly one apply for the confirmed proposal",
      );
      check(api.durableWriteCount === 1, "the confirmed apply performed exactly one durable write");
      await drawer.getByText("已分配 1 项，1 项待安排", { exact: true })
        .waitFor({ state: "visible", timeout: 15_000 });
      const appliedText = await drawer.innerText();
      check(appliedText.toLowerCase().includes("product-release") && appliedText.includes(READY_TODO), "the result retains its Goal and assigned task");
      check(appliedText.includes(GAP_TODO) && appliedText.includes("待安排 · 尚未加入此目标"), "pending work names its task and actionable reason");
      check(!appliedText.includes("team.plan") && !appliedText.includes("配额包络"), "protocol and original-plan details stay collapsed");
      check(await drawer.getByRole("button", { name: "确认分配", exact: true }).count() === 0, "completed assignments remove the confirmation control");
      await drawer.getByText("查看原计划", { exact: true }).click();
      check(await drawer.getByText("配额包络", { exact: true }).isVisible(), "the original plan remains available on demand");
      await drawer.getByText("查看原计划", { exact: true }).click();
      const resultHeight = await drawer.locator(".personal-team-plan-result").evaluate((element) => element.getBoundingClientRect().height);
      check(resultHeight < 400, "the assignment result remains a compact card");
      await page.screenshot({
        path: resolve(outputDir, "team-plan-applied.png"),
        fullPage: false,
        animations: "disabled",
      });

      // The manager conversation offers the card its own channel produced, so a
      // plan asked for there is confirmable there instead of only under the Goal
      // it staffs. The Goal-scoped card stays in that Goal's workspace.
      await page.locator(".personal-manager-link").first().click();
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      const managerCard = page.locator(".personal-proposal-row", { hasText: MANAGER_PROPOSAL_TITLE });
      try {
        await managerCard.waitFor({ state: "visible", timeout: 15_000 });
      } catch (error) {
        throw new Error(
          `${error.message}; manager rows=${JSON.stringify(await page.locator(".personal-proposal-row").allInnerTexts())};`
          + ` body=${(await page.locator("body").innerText()).slice(0, 1200)}`,
        );
      }
      check(
        await managerCard.getAttribute("data-action-kind") === "team.plan",
        "the manager conversation offers the team plan card it produced",
      );
      await page.screenshot({
        path: resolve(outputDir, "team-plan-manager-conversation.png"),
        fullPage: false,
        animations: "disabled",
      });
      // A lost response occurs after the durable write. A successful list
      // readback must return the stored result, not offer the already-committed
      // operation for another apply.
      await managerCard.click();
      const writesBeforeA = api.durableWriteCount;
      api.loseNextTeamPlanResponse = true;
      await drawer.getByRole("button", { name: "确认分配", exact: true }).click();
      await drawer.getByText("已分配 2 项，1 项待安排", { exact: true }).waitFor({ state: "visible" });
      check(api.actionApplies.filter((id) => id === MANAGER_PROPOSAL_ID).length === 1, "the uncertain manager apply is not retried after applied readback");
      check(api.durableWriteCount === writesBeforeA + 1, "the uncertain manager apply committed once");
      check(await drawer.getByRole("button", { name: "重试原操作", exact: true }).count() === 0, "an applied readback does not expose assignment retry");
      check((await drawer.innerText()).includes("待安排 · 尚未加入此目标"), "the applied readback preserves the original unassigned work");
      const resultCard = page.locator(".personal-proposal-row", { hasText: "团队分配 · 已记录" });
      await drawer.locator(".personal-drawer-close").click();
      await resultCard.waitFor({ state: "visible" });
      check((await resultCard.innerText()).includes("已分配 2 项，1 项待安排"), "closing details keeps the assignment result in the original conversation");
      check(!(await resultCard.innerText()).includes("team.plan"), "the applied card uses a user-facing label instead of a protocol kind");

      // A one-shot failure of the authoritative list read leaves only the
      // cached preview. It must not grant another apply until readback works.
      const readbackContext = await openWorkspacePage(browser, url, {
        apiOptions: { initialActionProposals: [managerTeamPlanProposal({
          proposalId: READBACK_PROPOSAL_ID,
          lanePrefix: "readback",
        })] },
      });
      try {
        const { api: readbackApi, page: readbackPage } = readbackContext;
        await readbackPage.locator(".personal-manager-link").first().click();
        await readbackPage.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
        const readbackDrawer = readbackPage.locator('.personal-context-drawer[data-context-kind="proposal"]');
        const readbackCard = readbackPage.locator(".personal-proposal-row", { hasText: MANAGER_PROPOSAL_TITLE });
        await readbackCard.waitFor({ state: "visible" });
        await readbackCard.click();
        const writesBeforeB = readbackApi.durableWriteCount;
        readbackApi.loseNextTeamPlanResponse = true;
        readbackApi.failActionListAfterLostTeamPlanResponse = true;
        await readbackDrawer.getByRole("button", { name: "确认分配", exact: true }).click();
        const readbackNotice = readbackPage.getByTestId("personal-action-readback-error");
        await readbackNotice.waitFor({ state: "visible" });
        await readbackDrawer.locator(".personal-proposal-state.is-error").waitFor({ state: "visible" });
        check(
          (await readbackDrawer.locator(".personal-proposal-state.is-error").innerText()).includes("尚不能确认完成")
            && (await readbackDrawer.locator(".personal-proposal-state.is-error").innerText()).includes("当前提案不允许重试分配"),
          "failed authoritative readback is shown as unverified and unavailable",
        );
        check(await readbackDrawer.getByRole("button", { name: "确认分配", exact: true }).count() === 0, "cached preview after a failed readback cannot offer first apply again");
        check(await readbackDrawer.getByRole("button", { name: "重试原操作", exact: true }).count() === 0, "cached preview after a failed readback cannot authorize assignment retry");
        check(await readbackDrawer.getByRole("button", { name: "重试分配", exact: true }).count() === 0, "cached preview does not expose the Team Plan retry label");
        check(readbackApi.actionApplies.filter((id) => id === READBACK_PROPOSAL_ID).length === 1, "readback failure does not send another apply");
        check(readbackApi.durableWriteCount === writesBeforeB + 1, "the readback-failure case committed once");
        await readbackNotice.getByRole("button", { name: "重试状态读取", exact: true }).click();
        await readbackDrawer.getByText("已分配 2 项，1 项待安排", { exact: true }).waitFor({ state: "visible" });
        check(readbackApi.actionApplies.filter((id) => id === READBACK_PROPOSAL_ID).length === 1, "state-read retry resolves the original apply without reposting");
        check(readbackApi.durableWriteCount === writesBeforeB + 1, "state-read retry performs no durable write");
        check(await readbackDrawer.getByRole("button", { name: "重试原操作", exact: true }).count() === 0, "applied readback still withholds assignment retry");
      } finally {
        await readbackContext.close();
      }

      // This distinct proposal models the backend's typed post-commit failure:
      // only this stored failed result permits retrying the same identity.
      const retryContext = await openWorkspacePage(browser, url, {
        apiOptions: { initialActionProposals: [managerTeamPlanProposal({
          proposalId: RETRY_PROPOSAL_ID,
          lanePrefix: "retry",
        })] },
      });
      try {
        const { api: retryApi, page: retryPage } = retryContext;
        await retryPage.locator(".personal-manager-link").first().click();
        await retryPage.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
        const retryDrawer = retryPage.locator('.personal-context-drawer[data-context-kind="proposal"]');
        const retryCard = retryPage.locator(".personal-proposal-row", { hasText: MANAGER_PROPOSAL_TITLE });
        await retryCard.waitFor({ state: "visible" });
        await retryCard.click();
        const writesBeforeC = retryApi.durableWriteCount;
        retryApi.failNextTeamPlanAfterCommit = RETRY_PROPOSAL_ID;
        await retryDrawer.getByRole("button", { name: "确认分配", exact: true }).click();
        const retry = retryDrawer.getByRole("button", { name: "重试原操作", exact: true });
        try {
          await retry.waitFor({ state: "visible", timeout: 5_000 });
        } catch (error) {
          throw new Error(`${error.message}; proposal=${JSON.stringify(retryPage.__loopxRuntime.actionProposals.get(RETRY_PROPOSAL_ID))}; drawer=${(await retryDrawer.innerText()).slice(0, 1200)}; applies=${JSON.stringify(retryApi.actionApplies)}; errors=${JSON.stringify(retryContext.errors)}`);
        }
        check(retryApi.actionApplies.filter((id) => id === RETRY_PROPOSAL_ID).length === 1, "the retryable failure keeps the original proposal ID");
        check(retryApi.durableWriteCount === writesBeforeC + 1, "the retryable failure follows one durable commit");
        const failedRetry = retryPage.__loopxRuntime.actionProposals.get(RETRY_PROPOSAL_ID);
        check(
          failedRetry?.status === "failed"
            && failedRetry?.failure?.error_code === "team_plan_commit_failed"
            && failedRetry?.failure?.retry_safe === true
            && failedRetry?.expected_state_fingerprint === "fixture-team-plan-r1"
            && failedRetry?.summary === MANAGER_PROPOSAL_TITLE
            && failedRetry?.normalized_parameters?.plan?.lanes?.[0]?.lane_id === "retry_lane_intake",
          "the failed readback preserves backend failure semantics and the original plan identity",
        );
        const previewCountBeforeRetry = retryApi.actionPreviews.length;
        await retry.click();
        await retryDrawer.getByRole("heading", { name: "已恢复原分配结果", exact: true }).waitFor();
        check(retryApi.actionApplies.filter((id) => id === RETRY_PROPOSAL_ID).length === 2, "retry applies the same failed proposal once");
        check(retryApi.durableWriteCount === writesBeforeC + 1, "retry recovers the committed receipt without a second write");
        check(retryApi.actionPreviews.length === previewCountBeforeRetry, "recovery does not create a replacement proposal");
        const recoveredRetry = retryPage.__loopxRuntime.actionProposals.get(RETRY_PROPOSAL_ID);
        check(
          recoveredRetry?.proposal_id === RETRY_PROPOSAL_ID
            && recoveredRetry?.expected_state_fingerprint === "fixture-team-plan-r1"
            && recoveredRetry?.receipt?.projection_verified === true
            && recoveredRetry?.receipt?.outcome === "team_plan_commit_recovered"
            && recoveredRetry?.receipt?.resource_ids?.goal_id === GOAL_ID
            && recoveredRetry?.receipt?.lanes?.length === 2
            && recoveredRetry?.receipt?.gap_count === 1
            && recoveredRetry?.receipt?.gap_lanes?.[0]?.lane_id === "retry_lane_review",
          "recovery returns the original verified Goal, lanes, and gap receipt",
        );
      } finally {
        await retryContext.close();
      }

      await context.checkpointCoverage();
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      await page.locator(".personal-manager-link").first().click();
      await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await resultCard.waitFor({ state: "visible" });
      await page.screenshot({
        path: resolve(outputDir, "team-plan-result-returned.png"),
        fullPage: false,
        animations: "disabled",
      });
      await resultCard.click();
      await drawer.getByText("已分配 2 项，1 项待安排", { exact: true }).waitFor({ state: "visible" });
      check(api.actionApplies.filter((id) => id === MANAGER_PROPOSAL_ID).length === 1, "reload reads back the result without reapplying the team plan");
      check(api.durableWriteCount === writesBeforeA + 1, "reopening the assignment result writes no work");
      await drawer.locator(".personal-drawer-close").click();
      const managerResult = page.getByRole("region", {name: "团队结果回到管家"});
      await managerResult.getByText("团队任务已分配，尚无可核验的已采用结果。").waitFor();
      check(await managerResult.getByRole("table").count() === 0, "an accepted result from another Todo is never returned to the manager");
      const managedDigest = "b".repeat(64);
      let managedState = "current";
      await page.route("**/api/chat/goal-results**", route => {
        const request = new URL(route.request().url());
        if (request.pathname.endsWith("/todo_a1a1a1a1a1a1")) {
          if (managedState === "stale") return route.fulfill({status: 409, json: {error: "acceptance changed"}});
          return route.fulfill({json: {ok: true,
            goal_id: managedState === "wrong-goal" ? "other-goal" : GOAL_ID,
            todo_id: "todo_a1a1a1a1a1a1", text: "# Verified managed conclusion\n\n| Measure | Value |\n| --- | --- |\n| Cash flow | 25 |\n",
            result: {sha256: managedDigest, content_type: "text/markdown", producer_agent_id: "lead"},
          }});
        }
        const planRow = {todo_id: "todo_a1a1a1a1a1a1", title: "Cash flow review",
          producer_agent_id: "lead", sha256: managedDigest, content_type: "text/markdown", size_bytes: 75};
        if (managedState === "paged-plan-report") {
          // A matching report behind the retired eight-page budget: 360 unrelated
          // rows first, then the plan's own row on the tenth page.
          const unrelated = Array.from({length: 360}, (_, index) => ({todo_id: `todo_history_${index}`,
            title: "Historical report", producer_agent_id: "lead", sha256: managedDigest,
            content_type: "text/markdown", size_bytes: 75}));
          const all = [...unrelated, planRow];
          const offset = Number(request.searchParams.get("cursor") ?? "0");
          return route.fulfill({json: {ok: true, items: all.slice(offset, offset + 40),
            total: all.length, unavailable_count: 0, unavailable_todo_ids: [],
            next_cursor: offset + 40 < all.length ? String(offset + 40) : null}});
        }
        const unavailableTodoIds = managedState === "unrelated-unavailable" ? ["todo_history_unreadable"] : [];
        const items = [{todo_id: managedState === "other-todo" ? "todo_unrelated" : "todo_a1a1a1a1a1a1",
          title: "Cash flow review", producer_agent_id: "lead", sha256: managedDigest,
          content_type: "text/markdown", size_bytes: 75}];
        return route.fulfill({json: {ok: true, items, total: items.length + unavailableTodoIds.length,
          next_cursor: null, unavailable_count: unavailableTodoIds.length,
          unavailable_todo_ids: unavailableTodoIds}});
      });
      await managerResult.getByRole("button", {name: "刷新结果"}).click();
      await managerResult.getByText("托管团队报告 · 已验收，采用尚未核验").waitFor();
      check((await managerResult.innerText()).includes("Verified managed conclusion"),
        "an exact accepted managed Todo report returns to the plan's original manager conversation");
      check((await managerResult.innerText()).includes("采用尚未核验"),
        "accepted managed work does not falsely claim requester adoption");
      await page.screenshot({path: resolve(outputDir, "team-plan-manager-managed-result.png"), fullPage: false, animations: "disabled"});
      await page.setViewportSize({width: 390, height: 844});
      check(await managerResult.evaluate(element => element.scrollWidth <= element.clientWidth),
        "the managed report fits the original conversation on mobile");
      await page.screenshot({path: resolve(outputDir, "team-plan-manager-managed-result-mobile.png"), fullPage: false, animations: "disabled"});
      await page.setViewportSize({width: 1512, height: 982});
      managedState = "stale";
      await managerResult.getByRole("button", {name: "刷新结果"}).click();
      await managerResult.getByText("托管报告无法核验；旧内容已撤回。").waitFor();
      check(!(await managerResult.innerText()).includes("Verified managed conclusion"),
        "a failed exact read withdraws the previous managed report");
      managedState = "wrong-goal";
      await managerResult.getByRole("button", {name: "刷新结果"}).click();
      await managerResult.getByText("托管报告无法核验；旧内容已撤回。").waitFor();
      check(!(await managerResult.innerText()).includes("Verified managed conclusion"),
        "a report from another Goal cannot appear in this conversation");
      managedState = "other-todo";
      await managerResult.getByRole("button", {name: "刷新结果"}).click();
      await managerResult.getByText("团队任务已分配，尚无可核验的已采用结果。").waitFor();
      check(!(await managerResult.innerText()).includes("Verified managed conclusion"),
        "a report from another Todo cannot appear in this plan");
      managedState = "unrelated-unavailable";
      await managerResult.getByRole("button", {name: "刷新结果"}).click();
      await managerResult.getByText("托管团队报告 · 已验收，采用尚未核验").waitFor();
      check((await managerResult.innerText()).includes("Verified managed conclusion"),
        "an unrelated unreadable Goal result must not hide this plan's accepted report");
      // Withdraw first, so the paginated case can only pass on a fresh read
      // rather than on the report left over from the previous state.
      managedState = "other-todo";
      await managerResult.getByRole("button", {name: "刷新结果"}).click();
      await managerResult.getByText("团队任务已分配，尚无可核验的已采用结果。").waitFor();
      managedState = "paged-plan-report";
      await managerResult.getByRole("button", {name: "刷新结果"}).click();
      await managerResult.getByText("托管团队报告 · 已验收，采用尚未核验").waitFor();
      check((await managerResult.innerText()).includes("Verified managed conclusion"),
        "a matching report beyond the retired eight-page budget stays discoverable");
      // Return the fixture to the no-managed-result state the later adoption
      // checks were written against.
      managedState = "other-todo";
      await managerResult.getByRole("button", {name: "刷新结果"}).click();
      await managerResult.getByText("团队任务已分配，尚无可核验的已采用结果。").waitFor();
      const goalSession = [...page.__loopxRuntime.sessions.values()].find(session => session.channel_id === `goal.${GOAL_ID}`);
      check(Boolean(goalSession), "the original Goal conversation has a session for result readback");
      let goalSessionId = "";
      if (goalSession) {
        goalSessionId = goalSession.session_id;
        const mode = page.__loopxRuntime.loopxModes.get(goalSession.session_id);
        check(Boolean(mode), "the Goal session exposes a complete LoopX mode readback");
        mode.settings.agent_id = "lead";
        mode.fixturePlanTodoId = "todo_a1a1a1a1a1a1";
        mode.fixtureAdoptionState = "current";
        // Unrelated ordinary sessions are common in a long-lived Goal. Their
        // team API rejects operations, and even nine such sessions must not
        // hide this coordinator's accepted, currently adopted result.
        for (let index = 0; index < 9; index += 1) {
          const sessionId = `session-ordinary-${index}`;
          page.__loopxRuntime.sessions.set(sessionId, {
            ...goalSession, session_id: sessionId, agent_id: `ordinary-${index}`,
          });
        }
        // Other coordinator conversations of the same Goal are just as unrelated:
        // their own work index names other Todos, never this plan's.
        for (let index = 0; index < 9; index += 1) {
          const sessionId = `session-coordinator-${index}`;
          page.__loopxRuntime.sessions.set(sessionId, {
            ...goalSession, session_id: sessionId, agent_id: `coordinator-${index}`,
          });
          page.__loopxRuntime.loopxModes.set(sessionId, {
            ...mode, session_id: sessionId,
            settings: {...mode.settings, agent_id: `coordinator-${index}`},
            fixturePlanTodoId: null,
            deliveries: [{operation_id: `other-${index}`, agent_id: "other-agent",
              todo_id: `todo_other_${index}`, status: "accepted"}],
          });
        }
        await managerResult.getByRole("button", {name: "刷新结果"}).click();
        await managerResult.getByRole("table").waitFor();
        check((await managerResult.innerText()).includes("Reviewed cash allocation"), "the accepted adopted report returns inside the original manager conversation");
        check(!api.loopxModeRequests.some(request => request.operation === "operations"
          && /^session-(ordinary|coordinator)-/.test(request.sessionId)), "unrelated Goal conversations are not queried for delegation operations");
        check(await managerResult.getByLabel("证据内容: report.md").count() === 1, "the adopted Markdown report is preferred over machine JSON");
        await page.screenshot({path: resolve(outputDir, "team-plan-manager-adopted-result.png"), fullPage: false, animations: "disabled"});
        await page.setViewportSize({width: 390, height: 844});
        check(await managerResult.evaluate(element => element.scrollWidth <= element.clientWidth), "the returned report remains readable on mobile");
        await page.screenshot({path: resolve(outputDir, "team-plan-manager-adopted-result-mobile.png"), fullPage: false, animations: "disabled"});
        await page.setViewportSize({width: 1512, height: 982});
        mode.fixtureInventoryGap = true;
        await managerResult.getByRole("button", {name: "刷新结果"}).click();
        await managerResult.getByText("团队结果或采用证据无法核验，请到 Goal 查看版本关系。").waitFor();
        check(await managerResult.getByRole("table").count() === 0, "an unreadable earlier inventory page withholds the adopted report");
        mode.fixtureInventoryGap = false;
        // The related conversation's own inventory being unreadable is different
        // from an unrelated conversation existing: this must withdraw the report.
        mode.fixtureTeamInventoryError = true;
        await managerResult.getByRole("button", {name: "刷新结果"}).click();
        await managerResult.getByText("团队结果或采用证据无法核验，请到 Goal 查看版本关系。").waitFor();
        check(await managerResult.getByRole("table").count() === 0, "an unreadable inventory for the related conversation withholds the report");
        mode.fixtureTeamInventoryError = false;
        mode.fixtureAdoptionState = "unavailable";
        mode.fixtureTeamReadDelayMs = 1000;
        await managerResult.getByRole("button", {name: "刷新结果"}).click();
        await managerResult.getByText("正在核验团队结果…").waitFor({state: "visible", timeout: 500});
        check(await managerResult.getByRole("table").count() === 0, "refresh immediately withdraws the old accepted report while the new read is pending");
        await managerResult.getByText("团队结果或采用证据无法核验，请到 Goal 查看版本关系。").waitFor();
        mode.fixtureTeamReadDelayMs = 0;
        check(await managerResult.getByRole("table").count() === 0, "unavailable adoption immediately withdraws the formerly visible report");
        check(api.durableWriteCount === 2, "result readback does not create another assignment or turn");
        await managerResult.getByRole("button", {name: "查看证据与任务"}).click();
        const goalChatTab = page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: "对话", exact: true});
        await goalChatTab.waitFor({state: "visible"});
        check(await goalChatTab.getAttribute("aria-current") === "page", "the result offers a direct path to Goal conversation evidence and intervention");
      }
      // The harness collects both uncaught page errors and console errors; a
      // dev-server resource status is not a client-side exception, so only the
      // former is a failure here.
      const scriptErrors = context.errors.filter((message) => !message.startsWith("Failed to load resource"));
      check(
        scriptErrors.length === 0,
        `no client-side exception was raised (${scriptErrors.join(" | ")})`,
      );
      const injected = [
        `503 ${new URL(url).origin}/api/actions/${MANAGER_PROPOSAL_ID}/apply`,
        `409 ${new URL(url).origin}/api/chat/goal-results/todo_a1a1a1a1a1a1?goal_id=${GOAL_ID}`,
        `503 ${new URL(url).origin}/api/chat/sessions/${goalSessionId}/loopx`,
      ];
      check(
        failedResponses.length === injected.length
        && failedResponses.every(response => injected.includes(response)),
        `only the injected failures were observed (${failedResponses.join(" | ")})`,
      );
    } finally {
      await context.close();
    }
    if (failures.length) throw new Error(failures.join(" | "));
    return { coverageEntries: context.coverageEntries, note: notes.join(" ") };
  },
};
