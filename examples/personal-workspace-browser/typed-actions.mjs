import { resolve } from "node:path";

import {
  goalCapabilityCatalog,
  outputDir,
  waitForInputValue,
} from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// A category switch is a state transition, not a settled fact: the panel can
// still be mid-mount when a one-shot count runs, which is how this assertion
// failed on CI while the same tree passed on main. Wait for the declared count
// and, when it never settles, name what the page actually hosted so the next
// failure is attributable without a local reproduction.
async function waitForSelectorCount(page, selector, expected, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  let count = await page.locator(selector).count();
  while (count !== expected && Date.now() < deadline) {
    await page.waitForTimeout(100);
    count = await page.locator(selector).count();
  }
  if (count === expected) {
    return;
  }
  const hosted = await page.evaluate((target) => [...document.querySelectorAll(target)].map((node) => {
    const rect = node.getBoundingClientRect();
    const section = node.closest("section, main, div");
    return `${node.tagName.toLowerCase()}.${node.className.toString().trim().split(/\s+/).join(".")}`
      + ` section=${section ? section.className.toString().trim().split(/\s+/)[0] : "<none>"}`
      + ` visible=${rect.width > 0 && rect.height > 0}`;
  }), selector);
  throw new Error(
    `Expected exactly ${expected} ${selector} panel(s) after the category settled, found ${count}`
    + `${hosted.length ? `: ${hosted.join(" | ")}` : ""}`,
  );
}

function operationProposal({ id, title, lifecycleState, status, resultDelivery = null }) {
  const outcomeObserved = lifecycleState === "outcome_observed";
  return {
    schema_version: "loopx_chat_action_proposal_v1",
    proposal_id: id,
    action_kind: "operation.execute",
    summary: title,
    normalized_parameters: {
      goal_id: "product-release",
      payload: { private_fixture_marker: "must-not-render" },
      authorized_principals: ["lark:ou_private_fixture"],
      projection: {
        schema_version: "loopx_operation_projection_v0",
        title,
        subtitle: "Synthetic finance simulator",
        focus: "BUY 1 SYNTH @ 10 TEST",
        fields: [
          { label: "Order", value: "Limit · GTC" },
          { label: "Maximum fee", value: "0.10 TEST" },
        ],
        warning: "Simulation only. No venue, signer, wallet, or transfer authority.",
        simulated: true,
      },
    },
    context: { kind: "goal", goal_id: "product-release" },
    expected_state_fingerprint: "fixture-operation-r1",
    permission_classification: "protected",
    validation_evidence: ["synthetic operation fixture"],
    available_transitions: [],
    status,
    receipt: outcomeObserved ? { projection_verified: true, receipt_id: `${id}-outcome` } : null,
    stale: null,
    gate: status === "gated" ? {
      kind: "human_confirmation_required",
      summary: "Confirm the exact request in the bound Feishu group.",
      next_action: "Use the original non-forwardable group card.",
    } : null,
    operation: {
      schema_version: "loopx_operation_envelope_v0",
      lifecycle_state: lifecycleState,
      operation_id: id,
      confirmation_digest: "a".repeat(64),
      payload_digest: "b".repeat(64),
      projection_digest: "c".repeat(64),
      expires_at: "2026-09-15T10:00:00Z",
      delivery: { provider: "lark", message_id: `${id}-message` },
      confirmation: outcomeObserved ? { provider: "lark" } : null,
      claim: outcomeObserved ? { claim_id: `${id}-claim` } : null,
      outcome: outcomeObserved ? {
        schema_version: "loopx_operation_outcome_v0",
        outcome: "simulated_filled",
        projection_verified: true,
        simulation: true,
        external_write_performed: false,
      } : null,
      result_delivery: resultDelivery,
    },
    created_at: "2026-09-14T01:00:00Z",
    updated_at: "2026-09-14T01:00:01Z",
  };
}

export const typedActionsScenario = {
  id: "typed-actions",
  async run({ browser, collectCoverage, url }) {
    const operationUi = await openWorkspacePage(browser, url, {
      apiOptions: {
        initialActionProposals: [
          ...["edit", "complete", "agent-complete", "monitor-stop"].map(operation => ({
            schema_version: "loopx_chat_action_proposal_v1",
            proposal_id: `reviewed-${operation}-recovery`, action_kind: operation === "monitor-stop" ? "monitor.update" : "todo.update",
            summary: `Recover a committed Todo ${operation}`, status: "failed",
            normalized_parameters: {goal_id: "product-release", todo_id: "todo_reviewed", operation: operation === "agent-complete" ? "complete" : operation === "monitor-stop" ? "stop" : operation, note: "Reviewed observation"},
            context: {kind: "goal", goal_id: "product-release"},
            expected_state_fingerprint: "reviewed-basis", permission_classification: "durable_write",
            validation_evidence: ["Canonical dry-run validated the edit."], available_transitions: ["apply", "cancel"],
            canonical_update_basis: {schema_version: ["agent-complete", "monitor-stop"].includes(operation)
              ? "loopx_chat_canonical_terminal_basis_v0" : "loopx_chat_canonical_update_basis_v0",
              provider_revision: "reviewed-revision", registry_sha256: "a".repeat(64), source_authority: "sqlite_v0"},
            failure: {error_code: "canonical_update_projection_pending"}, receipt: null, stale: null,
            created_at: "2026-09-14T01:00:00Z", updated_at: "2026-09-14T01:00:01Z",
          })),
          operationProposal({
            id: "operation-awaiting-confirmation",
            title: "Simulated order awaiting group confirmation",
            lifecycleState: "awaiting_confirmation",
            status: "gated",
          }),
          operationProposal({
            id: "operation-result-pending",
            title: "Simulation result awaiting card readback",
            lifecycleState: "outcome_observed",
            status: "applied",
          }),
          operationProposal({
            id: "operation-result-verified",
            title: "Verified simulated order result",
            lifecycleState: "outcome_observed",
            status: "applied",
            resultDelivery: {
              provider: "lark",
              message_id: "operation-result-verified-message",
              transport: "callback_update",
            },
          }),
        ],
      },
    });
    try {
      const { page } = operationUi;
      await page.locator(".personal-goal-link", { hasText: "Product Release" }).click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();

      // A loaded failure must remain discoverable and retry its original id,
      // rather than being hidden or regenerated into a second business edit.
      for (const operation of ["edit", "complete", "agent-complete", "monitor-stop"]) {
        await page.locator(".personal-proposal-row", {hasText: `Recover a committed Todo ${operation}`}).click();
        const recovery = page.locator('.personal-context-drawer[data-context-kind="proposal"]');
        await recovery.getByText("操作已提交，展示尚未同步。重试此操作以恢复当前视图。", {exact: true}).waitFor({state: "visible"});
        const previewsBeforeRecovery = operationUi.api.actionPreviews.length;
        await recovery.getByRole("button", {name: "重试原操作", exact: true}).click();
        await recovery.getByText("已应用，LoopX 状态将刷新。", {exact: true}).waitFor({state: "visible"});
        if (!operationUi.api.actionApplies.includes(`reviewed-${operation}-recovery`)) throw new Error("Recovery lost the original command identity");
        if (operationUi.api.actionPreviews.length !== previewsBeforeRecovery) throw new Error("Recovery created a replacement proposal");
        await page.getByRole("button", { name: /关闭详情/ }).click();
      }

      const pendingResult = page.locator(".personal-proposal-row", {
        hasText: "Simulation result awaiting card readback",
      });
      try {
        await pendingResult.waitFor({ state: "visible" });
      } catch (error) {
        throw new Error(`${error.message}; errors=${operationUi.errors.join(" | ")}; proposals=${await page.locator(".personal-proposal-row").allInnerTexts()}; body=${(await page.locator("body").innerText()).slice(0, 2000)}`);
      }
      if (!(await pendingResult.innerText()).includes("结果卡回传待恢复")) {
        throw new Error("Pending result did not disclose unverified card delivery");
      }
      await pendingResult.click();
      const pendingDrawer = page.locator('.personal-context-drawer[data-context-kind="proposal"]');
      await pendingDrawer.getByText("等待回传并核验原群卡片", { exact: true }).waitFor({ state: "visible" });
      const pendingText = await pendingDrawer.innerText();
      for (const privateValue of ["must-not-render", "ou_private_fixture"]) {
        if (pendingText.includes(privateValue)) throw new Error(`Operation projection leaked ${privateValue}`);
      }
      if (await pendingDrawer.getByRole("button", { name: /重新生成|确认并应用|拒绝/ }).count()) {
        throw new Error("Pending group operation exposed a local mutation control");
      }
      await page.screenshot({ path: resolve(outputDir, "operation-result-pending.png"), fullPage: false, animations: "disabled" });
      await page.getByRole("button", { name: /关闭详情/ }).click();

      const verifiedResult = page.locator(".personal-proposal-row", {
        hasText: "Verified simulated order result",
      });
      await verifiedResult.waitFor({ state: "visible" });
      if (!(await verifiedResult.innerText()).includes("结果已核验")) {
        throw new Error("Verified result lost its exact delivery status");
      }
      await verifiedResult.click();
      const verifiedDrawer = page.locator('.personal-context-drawer[data-context-kind="proposal"]');
      await verifiedDrawer.getByText("已在原群卡片完成回读核验", { exact: true }).waitFor({ state: "visible" });
      await page.screenshot({ path: resolve(outputDir, "operation-result-verified.png"), fullPage: false, animations: "disabled" });
      await page.getByRole("button", { name: /关闭详情/ }).click();

      const gatedSummary = page.locator(".personal-gated-summary");
      await gatedSummary.locator("summary").click();
      const awaiting = gatedSummary.locator(".personal-proposal-row", {
        hasText: "Simulated order awaiting group confirmation",
      });
      await awaiting.waitFor({ state: "visible" });
      if (!(await awaiting.innerText()).includes("前往飞书群确认")) {
        throw new Error("Awaiting operation did not route confirmation to Feishu");
      }
      await awaiting.click();
      const awaitingDrawer = page.locator('.personal-context-drawer[data-context-kind="proposal"]');
      await awaitingDrawer.getByText("前往飞书群确认", { exact: true }).waitFor({ state: "visible" });
      if (await awaitingDrawer.getByRole("button", { name: /确认并应用|拒绝|稍后处理|重新生成/ }).count()) {
        throw new Error("Awaiting group operation exposed a local decision control");
      }
      await page.screenshot({ path: resolve(outputDir, "operation-awaiting-group-confirmation.png"), fullPage: false, animations: "disabled" });
    } finally {
      await operationUi.close();
    }

    const storedWorkspaceGate = {
      schema_version: "loopx_chat_action_proposal_v1",
      proposal_id: "stored-goal-create-workspace-gate",
      action_kind: "goal.create",
      summary: "Create stored workspace Goal",
      normalized_parameters: { goal_id: "product-release", title: "Stored workspace Goal", workspace_ref: "current" },
      context: { kind: "goal_directory", goal_id: "product-release" },
      expected_state_fingerprint: "fixture-workspace-gate-r1",
      permission_classification: "durable_write",
      validation_evidence: ["workspace selection required"],
      available_transitions: ["regenerate", "reject", "defer"],
      status: "gated",
      receipt: null,
      stale: null,
      gate: {
        kind: "workspace_selection_required",
        summary: "Select one server-configured workspace before creating this Goal.",
        next_action: "Select a workspace to regenerate the confirmation preview.",
        candidates: [{ workspace_ref: "workspace-fixture", label: "Workspace 1" }],
      },
      created_at: "2026-09-14T01:00:00Z",
      updated_at: "2026-09-14T01:00:01Z",
    };
    const workspaceGateUi = await openWorkspacePage(browser, url, {
      apiOptions: { initialActionProposals: [storedWorkspaceGate] },
    });
    try {
      const { api, page } = workspaceGateUi;
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      await page.locator(".personal-goal-link", { hasText: "Product Release" }).click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await page.locator(".personal-gated-summary summary").click();
      const proposalRow = page.locator(".personal-proposal-row", { hasText: "Stored workspace Goal" });
      try {
        await proposalRow.waitFor({ state: "visible", timeout: 10_000 });
      } catch (error) {
        throw new Error(`${error.message}; proposals=${await page.locator(".personal-proposal-row").allInnerTexts()}; errors=${workspaceGateUi.errors.join(" | ")}; body=${(await page.locator("body").innerText()).slice(0, 2000)}`);
      }
      await proposalRow.click();
      const drawer = page.locator('.personal-context-drawer[data-context-kind="proposal"]');
      if ((await drawer.innerText()).includes("需要宿主确认")) {
        throw new Error("Workspace selection gate was mislabeled as host-only confirmation");
      }
      const writesBeforeSelection = api.durableWriteCount;
      await drawer.getByRole("button", { name: /Workspace 1/ }).click();
      await page.getByRole("button", { name: "创建 Goal 并开始首轮", exact: true }).waitFor({ state: "visible" });
      const regenerated = api.actionPreviews.at(-1);
      if (regenerated?.normalized_parameters.workspace_ref !== "workspace-fixture") {
        throw new Error(`Stored workspace selection did not regenerate the Goal preview: ${JSON.stringify(regenerated)}`);
      }
      if (api.durableWriteCount !== writesBeforeSelection) throw new Error("Selecting a workspace applied without confirmation");
      await page.getByRole("button", { name: "创建 Goal 并开始首轮", exact: true }).click();
      await page.getByText("已应用，LoopX 状态将刷新。", { exact: true }).waitFor({ state: "visible" });
      if (!api.actionApplies.includes(regenerated.proposalId)) throw new Error("Confirmation did not apply the regenerated preview");
      if (api.durableWriteCount !== writesBeforeSelection + 1) throw new Error("Confirmation did not write exactly one Goal");
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      const stored = await page.evaluate(async (proposalId) => {
        const response = await fetch("/api/actions?goal_id=product-release");
        const payload = await response.json();
        return payload.proposals?.find((proposal) => proposal.proposal_id === proposalId);
      }, regenerated.proposalId);
      if (stored?.status !== "applied" || stored.receipt?.projection_verified !== true) {
        throw new Error(`Confirmed workspace Goal did not survive readback: ${JSON.stringify(stored)}`);
      }
    } finally {
      await workspaceGateUi.close();
    }

    // Real Goal button -> typed preview -> compiler -> drawer/apply, with only
    // the service boundary controlled. No test computes the plan under review.
    for (const width of [1512, 390]) {
      const review = await openWorkspacePage(browser, url, { viewport: { width, height: 982 } });
      const { page: reviewPage, api: reviewApi } = review;
      try {
        for (const [patch, mode] of [
          [{ permission_classification: "protected" }, "review"],
          [{ validation_evidence: [] }, "refresh"],
          [{ stale: { current_state_fingerprint: "fixture-r2" } }, "refresh"],
        ]) {
          const before = reviewApi.actionApplies.length;
          reviewApi.nextLifecycleProposalPatch = patch;
          if (width < 640) await reviewPage.locator(".personal-mobile-menu").click();
          await reviewPage.getByRole("button", { name: "停止 Product Release", exact: true }).click();
          await reviewPage.locator(`[data-action-review="${mode}"]`).waitFor({ state: "visible" });
          if (reviewApi.actionApplies.length !== before) throw new Error("Unsafe lifecycle preview applied directly");
          if (reviewApi.goalActivationStates.get("product-release") !== "active") throw new Error("Unsafe preview changed state");
          if (mode === "refresh" && !(await reviewPage.getByRole("button", { name: "停止 Goal", exact: true }).isDisabled())) throw new Error("Incomplete or stale preview remained applicable");
          await reviewPage.getByRole("button", { name: "关闭", exact: true }).click();
        }
        for (const evidence of [[null], [""], [" \t"], [{}], ["valid", null], ["valid", {}], ["valid", ""]]) {
          const before = reviewApi.actionApplies.length;
          reviewApi.nextLifecycleProposalPatch = { validation_evidence: evidence };
          if (width < 640) await reviewPage.locator(".personal-mobile-menu").click();
          await reviewPage.getByRole("button", { name: "停止 Product Release", exact: true }).click();
          await reviewPage.locator(".personal-action-feedback").filter({ hasText: "validation_evidence" }).waitFor({ state: "visible" });
          if (reviewApi.actionApplies.length !== before) throw new Error("Malformed evidence bypassed the transport schema and applied");
          if (reviewApi.goalActivationStates.get("product-release") !== "active") throw new Error("Malformed evidence changed durable state");
          if (await reviewPage.locator('[data-action-review="direct"]').count()) throw new Error("Malformed evidence produced a direct review plan");
          if (width < 640) await reviewPage.locator(".personal-mobile-menu").click();
          await reviewPage.getByRole("button", { name: "停止 Product Release", exact: true }).waitFor({ state: "visible" });
          if (width < 640) await reviewPage.keyboard.press("Escape");
        }
        reviewApi.nextLifecycleProposalPatch = { normalized_parameters: { goal_id: "other-goal", operation: "stop" }, context: { goal_id: "other-goal" } };
        const beforeMismatch = reviewApi.actionApplies.length;
        if (width < 640) await reviewPage.locator(".personal-mobile-menu").click();
        await reviewPage.getByRole("button", { name: "停止 Product Release", exact: true }).click();
        await reviewPage.getByText("预览目标与请求的 Goal 或操作不一致", { exact: false }).waitFor({ state: "visible" });
        if (reviewApi.actionApplies.length !== beforeMismatch) throw new Error("Mismatched response target was applied");
        for (const outcome of ["stale", "unverified", "mismatch-id", "mismatch-goal", "mismatch-operation"]) {
          reviewApi.nextLifecycleApplyOutcome = outcome;
          if (width < 640) await reviewPage.locator(".personal-mobile-menu").click();
          await reviewPage.getByRole("button", { name: "停止 Product Release", exact: true }).click();
          await reviewPage.locator(`[data-action-review="${outcome === "stale" ? "refresh" : "repair"}"]`).waitFor({ state: "visible" });
          if (await reviewPage.getByText("已应用，LoopX 状态将刷新。", { exact: true }).count()) throw new Error("Unverified or stale apply displayed completion");
          if (reviewApi.goalActivationStates.get("product-release") !== "active") throw new Error("Failed apply lost rollback");
          if (outcome !== "stale" && await reviewPage.getByText("应用失败，没有写入任何变更。", { exact: true }).count()) throw new Error("Unverified readback falsely claimed no write");
          await reviewPage.screenshot({ path: resolve(outputDir, `action-review-${width}-${outcome}.png`), fullPage: false, animations: "disabled" });
          await reviewPage.getByRole("button", { name: "关闭", exact: true }).click();
        }
      } finally {
        await review.close();
      }
    }
    const capabilityOff = await openWorkspacePage(browser, url, {
      apiOptions: { goalSubagentConfigurationEnabled: false },
    });
    try {
      await capabilityOff.page.locator(".personal-goal-link").first().click();
      await capabilityOff.page.getByRole("button", { name: "概览", exact: true }).click();
      await capabilityOff.page.getByRole("button", { name: "Goal 信息", exact: true }).click();
      await capabilityOff.page.locator(".personal-drawer-header").waitFor({ state: "visible" });
      if (await capabilityOff.page.locator(".personal-goal-subagents").count()) {
        throw new Error("Capability-off Dashboard exposed Goal sub-agent controls");
      }
    } finally {
      await capabilityOff.close();
    }

    const context = await openWorkspacePage(browser, url, { collectCoverage });
    const { api, checkpointCoverage, errors: pageErrors, page } = context;
    const failures = [];
    const notes = [];
    const pass = (criterion, note) => notes.push(`${criterion}: ${note}`);
    const fail = (criterion, note) => failures.push(`${criterion}: ${note}`);
    try {
      const stoppedDirectory = page.locator(".personal-stopped-goals");
      await page.waitForFunction(
        () => document.querySelectorAll(".personal-stopped-goals .personal-goal-row").length === 2,
        null,
        { timeout: 3_000 },
      );
      await page.locator(".personal-goal-link").filter({ hasText: "Product Release" }).click();

      const writesBeforeLifecyclePreview = api.durableWriteCount;
      const statusRequestsBeforeStop = api.statusRequestCount;
      api.nextLifecyclePreviewDelayMs = 900;
      api.nextLifecycleApplyDelayMs = 900;
      api.nextStatusDelayMs = 900;
      await page.getByRole("button", { name: "停止 Product Release", exact: true }).click();
      await page.waitForFunction(
        () => document.querySelectorAll(".personal-goal-list:not(.is-stopped) .personal-goal-row").length === 4,
        null,
        { timeout: 600 },
      );
      const pendingStop = page.locator('.personal-stopped-goals .personal-goal-lifecycle[aria-label="恢复 Product Release"]');
      if (await pendingStop.getAttribute("aria-busy") !== "true") throw new Error("Pending Goal stop does not expose accessible progress");
      await page.waitForTimeout(1_000);
      const stopPreview = api.actionPreviews.findLast((preview) => preview.action_kind === "goal.lifecycle" && preview.normalized_parameters.operation === "stop");
      if (!stopPreview || stopPreview.normalized_parameters.goal_id !== "product-release") throw new Error("Goal stop did not create the expected typed preview");
      if (api.durableWriteCount !== writesBeforeLifecyclePreview) throw new Error("Goal stop wrote durable state before its typed apply completed");
      if (await page.getByText("确认执行", { exact: true }).count()) throw new Error("Goal stop still opened a redundant confirmation drawer");
      if ((await page.locator(".personal-goal-list:not(.is-stopped) .personal-goal-row").count()) !== 4) throw new Error("Optimistic Goal stop did not update the active sidebar immediately");
      await page.waitForTimeout(2_000);
      if (api.statusRequestCount <= statusRequestsBeforeStop) throw new Error("Successful Goal stop did not start background full-status reconciliation");
      if ((await page.locator(".personal-goal-list:not(.is-stopped) .personal-goal-row").count()) !== 4) throw new Error("Full-status reconciliation reverted a successful Goal stop");
      await stoppedDirectory.locator("summary").click();
      await page.getByRole("button", { name: "恢复 Product Release", exact: true }).click();
      await page.getByText("确认执行", { exact: true }).waitFor({ state: "visible" });
      const resumePreview = api.actionPreviews.findLast((preview) => preview.action_kind === "goal.lifecycle" && preview.normalized_parameters.operation === "resume");
      await page.locator('[data-action-review="review"]').filter({ hasText: "恢复自动调度前需要确认" }).waitFor({ state: "visible" });
      if (!resumePreview || resumePreview.normalized_parameters.goal_id !== "product-release") throw new Error("Goal resume did not create the expected typed preview");
      if (api.durableWriteCount !== writesBeforeLifecyclePreview + 1) throw new Error("Goal resume preview wrote state before owner confirmation");
      api.nextLifecycleApplyDelayMs = 900;
      await page.getByRole("button", { name: "恢复 Goal", exact: true }).click();
      await page.getByRole("button", { name: "停止 Product Release", exact: true }).waitFor({ state: "attached", timeout: 600 });
      await page.waitForTimeout(1_100);
      if ((await page.locator(".personal-goal-list:not(.is-stopped) .personal-goal-row").count()) !== 5) throw new Error("Full-status reconciliation reverted a successful Goal resume");

      api.nextStatusDelayMs = 1_600;
      await page.getByRole("button", { name: "停止 Product Release", exact: true }).click();
      await page.getByRole("button", { name: "恢复 Product Release", exact: true }).waitFor({ state: "attached" });
      await page.getByRole("button", { name: "恢复 Product Release", exact: true }).click();
      await page.getByText("确认执行", { exact: true }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: "恢复 Goal", exact: true }).click();
      await page.getByRole("button", { name: "停止 Product Release", exact: true }).waitFor({ state: "attached" });
      await page.waitForTimeout(1_800);
      if ((await page.locator(".personal-goal-list:not(.is-stopped) .personal-goal-row").count()) !== 5) throw new Error("A stale background response overwrote a newer optimistic Goal transition");

      api.failNextLifecyclePreview = true;
      api.nextLifecyclePreviewDelayMs = 900;
      await page.getByRole("button", { name: "停止 Product Release", exact: true }).click();
      await page.getByRole("button", { name: "恢复 Product Release", exact: true }).waitFor({ state: "attached", timeout: 600 });
      await page.getByRole("button", { name: "停止 Product Release", exact: true }).waitFor({ state: "attached", timeout: 2_000 });
      if (api.goalActivationStates.get("product-release") !== "active") throw new Error("Rejected Goal stop preview mutated the durable fixture state");

      api.failNextLifecycleApply = true;
      api.nextLifecycleApplyDelayMs = 900;
      await page.getByRole("button", { name: "停止 Product Release", exact: true }).click();
      await page.getByRole("button", { name: "恢复 Product Release", exact: true }).waitFor({ state: "attached", timeout: 600 });
      await page.getByRole("button", { name: "停止 Product Release", exact: true }).waitFor({ state: "attached", timeout: 2_000 });
      if (api.goalActivationStates.get("product-release") !== "active") throw new Error("Rejected Goal stop mutated the durable fixture state");
      api.failNextStatusRequest = true;
      api.nextStatusDelayMs = 400;
      await page.getByRole("button", { name: "停止 Product Release", exact: true }).click();
      await page.waitForTimeout(900);
      if (await page.getByText("无法读取状态", { exact: false }).count()) throw new Error("Background lifecycle reconciliation replaced the workspace with a fatal status error");
      if ((await page.locator(".personal-goal-list:not(.is-stopped) .personal-goal-row").count()) !== 4) throw new Error("Background reconciliation failure reverted the successful optimistic Goal state");
      const closeLifecycleDrawer = page.getByRole("button", { name: "关闭", exact: true });
      if (await closeLifecycleDrawer.count()) await closeLifecycleDrawer.click();
      await page.emulateMedia({ reducedMotion: "reduce" });
      const stoppedChevronTransition = await stoppedDirectory.locator("summary > svg:first-child").evaluate((element) => getComputedStyle(element).transitionDuration);
      if (stoppedChevronTransition !== "0s") throw new Error(`Stopped Goals disclosure ignores reduced motion: ${stoppedChevronTransition}`);
      await page.emulateMedia({ reducedMotion: "no-preference" });
      await page.screenshot({ path: resolve(outputDir, "goal-lifecycle-directory.png"), fullPage: false, animations: "disabled" });
      pass(1, "Goal stop applies directly without a redundant confirmation, while resume stays reviewed; both update optimistically, roll back rejected applies, and reconcile status in the background.");
      await page.locator(".personal-goal-link").first().click();
      await page.getByRole("button", { name: "概览", exact: true }).click();
      await page.getByRole("button", { name: "Goal 信息", exact: true }).click();
      const drawerHeaderVisual = await page.locator(".personal-drawer-header").evaluate((element) => {
        const close = element.querySelector(".personal-drawer-close")?.getBoundingClientRect();
        const header = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return {
          closeHeight: close?.height ?? 0,
          closeTopInset: close ? close.top - header.top : 0,
          closeWidth: close?.width ?? 0,
          headerHeight: header.height,
          paddingTop: Number.parseFloat(style.paddingTop),
        };
      });
      if (drawerHeaderVisual.closeHeight < 44 || drawerHeaderVisual.closeWidth < 44) {
        throw new Error(`Goal drawer close control is below the 44px target: ${JSON.stringify(drawerHeaderVisual)}`);
      }
      if (drawerHeaderVisual.headerHeight < 84 || drawerHeaderVisual.paddingTop < 18 || drawerHeaderVisual.closeTopInset < 18) {
        throw new Error(`Goal drawer header is still pinned too close to the top edge: ${JSON.stringify(drawerHeaderVisual)}`);
      }
      const goalDrawerOrder = await page.locator(".personal-drawer-body").evaluate((element) => {
        const lark = element.querySelector(".personal-goal-notification");
        const session = element.querySelector(".personal-goal-session");
        const actions = element.querySelector(".personal-drawer-action-grid");
        const subagents = element.querySelector(".personal-goal-subagents");
        const precedes = (earlier, later) => Boolean(earlier && later
          && (earlier.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING));
        return {
          actionsBeforeSubagents: precedes(actions, subagents),
          larkBeforeSession: precedes(lark, session),
          sessionBeforeSubagents: precedes(session, subagents),
          subagentsLast: subagents?.nextElementSibling === null,
        };
      });
      if (!goalDrawerOrder.larkBeforeSession
        || !goalDrawerOrder.sessionBeforeSubagents
        || !goalDrawerOrder.actionsBeforeSubagents
        || !goalDrawerOrder.subagentsLast) {
        throw new Error(`Goal drawer did not keep Lark and Session ahead of advanced sub-agent settings: ${JSON.stringify(goalDrawerOrder)}`);
      }
      const subagentSwitch = page.getByRole("switch", { name: "预览开启子代理执行" });
      await subagentSwitch.waitFor({ state: "visible" });
      if (await subagentSwitch.getAttribute("aria-checked") !== "false") throw new Error("Per-Goal sub-agent execution did not default off");
      if (!(await subagentSwitch.isEnabled())) throw new Error("Per-Goal sub-agent execution still required a task-domain selection");
      await page.getByLabel("最多子代理数").selectOption("2");
      const writesBeforeSubagentPreview = api.durableWriteCount;
      api.freezeGoalSubagentStatusProjection = true;
      await page.getByRole("button", { name: "使用 Luna / max", exact: true }).click();
      if (await page.getByRole("textbox", { name: "子 Agent 模型", exact: true }).inputValue() !== "gpt-5.6-luna") throw new Error("Luna preset did not fill the model");
      if (await page.getByRole("combobox", { name: "子 Agent 推理档位", exact: true }).inputValue() !== "max") throw new Error("Luna preset did not fill max effort");
      if (api.durableWriteCount !== writesBeforeSubagentPreview) throw new Error("Model preset performed a write");
      await page.getByRole("button", { name: "预览配置调整", exact: true }).click();
      await page.getByText("预览已锁定，确认后才会写入这个 Goal。", { exact: true }).waitFor({ state: "visible" });
      const offModelPreview = api.goalSubagentPreviews.at(-1);
      if (offModelPreview?.enabled !== false || offModelPreview?.model_config?.model !== "gpt-5.6-luna") throw new Error("Model-only preview must preserve disabled execution");
      if (api.durableWriteCount !== writesBeforeSubagentPreview) throw new Error("Model-only preview performed a write");
      await page.locator(".personal-subagent-preview").getByRole("button", { name: "取消", exact: true }).click();
      await page.getByRole("button", { name: "使用 Luna / max", exact: true }).click();
      await page.screenshot({ path: resolve(outputDir, "goal-subagent-model-desktop.png"), fullPage: false, animations: "disabled" });
      const modelViewport = page.viewportSize();
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByRole("textbox", { name: "子 Agent 模型", exact: true }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(outputDir, "goal-subagent-model-mobile.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize(modelViewport);
      await subagentSwitch.click();
      await page.getByText("预览已锁定，确认后才会写入这个 Goal。", { exact: true }).waitFor({ state: "visible" });
      if (api.durableWriteCount !== writesBeforeSubagentPreview) throw new Error("Unrestricted sub-agent preview mutated durable Goal state");
      if (api.goalSubagentPreviews.at(-1)?.allowed_domains.length !== 0) throw new Error("Unrestricted sub-agent preview invented a task-domain filter");
      await page.getByText("最多允许创建 2 个子代理；任务领域限制：不限制任务领域。", { exact: true }).waitFor({ state: "visible" });
      const previewPlacement = await page.locator(".personal-goal-subagents").evaluate((element) => {
        const preview = element.querySelector(".personal-subagent-preview");
        const currentSummary = element.querySelector("dl");
        const switchBounds = element.querySelector(".personal-subagent-switch")?.getBoundingClientRect();
        const previewBounds = preview?.getBoundingClientRect();
        return {
          gapFromSwitch: switchBounds && previewBounds ? previewBounds.top - switchBounds.bottom : Number.POSITIVE_INFINITY,
          previewBeforeCurrentSummary: Boolean(preview && currentSummary
            && (preview.compareDocumentPosition(currentSummary) & Node.DOCUMENT_POSITION_FOLLOWING)),
        };
      });
      if (!previewPlacement.previewBeforeCurrentSummary || previewPlacement.gapFromSwitch > 150) {
        throw new Error(`Sub-agent confirmation is detached from its switch: ${JSON.stringify(previewPlacement)}`);
      }
      await page.locator(".personal-subagent-preview").getByRole("button", { name: "确认", exact: true }).click();
      await page.getByText("已写入并完成读回校验；提升后的 Codex 子 Agent 上限将在新 Session 中生效。", { exact: true }).waitFor({ state: "visible" });
      const enabledSubagentSwitch = page.getByRole("switch", { name: "预览关闭子代理执行" });
      await enabledSubagentSwitch.waitFor({ state: "visible" });
      if (await enabledSubagentSwitch.getAttribute("aria-checked") !== "true") throw new Error("Verified apply receipt did not keep the per-Goal switch on while the status projection remained stale");
      if (api.durableWriteCount !== writesBeforeSubagentPreview + 1) throw new Error("Unrestricted sub-agent apply did not produce exactly one durable Goal write");
      api.freezeGoalSubagentStatusProjection = false;
      const echoedStatusResponse = page.waitForResponse((response) =>
        new URL(response.url()).pathname === "/status.json",
      );
      await page.getByRole("button", { name: "刷新状态", exact: true }).click();
      await echoedStatusResponse;
      const configuredGoalId = api.goalSubagentPreviews.at(-1)?.goal_id;
      if (!configuredGoalId) throw new Error("Sub-agent apply did not retain its Goal identity");
      api.goalSubagentConfigurations.set(configuredGoalId, {
        mode: "multi_subagent",
        spawn_allowed: true,
        max_children: 3,
        allowed_domains: ["validation"],
      });
      const supersedingStatusResponse = page.waitForResponse((response) =>
        new URL(response.url()).pathname === "/status.json",
      );
      await page.getByRole("button", { name: "刷新状态", exact: true }).click();
      await supersedingStatusResponse;
      const maxChildrenInput = page.getByLabel("最多子代理数");
      try {
        await waitForInputValue(maxChildrenInput, "3");
      } catch (error) {
        throw new Error(
          `${error instanceof Error ? error.message : String(error)}; preview=${JSON.stringify(api.goalSubagentPreviews.at(-1))}; authoritative=${JSON.stringify(api.goalSubagentConfigurations.get(configuredGoalId))}`,
        );
      }
      const supersededMaxChildren = await maxChildrenInput.inputValue();
      if (supersededMaxChildren !== "3") {
        throw new Error(
          `A later authoritative status did not supersede the verified apply receipt: max_children=${supersededMaxChildren}, status_requests=${api.statusRequestCount}, preview=${JSON.stringify(api.goalSubagentPreviews.at(-1))}, authoritative=${JSON.stringify(api.goalSubagentConfigurations.get(configuredGoalId))}`,
        );
      }
      if (!(await page.getByRole("checkbox", { name: /validation/u }).isChecked())) {
        throw new Error("The superseding authoritative status did not update allowed domains");
      }
      if (api.durableWriteCount !== writesBeforeSubagentPreview + 1) throw new Error("Status supersession produced a durable write");
      if (api.goalSubagentWrites[0]?.model_config?.model !== "gpt-5.6-luna" || api.goalSubagentWrites[0]?.model_config?.reasoning_effort !== "max") throw new Error("Native model preference did not survive UI request");

      await page.getByRole("button", { name: "清除模型偏好", exact: true }).click();
      const codeDomain = page.getByRole("checkbox", { name: /code/u });
      const validationDomain = page.getByRole("checkbox", { name: /validation/u });
      await codeDomain.waitFor({ state: "visible" });
      await validationDomain.waitFor({ state: "visible" });
      await codeDomain.check();
      await validationDomain.check();
      await page.getByRole("button", { name: "预览配置调整", exact: true }).click();
      await page.getByText("预览已锁定，确认后才会写入这个 Goal。", { exact: true }).waitFor({ state: "visible" });
      if (api.durableWriteCount !== writesBeforeSubagentPreview + 1) throw new Error("Restricted sub-agent preview mutated durable Goal state");
      if ([...(api.goalSubagentPreviews.at(-1)?.allowed_domains ?? [])].sort((a, b) => a.localeCompare(b)).join(",") !== "code,validation") throw new Error("Sub-agent preview lost the bounded task domains");
      await page.locator(".personal-subagent-preview").getByRole("button", { name: "确认", exact: true }).click();
      await page.getByText("已写入并完成读回校验；提升后的 Codex 子 Agent 上限将在新 Session 中生效。", { exact: true }).waitFor({ state: "visible" });
      if (api.durableWriteCount !== writesBeforeSubagentPreview + 2) throw new Error("Restricted sub-agent apply did not produce exactly one additional Goal write");
      if (api.goalSubagentWrites.at(-1)?.model_config !== null) throw new Error("Clearing the model was not sent explicitly");
      await page.screenshot({ path: resolve(outputDir, "goal-subagent-toggle.png"), fullPage: false, animations: "disabled" });

      await enabledSubagentSwitch.click();
      await page.locator(".personal-subagent-preview").getByRole("button", { name: "确认", exact: true }).click();
      const disabledSubagentSwitch = page.getByRole("switch", { name: "预览开启子代理执行" });
      await disabledSubagentSwitch.waitFor({ state: "visible" });
      if (await disabledSubagentSwitch.getAttribute("aria-checked") !== "false") throw new Error("Verified status readback did not turn the per-Goal switch off");
      if (api.durableWriteCount !== writesBeforeSubagentPreview + 3) throw new Error("Sub-agent disable did not produce exactly one additional durable Goal write");
      await page.getByRole("button", { name: /关闭详情/ }).click();
      const productReleaseGoal = page.locator(".personal-goal-link", { hasText: "Product Release" }).first();
      if (!await productReleaseGoal.isVisible()) {
        const stoppedGoals = page.locator(".personal-stopped-goals");
        if (await stoppedGoals.getAttribute("open") === null) await stoppedGoals.locator("summary").click();
      }
      await productReleaseGoal.waitFor({ state: "visible" });
      await productReleaseGoal.click();
      await page.getByRole("button", { name: "概览", exact: true }).click();
      await page.getByRole("button", { name: "Goal 信息", exact: true }).click();
      await page.getByText("当前没有开放的 advancement Todo 声明 task_domain", { exact: false }).waitFor({ state: "visible" });
      const emptyDomainSwitch = page.getByRole("switch", { name: "预览开启子代理执行" });
      if (!(await emptyDomainSwitch.isEnabled())) throw new Error("A Goal without projected task domains could not enable unrestricted sub-agent execution");
      api.failNextGoalSubagentResponse = true;
      await emptyDomainSwitch.click();
      await page.getByText("LoopX Chat 服务暂时不可用（HTTP 502）。请确认 Dashboard 与 Chat 服务已启动且来自同一版本。", { exact: true }).waitFor({ state: "visible" });
      if ((await page.locator(".personal-goal-subagents").innerText()).includes("Unexpected end of JSON input")) {
        throw new Error("Empty Chat API responses still expose a raw JSON parser failure");
      }
      if (api.durableWriteCount !== writesBeforeSubagentPreview + 3) throw new Error("A failed Goal sub-agent preview wrote Goal state");
      await emptyDomainSwitch.click();
      await page.getByText("预览已锁定，确认后才会写入这个 Goal。", { exact: true }).waitFor({ state: "visible" });
      if (api.goalSubagentPreviews.at(-1)?.allowed_domains.length !== 0) throw new Error("A Goal without task-domain candidates invented a restriction");
      await page.locator(".personal-subagent-preview").getByRole("button", { name: "取消", exact: true }).click();
      if (api.durableWriteCount !== writesBeforeSubagentPreview + 3) throw new Error("Canceling an unrestricted preview wrote Goal state");
      await page.getByRole("button", { name: /关闭详情/ }).click();
      api.freezeGoalSubagentStatusProjection = false;
      pass(22, "Per-Goal sub-agent execution supports unrestricted and restricted policies, previews before writing, verifies shared-state readback, leaves no-domain Goals usable, and can be disabled again.");

      if (await page.locator("html").getAttribute("lang") !== "zh-CN") throw new Error("Desktop did not start in Simplified Chinese");
      await page.locator('.personal-sidebar-utility[aria-label="设置"]').click();
      await page.getByRole("region", { name: "设置", exact: true }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: /语言/ }).click();
      const englishLocale = page.getByRole("radio", { name: /English/ });
      await englishLocale.click();
      await page.getByRole("heading", { level: 1, name: "Language", exact: true }).waitFor({ state: "visible" });
      if (await page.locator("html").getAttribute("lang") !== "en") throw new Error("Language switch did not update the document locale");
      if (await page.evaluate(() => localStorage.getItem("loopx-pw-locale")) !== "en") throw new Error("English locale was not persisted");
      await page.screenshot({ path: resolve(outputDir, "desktop-settings-english.png"), fullPage: false, animations: "disabled" });
      await checkpointCoverage();
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      await page.getByText("LoopX Manager", { exact: true }).first().waitFor({ state: "visible" });
      if (await page.locator("html").getAttribute("lang") !== "en") throw new Error("English locale did not survive reload");
      await page.locator(".personal-goal-link", { hasText: /loopx meta/i }).click();
      await page.getByRole("button", { name: "Overview", exact: true }).click();
      await page.getByRole("button", { name: "Goal information", exact: true }).click();
      await page.getByText("Repository", { exact: true }).waitFor({ state: "visible" });
      await page.getByText("Execution Session", { exact: true }).waitFor({ state: "visible" });
      await page.locator(".personal-goal-repository").getByText("Read only", { exact: true }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: /Close details/ }).click();
      const englishGoalNavigation = page.getByRole("navigation", { name: "Goal view" });
      await englishGoalNavigation.getByRole("button", { name: /^(Chat|对话)$/, exact: true }).click();
      await page.locator('[data-goal-panel="chat"]').getByText("Agent is waiting for your decision", { exact: true }).first().waitFor({ state: "visible" });
      await englishGoalNavigation.getByRole("button", { name: /^(Files|成果)$/, exact: true }).click();
      if (await page.locator('[data-output-kind="evidence"]').count() !== 0) throw new Error("Run status appeared as a delivered file");
      await page.getByRole("region", { name: "Accepted team reports" }).getByText("No verifiable team reports yet.", { exact: true }).waitFor({ state: "visible" });
      const englishProjectionText = await page.locator(".personal-workspace-main").innerText();
      for (const forbidden of ["最近运行", "最近验证", "Agent 正在整理下一步", "Agent 正在推进当前 Goal", "Agent 等待你的决定"]) {
        if (englishProjectionText.includes(forbidden)) throw new Error(`English projection exposed Chinese UI copy ${forbidden}: ${englishProjectionText}`);
      }
      await page.screenshot({ path: resolve(outputDir, "desktop-english-projection-copy.png"), fullPage: false, animations: "disabled" });

      const writesBeforeEnglishPreviews = api.durableWriteCount;
      await page.locator(".personal-manager-link").first().click();
      if (await page.locator(".personal-composer-tools").getAttribute("open") === null) await page.locator(".personal-composer-tools > summary").click();
      await page.getByRole("button", { name: "Create Goal", exact: true }).last().click();
      const englishGoalForm = page.getByRole("dialog", { name: "Create Goal", exact: true });
      await englishGoalForm.getByLabel("Execution permission", {exact: true}).selectOption("read_only");
      await englishGoalForm.getByLabel("Objective", { exact: true }).fill("Prepare my weekly work review");
      await englishGoalForm.getByLabel("Completion criteria", { exact: true }).fill("List completed work, blockers, and next-week plans");
      await englishGoalForm.getByLabel("Execution boundary (optional)", { exact: true }).fill("Read only; do not call external tools or modify repositories");
      await englishGoalForm.getByRole("button", { name: "Review configuration" }).click();
      await page.getByText("Confirm execution", { exact: true }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: "Create Goal and start first run", exact: true }).waitFor({ state: "visible" });
      const englishGoalPreview = api.actionPreviews.at(-1);
      if (englishGoalPreview?.action_kind !== "goal.create") throw new Error(`English Goal input did not create a Goal preview: ${JSON.stringify(englishGoalPreview)}`);
      if (englishGoalPreview.normalized_parameters.title !== "Prepare my weekly work review") throw new Error(`English Goal title drifted: ${JSON.stringify(englishGoalPreview.normalized_parameters)}`);
      if (englishGoalPreview.normalized_parameters.completion_criteria !== "List completed work, blockers, and next-week plans") throw new Error(`English completion criteria were not preserved: ${JSON.stringify(englishGoalPreview.normalized_parameters)}`);
      if (englishGoalPreview.normalized_parameters.execution_boundary !== "Read only; do not call external tools or modify repositories") throw new Error(`English execution boundary was not preserved: ${JSON.stringify(englishGoalPreview.normalized_parameters)}`);
      if (englishGoalPreview.normalized_parameters.permission !== "read_only") throw new Error(`English execution boundary did not remain read-only: ${JSON.stringify(englishGoalPreview.normalized_parameters)}`);
      await page.getByRole("button", { name: "Close", exact: true }).click();

      await page.locator(".personal-goal-link").first().click();
      const writesBeforeEnglishMonitorShortcut = api.durableWriteCount;
      if (await page.locator(".personal-composer-tools").getAttribute("open") === null) await page.locator(".personal-composer-tools > summary").click();
      await page.getByRole("button", { name: "Configure scheduled check" }).click();
      await page.getByRole("dialog", {name: "Configure scheduled check", exact: true}).getByRole("button", {name: "Review configuration"}).click();
      await page.getByText("Confirm execution", { exact: true }).waitFor({ state: "visible" });
      const englishMonitorShortcut = api.actionPreviews.findLast((preview) => preview.action_kind === "monitor.create");
      if (englishMonitorShortcut?.normalized_parameters.cadence !== "2h") throw new Error(`English scheduled-check shortcut cadence drifted: ${JSON.stringify(englishMonitorShortcut?.normalized_parameters)}`);
      if (englishMonitorShortcut?.normalized_parameters.stop_condition !== "goal_complete") throw new Error(`English scheduled-check shortcut stop condition drifted: ${JSON.stringify(englishMonitorShortcut?.normalized_parameters)}`);
      if (englishMonitorShortcut?.normalized_parameters.target !== "Check the current Goal for blockers, progress, and new outputs") throw new Error(`English scheduled-check shortcut did not fall back to the localized default check target: ${JSON.stringify(englishMonitorShortcut?.normalized_parameters)}`);
      if (api.durableWriteCount !== writesBeforeEnglishMonitorShortcut) throw new Error("English scheduled-check shortcut wrote durable state before confirmation");
      await page.getByRole("button", { name: "Close", exact: true }).click();
      await page.getByRole("button", { name: "Configure scheduled check" }).click();
      const englishScheduleForm = page.getByRole("dialog", { name: "Configure scheduled check", exact: true });
      const previewsBeforeEnglishCalendarSchedule = api.actionPreviews.length;
      await englishScheduleForm.getByLabel("Interval", {exact: true}).fill("0");
      await englishScheduleForm.getByRole("button", {name: "Review configuration"}).click();
      if (api.actionPreviews.length !== previewsBeforeEnglishCalendarSchedule) throw new Error("Invalid interval created a preview");
      await englishScheduleForm.getByLabel("Interval", {exact: true}).fill("2");
      await englishScheduleForm.getByLabel("Check target", {exact: true}).fill("Verify the review includes completed work, blockers, and next-week plans");
      await englishScheduleForm.getByRole("button", {name: "Review configuration"}).click();
      await page.getByText("Confirm execution", { exact: true }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: "Confirm and apply", exact: true }).waitFor({ state: "visible" });
      const englishMonitorPreview = api.actionPreviews.at(-1);
      if (englishMonitorPreview?.action_kind !== "monitor.create") throw new Error(`English monitor input did not create a monitor preview: ${JSON.stringify(englishMonitorPreview)}`);
      if (englishMonitorPreview.normalized_parameters.cadence !== "2h") throw new Error(`English monitor cadence drifted: ${JSON.stringify(englishMonitorPreview.normalized_parameters)}`);
      if (englishMonitorPreview.normalized_parameters.target !== "Verify the review includes completed work, blockers, and next-week plans") throw new Error(`English monitor target drifted: ${JSON.stringify(englishMonitorPreview.normalized_parameters)}`);
      if (englishMonitorPreview.normalized_parameters.stop_condition !== "goal_complete") throw new Error(`English monitor stop condition drifted: ${JSON.stringify(englishMonitorPreview.normalized_parameters)}`);
      if (api.durableWriteCount !== writesBeforeEnglishPreviews) throw new Error("English write previews mutated durable state before confirmation");
      await page.getByRole("button", { name: "Close", exact: true }).click();

      await page.getByRole("button", { name: "Overview", exact: true }).click();
      await page.getByRole("button", { name: "Goal information", exact: true }).click();
      await page.getByRole("button", { name: "Set up Heartbeat", exact: true }).click();
      await page.getByRole("dialog", { name: "Goal Heartbeat", exact: true }).getByRole("button", { name: "Review configuration" }).click();
      await page.getByText("Confirm execution", { exact: true }).waitFor({ state: "visible" });
      const englishHeartbeatPreview = api.actionPreviews.at(-1);
      if (englishHeartbeatPreview?.action_kind !== "heartbeat.bind") throw new Error("English Heartbeat input did not create a heartbeat preview: " + JSON.stringify(englishHeartbeatPreview));
      if (englishHeartbeatPreview.normalized_parameters.cadence !== "1d") throw new Error("English Heartbeat cadence drifted: " + JSON.stringify(englishHeartbeatPreview.normalized_parameters));
      if (englishHeartbeatPreview.normalized_parameters.stop_condition !== "goal_complete") throw new Error("English Heartbeat stop condition drifted: " + JSON.stringify(englishHeartbeatPreview.normalized_parameters));
      const writesBeforeEnglishHeartbeatApply = api.durableWriteCount;
      api.allowNextHeartbeatApply = true;
      await page.getByRole("button", { name: "Confirm and apply", exact: true }).click();
      await page.getByText("Applied. LoopX state will refresh.", { exact: true }).waitFor({ state: "visible" });
      if (api.durableWriteCount !== writesBeforeEnglishHeartbeatApply + 1) throw new Error("English Heartbeat apply did not produce exactly one durable write");
      await page.getByRole("button", { name: "View updated Goal", exact: true }).click();
      await page.getByRole("navigation", { name: "Goal view" }).getByRole("button", { name: /^(Chat|对话)$/, exact: true }).click();
      const englishHeartbeatSchedule = page.locator(".personal-schedule-row", { hasText: "Goal Heartbeat" }).first();
      await page.locator(".personal-activity-summary > summary", { hasText: "Background work" }).waitFor({ state: "visible" });
      if (await englishHeartbeatSchedule.isVisible()) throw new Error("Schedules must fold under background work instead of crowding the conversation");
      await page.locator(".personal-activity-summary > summary").click();
      await englishHeartbeatSchedule.waitFor({ state: "visible" });
      const englishHeartbeatScheduleText = await englishHeartbeatSchedule.innerText();
      if (!englishHeartbeatScheduleText.includes("1d")) throw new Error("Applied English Heartbeat lost cadence: " + englishHeartbeatScheduleText);
      await englishHeartbeatSchedule.click();
      const englishHeartbeatDrawer = page.locator('.personal-context-drawer[data-context-kind="schedule"]');
      await englishHeartbeatDrawer.getByText("goal_complete", { exact: true }).waitFor({ state: "visible" });
      await englishHeartbeatDrawer.getByText("Asia/Shanghai", { exact: true }).waitFor({ state: "visible" });
      const englishHeartbeatReadback = await englishHeartbeatDrawer.innerText();
      for (const forbidden of ["等待下次宿主唤醒", "仅在需要你时通知", "由 heartbeat-prompt 生命周期驱动", "Goal 完成或 owner 停止"]) {
        if (englishHeartbeatReadback.includes(forbidden)) throw new Error("Applied English Heartbeat exposed Chinese fallback " + forbidden + ": " + englishHeartbeatReadback);
      }
      await page.getByRole("button", { name: /Close details/ }).click();
      pass(20, "English Goal and monitor previews stay read-only until confirmation, and applied Heartbeat readback preserves typed schedule semantics.");

      await page.locator('.personal-sidebar-utility[aria-label="Settings"]').click();
      await page.getByRole("button", { name: /Language/ }).click();
      await page.getByRole("radio", { name: /Simplified Chinese/ }).click();
      await page.getByRole("heading", { level: 1, name: "语言", exact: true }).waitFor({ state: "visible" });
      if (await page.evaluate(() => localStorage.getItem("loopx-pw-locale")) !== "zh-CN") throw new Error("Simplified Chinese locale was not persisted");
      await page.getByRole("button", { name: "返回工作区", exact: true }).click();
      await page.locator(".personal-manager-link").first().click();
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });

      const writesBeforeGoalCreate = api.durableWriteCount;
      if (await page.locator(".personal-composer-tools").getAttribute("open") === null) await page.locator(".personal-composer-tools > summary").click();
      await page.getByRole("button", { name: "创建新 Goal", exact: true }).last().click();
      const goalForm = page.getByRole("dialog", { name: "创建新 Goal", exact: true });
      await goalForm.getByLabel("执行权限", {exact: true}).selectOption("read_only");
      await goalForm.getByLabel("目标", {exact: true}).fill("整理我的每周工作复盘");
      await goalForm.getByLabel("完成标准", {exact: true}).fill("列出已完成、阻塞、下周计划");
      await goalForm.getByLabel("执行边界（可选）", {exact: true}).fill("不调用外部工具，不修改仓库");
      await goalForm.getByRole("button", { name: "检查配置" }).click();
      await page.getByText("确认执行").waitFor({ state: "visible" });
      const goalPreview = api.actionPreviews.at(-1);
      for (const field of ["agent_id", "goal_id", "heartbeat", "initial_todos", "permission", "stop_condition", "workspace_ref"]) {
        if (!(field in (goalPreview?.normalized_parameters ?? {}))) throw new Error(`Goal preview missing ${field}`);
      }
      if (goalPreview?.normalized_parameters.title !== "整理我的每周工作复盘") throw new Error(`Structured Goal title drifted: ${JSON.stringify(goalPreview?.normalized_parameters)}`);
      if (goalPreview?.normalized_parameters.goal_id === "loopx" || !String(goalPreview?.normalized_parameters.goal_id).startsWith("goal-")) throw new Error(`Structured Goal id was derived from template chrome: ${JSON.stringify(goalPreview?.normalized_parameters)}`);
      if (!String(goalPreview?.normalized_parameters.objective).includes("列出已完成、阻塞、下周计划")) throw new Error(`Goal completion standard was lost: ${JSON.stringify(goalPreview?.normalized_parameters)}`);
      if (goalPreview?.normalized_parameters.completion_criteria !== "列出已完成、阻塞、下周计划") throw new Error(`Goal completion criteria were not preserved structurally: ${JSON.stringify(goalPreview?.normalized_parameters)}`);
      if (goalPreview?.normalized_parameters.execution_boundary !== "不调用外部工具，不修改仓库") throw new Error(`Goal execution boundary was not preserved structurally: ${JSON.stringify(goalPreview?.normalized_parameters)}`);
      if (goalPreview?.normalized_parameters.permission !== "read_only") throw new Error(`Goal execution boundary did not remain read-only: ${JSON.stringify(goalPreview?.normalized_parameters)}`);
      if (JSON.stringify(goalPreview?.normalized_parameters.initial_todos).includes("推进首个可验证结果")) throw new Error(`Goal preview kept unrelated generic Todos: ${JSON.stringify(goalPreview?.normalized_parameters)}`);
      if (api.durableWriteCount !== writesBeforeGoalCreate) throw new Error("Goal preview wrote durable state before confirmation");
      pass(7, "Goal preview includes Goal, Agent, workspace, permissions, Todos, heartbeat, and stop condition fields.");
      await page.getByRole("button", { name: "创建 Goal 并开始首轮", exact: true }).click();
      try {
        await page.getByText(/已应用/).first().waitFor({ state: "visible" });
      } catch (error) {
        await page.screenshot({ path: resolve(outputDir, "goal-apply-failed.png"), fullPage: true, animations: "disabled" });
        throw new Error(`${error.message}; applies=${JSON.stringify(api.actionApplies)}; errors=${pageErrors.join(" | ")}; body=${(await page.locator("body").innerText()).slice(0, 3000)}`);
      }
      if (api.durableWriteCount !== writesBeforeGoalCreate + 1) throw new Error("Goal apply did not create exactly one durable resource");
      await page.evaluate(async (proposalId) => {
        await fetch(`/api/actions/${proposalId}/apply`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
      }, goalPreview.proposalId);
      if (api.durableWriteCount !== writesBeforeGoalCreate + 1) throw new Error("Repeated proposal apply duplicated durable state");
      pass(9, "A repeated apply request kept one durable resource and one first-turn resource key.");
      await page.getByRole("button", { name: /关闭详情/ }).click();
      const actionReceiptClose = page.getByRole("button", { name: "关闭操作回执", exact: true });
      if (await actionReceiptClose.isVisible().catch(() => false)) await actionReceiptClose.click();

      const goalButton = page.locator(".personal-goal-link").first();
      await goalButton.click();
      const goalNavigation = page.getByRole("navigation", { name: "Goal 视图" });
      const defaultTasksTab = goalNavigation.getByRole("button", { name: /^(Tasks|任务)$/ });
      if (await defaultTasksTab.getAttribute("aria-current") !== "page") throw new Error("Selecting a Goal did not prioritize its Tasks view");
      await page.screenshot({ path: resolve(outputDir, "goal-tasks-loopx-theme.png"), fullPage: false, animations: "disabled" });
      await goalNavigation.getByRole("button", { name: /^(Files|成果)$/ }).click();
      const publicFiles = page.locator(".personal-files-list > button");
      if (await publicFiles.count() !== 0) throw new Error("A status or run observation appeared as a delivered file");
      await page.getByRole("region", { name: "已验收的团队报告" }).getByText("暂无可核验的团队报告。", { exact: true }).waitFor({ state: "visible" });
      await page.screenshot({ path: resolve(outputDir, "goal-files-loopx-theme.png"), fullPage: false, animations: "disabled" });
      await goalNavigation.getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await page.locator(".personal-channel-timeline").waitFor({ state: "visible" });
      await page.screenshot({ path: resolve(outputDir, "goal-chat-loopx-theme.png"), fullPage: false, animations: "disabled" });
      await defaultTasksTab.click();
      const desktopNavigationTrigger = page.locator(".personal-mobile-menu");
      if (await desktopNavigationTrigger.isVisible()) {
        throw new Error("Mobile-only Goal navigation trigger leaked into the persistent desktop sidebar layout");
      }
      const fullDesktopViewport = page.viewportSize();
      await page.setViewportSize({ width: 900, height: 720 });
      const compactHeaderButtons = [
        page.locator(".personal-mobile-menu"),
        page.locator(".personal-refresh-control .personal-icon-button"),
      ];
      for (const button of compactHeaderButtons) {
        const box = await button.boundingBox();
        if (!box || Math.abs(box.width - 36) > 0.5 || Math.abs(box.height - 36) > 0.5) {
          throw new Error(`Compact header icon button was compressed: ${JSON.stringify(box)}`);
        }
      }
      const compactGoalSettingsBox = await page.locator(".personal-goal-settings-action").boundingBox();
      if (!compactGoalSettingsBox || compactGoalSettingsBox.width < 40 || compactGoalSettingsBox.height < 36) {
        throw new Error(`Compact Goal settings trigger was compressed: ${JSON.stringify(compactGoalSettingsBox)}`);
      }
      const compactHeaderLayout = await page.evaluate(() => {
        const title = document.querySelector('.personal-channel-header[data-goal-selected="true"] .personal-channel-title h1');
        return {
          documentWidth: document.documentElement.scrollWidth,
          titleHeight: title?.getBoundingClientRect().height ?? 0,
          titleWidth: title?.getBoundingClientRect().width ?? 0,
          viewportWidth: window.innerWidth,
        };
      });
      if (compactHeaderLayout.titleWidth <= 0 || compactHeaderLayout.titleHeight > 36) {
        throw new Error(`Compact Goal title did not remain on one line: ${JSON.stringify(compactHeaderLayout)}`);
      }
      if (compactHeaderLayout.documentWidth > compactHeaderLayout.viewportWidth + 1) {
        throw new Error(`Compact header caused horizontal overflow: ${JSON.stringify(compactHeaderLayout)}`);
      }
      await page.screenshot({ path: resolve(outputDir, "goal-header-compact-width.png"), fullPage: false, animations: "disabled" });
      if (fullDesktopViewport) await page.setViewportSize(fullDesktopViewport);
      await page.locator(".personal-goal-link", { hasText: "Progress Projection" }).click();
      await page.getByRole("heading", { name: "Progress Projection" }).waitFor({ state: "visible" });
      await page.locator(".personal-task-card").getByText("Current Todo", {exact: true}).waitFor();
      if ((await page.locator(".personal-channel-title").innerText()).includes("Current Todo")) throw new Error("Header repeated the task already shown in the workspace");
      const progressColumn = page.locator(".personal-object-list", { hasText: "待执行 / 进行中" });
      if ((await progressColumn.locator(".personal-task-card").count()) !== 5) throw new Error("Id-less long Todo was duplicated across compact and full projections");
      await progressColumn.getByText("Full queue follow-up", { exact: true }).waitFor();
      await progressColumn.getByText("Deferred queue task", { exact: true }).waitFor();
      await progressColumn.getByText("Deferred follow-up outside preview", { exact: true }).waitFor();
      async function assertDeferredTask(conditionExpected = true) {
        const title = conditionExpected ? "Deferred queue task" : "Deferred follow-up outside preview";
        const card = page.locator(".personal-task-card", { hasText: title });
        await card.getByText("已延期", { exact: true }).waitFor();
        if (await card.getByText("待执行", { exact: true }).count()) throw new Error("Deferred task was labeled queued");
        await card.getByText(title, { exact: true }).click();
        const drawer = page.getByRole("dialog", { name: "Todo 详情" });
        await drawer.getByText(
          conditionExpected ? "恢复条件已满足，等待生命周期重新规划" : "等待恢复条件满足后重新评估",
          { exact: true },
        ).waitFor();
        const condition = drawer.locator("dl > div", { has: page.getByText("恢复条件", { exact: true }) });
        await condition.getByText(conditionExpected ? "resume_at:2026-09-14T01:30:00Z" : "未设置", { exact: true }).waitFor();
        if (conditionExpected) {
          await drawer.getByText("可恢复", { exact: true }).waitFor();
          await drawer.getByText("resume_at_browser_smoke_receipt", { exact: true }).waitFor();
        }
        if (await drawer.getByText("待执行", { exact: true }).count()) throw new Error("Deferred drawer was labeled ready");
        await page.screenshot({ path: resolve(outputDir, `deferred-task-${conditionExpected ? "condition" : "missing"}.png`), fullPage: false, animations: "disabled" });
        await drawer.getByRole("button", { name: /关闭详情/ }).click();
      }
      await assertDeferredTask();
      await assertDeferredTask(false);
      const completedColumn = page.locator(".personal-object-list", { hasText: "已完成" }).last();
      const taskLaneScrollers = page.locator('.personal-task-kanban .personal-task-lane-scroll');
      if (await taskLaneScrollers.count() !== 4) throw new Error('Every desktop Task lane must own a scroll region');
      for (let laneIndex = 0; laneIndex < 4; laneIndex += 1) {
        if (await taskLaneScrollers.nth(laneIndex).evaluate(element => getComputedStyle(element).overflowY) !== 'auto') {
          throw new Error(`Task lane ${laneIndex + 1} does not support independent scrolling`);
        }
      }
      await completedColumn.getByText("4087", { exact: true }).waitFor({ state: "visible" });
      await completedColumn.getByText("Completed A", { exact: true }).waitFor({ state: "visible" });
      if (await completedColumn.getByText("Completed Monitor", { exact: true }).count()) throw new Error("Completed continuous monitor leaked into the completed Tasks column");
      const historyScroll = completedColumn.locator('.personal-task-lane-scroll');
      for (let batch = 1; batch < 103; batch += 1) {
        const response = page.waitForResponse(response => response.url().includes('/api/chat/completed-todos?') && response.url().includes(`cursor=${batch * 40}`));
        await historyScroll.evaluate(element => { element.scrollTop = element.scrollHeight; });
        await response;
        await page.waitForFunction(minimum => {
          const window = document.querySelector('[data-testid="completed-task-lane"] .personal-completed-window');
          return window && Number.parseFloat(window.style.height) >= minimum;
        }, Math.min(4087, (batch + 1) * 40) * 148);
        if (await completedColumn.locator('.personal-completed-row').count() > 20) throw new Error('Completed history DOM grew with accumulated pages');
      }
      await historyScroll.evaluate(element => { element.scrollTop = element.scrollHeight; });
      await completedColumn.getByText('Completed historical Task 4087', { exact: true }).waitFor();
      await completedColumn.getByText('已显示全部完成记录', { exact: true }).waitFor();
      await page.screenshot({ path: resolve(outputDir, 'completed-history-4087.png'), fullPage: false, animations: 'disabled' });
      await historyScroll.evaluate(element => { element.scrollTop = 0; });
      await completedColumn.getByText('Completed A', { exact: true }).waitFor();
      // Both presentations retain one snapshot, including archived history and evidence.
      let historyRequests = 0;
      page.on('request', request => { if (request.url().includes('/api/chat/completed-todos?')) historyRequests += 1; });
      await page.getByRole('button', { name: '列表', exact: true }).click();
      await assertDeferredTask();
      const listHistory = page.getByTestId('completed-task-lane');
      await listHistory.getByRole('button', { name: '已完成', exact: false }).click();
      await listHistory.getByText('4087', { exact: true }).waitFor();
      await listHistory.getByText('Completed A', { exact: true }).waitFor();
      await listHistory.locator('.personal-task-lane-scroll').evaluate(element => { element.scrollTop = element.scrollHeight; });
      await listHistory.getByText('Completed historical Task 4087', { exact: true }).waitFor();
      if (await listHistory.locator('.personal-completed-row').count() > 20) throw new Error('List history DOM grew with accumulated pages');
      await page.screenshot({ path: resolve(outputDir, 'completed-history-list.png'), fullPage: false, animations: 'disabled' });
      await page.getByRole('button', { name: '看板', exact: true }).click();
      await completedColumn.getByText('Completed A', { exact: true }).waitFor();
      if (historyRequests) throw new Error('Switching presentation replaced the completed-history snapshot');
      await page.locator(".personal-goal-link", { hasText: "Multi Agent Projection" }).click();
      const multiAgentHeader = await page.locator(".personal-channel-title").innerText();
      if (multiAgentHeader.includes("codex-older-lane")) {
        throw new Error(`Multi-Agent Goal header still implies arbitrary single-lane ownership: ${multiAgentHeader}`);
      }
      if ((await page.locator(".personal-object-list", { hasText: "待执行 / 进行中" }).locator(".personal-task-card").count()) !== 2) {
        throw new Error("All-Agent default did not preserve both projected work lanes");
      }
      const laneFilter = page.getByRole("combobox", { name: "按工作 Agent 筛选" });
      if (await laneFilter.inputValue() !== "all") throw new Error("Multi-Agent Tasks view did not default to all work lanes");
      await page.screenshot({ path: resolve(outputDir, "multi-agent-task-lanes.png"), fullPage: false, animations: "disabled" });
      await laneFilter.selectOption("codex-latest-lane");
      const filteredText = await page.locator(".personal-object-list", { hasText: "待执行 / 进行中" }).innerText();
      if (!filteredText.includes("Latest lane work") || filteredText.includes("Older lane work")) {
        throw new Error(`Work-Agent filter did not consistently filter task cards: ${filteredText}`);
      }
      const runtimeSelector = page.getByRole("combobox", { name: "选择聊天 Runtime" });
      if (!await runtimeSelector.count()) throw new Error("Chat runtime selector is not explicitly labelled independently from work-Agent lanes");
      await goalButton.click();
      const readBoardGeometry = async () => {
        const kanban = page.locator(".personal-task-kanban");
        await kanban.waitFor({ state: "visible" });
        const kanbanBox = await kanban.boundingBox();
        const columns = await page.locator(".personal-task-kanban > .personal-object-list").evaluateAll((els) =>
          els.map((el) => { const rect = el.getBoundingClientRect(); return { left: rect.left, right: rect.right, width: rect.width }; })
        );
        return { kanbanBox, columns };
      };
      const assertBoardGeometry = (label, geometry) => {
        if (!geometry.kanbanBox || geometry.columns.length !== 4) {
          throw new Error(`${label}: expected 4 kanban columns, got ${geometry.columns?.length}`);
        }
        const kanbanRight = geometry.kanbanBox.x + geometry.kanbanBox.width;
        if (Math.abs(geometry.columns[3].right - kanbanRight) > 2) {
          throw new Error(`${label}: kanban columns do not fill the board (lastRight=${geometry.columns[3].right}, boardRight=${kanbanRight})`);
        }
        if (new Set(geometry.columns.map((column) => Math.round(column.width))).size !== 1) {
          throw new Error(`${label}: kanban columns are not equal width: ${JSON.stringify(geometry.columns)}`);
        }
      };
      const selectFirstGoal = async () => {
        await page.locator(".personal-goal-link").first().click();
        await page.locator(".personal-task-kanban").waitFor({ state: "visible" });
      };
      const selectProductReleaseGoal = async () => {
        const goal = page.locator(".personal-goal-link", { hasText: "Product Release" }).first();
        if (!await goal.isVisible()) {
          const stoppedGoals = page.locator(".personal-stopped-goals");
          if (await stoppedGoals.getAttribute("open") === null) await stoppedGoals.locator("summary").click();
        }
        await goal.click();
        await page.locator(".personal-task-kanban").waitFor({ state: "visible" });
      };
      const populatedGeometry = await readBoardGeometry();
      assertBoardGeometry("populated board", populatedGeometry);
      await selectProductReleaseGoal();
      const emptyGeometry = await readBoardGeometry();
      assertBoardGeometry("empty board", emptyGeometry);
      if (Math.abs(emptyGeometry.kanbanBox.width - populatedGeometry.kanbanBox.width) > 2) {
        throw new Error(`Empty board width ${emptyGeometry.kanbanBox.width} differs from populated ${populatedGeometry.kanbanBox.width}`);
      }
      const desktopViewport = page.viewportSize();
      await page.setViewportSize({ width: 2048, height: 1200 });
      await page.waitForTimeout(200);
      await selectFirstGoal();
      const populatedWide = await readBoardGeometry();
      assertBoardGeometry("populated board (wide)", populatedWide);
      await selectProductReleaseGoal();
      const emptyWide = await readBoardGeometry();
      assertBoardGeometry("empty board (wide)", emptyWide);
      if (Math.abs(emptyWide.kanbanBox.width - populatedWide.kanbanBox.width) > 2) {
        throw new Error(`Empty board width (wide) ${emptyWide.kanbanBox.width} differs from populated ${populatedWide.kanbanBox.width}`);
      }
      await page.setViewportSize(desktopViewport);
      await page.waitForTimeout(200);
      await selectFirstGoal();
      await page.locator(".personal-object-list").first().waitFor({ state: "visible" });
      if (await page.locator(".personal-task-capability-callout").count()) throw new Error("Goal capability settings still consume a full-width Tasks row");
      await page.getByRole("button", { name: "Goal 设置", exact: true }).click();
      await page.getByRole("heading", { level: 1, name: "Goal 能力", exact: true }).waitFor({ state: "visible" });
      if (await page.locator(".personal-workspace-shell:visible").count()) throw new Error("Unified Goal capability action did not open the Settings surface");
      await page.getByRole("heading", { level: 2, name: /^周期报告/ }).waitFor({ state: "visible" });
      const goalCapabilityOrder = await page.locator(".personal-capability-list button strong").allTextContents();
      if (goalCapabilityOrder.includes("管家执行器") || goalCapabilityOrder.includes("管家 Runtime")) {
        throw new Error(`Machine-only capabilities leaked into Goal settings: ${JSON.stringify(goalCapabilityOrder)}`);
      }
      if (await page.locator(".personal-capability-editor-status").count()) throw new Error("Editable Goal settings must not show internal editor-contract notices");
      const expectedGoalCapabilities = [
        "变更质量验证", "Goal 复核周期", "探索图谱", "探索 Harness", "飞书事件收件箱",
        "飞书看板心跳同步", "已退役的 Authority 观测", "自适应子 Agent 容量",
        "已注册 Peer 任务协调", "周期报告", "Reward Memory 实验",
      ];
      if (JSON.stringify([...goalCapabilityOrder].sort()) !== JSON.stringify(expectedGoalCapabilities.sort())) {
        throw new Error(`Goal capability workbench did not render the complete catalog: ${JSON.stringify(goalCapabilityOrder)}`);
      }
      const selectedCapabilityName = await page.locator('.personal-capability-list button[aria-current="page"] strong').innerText();
      if (selectedCapabilityName !== goalCapabilityOrder[0]) throw new Error("Default capability selection must match the first visible catalog entry");
      const capabilityIndex = (name) => goalCapabilityOrder.indexOf(name);
      if (capabilityIndex("周期报告") >= capabilityIndex("探索 Harness")
        || capabilityIndex("自适应子 Agent 容量") <= capabilityIndex("探索 Harness")
        || capabilityIndex("自适应子 Agent 容量") >= capabilityIndex("已退役的 Authority 观测")
        || capabilityIndex("自适应子 Agent 容量") >= capabilityIndex("Reward Memory 实验")) {
        throw new Error(`Goal capability maturity ordering drifted: ${JSON.stringify(goalCapabilityOrder)}`);
      }
      await page.locator(".personal-capability-list").getByRole("button", { name: "已退役的 Authority 观测" }).click();
      const retiredDetail = page.locator(".personal-capability-detail");
      await retiredDetail.getByText(/--clear-local-authority-shadow/).waitFor();
      if (await retiredDetail.getByRole("checkbox").count()
          || await retiredDetail.getByRole("button", { name: "预览变更", exact: true }).count()) {
        throw new Error("Retired observation must not expose activation controls");
      }
      await page.screenshot({ path: resolve(outputDir, "retired-observation-settings.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      await retiredDetail.getByText(/--clear-local-authority-shadow/).waitFor();
      await page.screenshot({ path: resolve(outputDir, "retired-observation-settings-mobile.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize(desktopViewport);
      await page.locator(".personal-capability-list").getByRole("button", { name: "周期报告" }).click();
      for (const label of [/^启用$/u, /^报告 Profile/u, /^Goal Channel 路由/u, /^时区/u]) {
        await page.getByLabel(label).waitFor({ state: "visible" });
      }
      await page.screenshot({ path: resolve(outputDir, "goal-capability-zh-cn.png"), fullPage: false, animations: "disabled" });
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await page.getByText("锁定 revision 的变更预览", { exact: true }).waitFor({ state: "visible" });
      const goalConfigurationPreview = api.goalConfigurationRequests.find((item) => item.phase === "preview");
      const projectedKeys = Object.keys(goalConfigurationPreview?.configuration ?? {}).sort((left, right) => left.localeCompare(right));
      if (JSON.stringify(projectedKeys) !== JSON.stringify(["enabled", "profile_preset", "route_ref", "timezone"])) {
        throw new Error(`Goal configuration preview leaked hidden machine fields: ${JSON.stringify(goalConfigurationPreview)}`);
      }
      await page.getByRole("button", { name: "编辑 JSON", exact: true }).click();
      const goalJson = page.locator("#goal-configuration-json");
      const originalGoalJson = await goalJson.inputValue();
      const goalPreviewButton = page.getByRole("button", { name: "预览变更", exact: true });
      const goalApplyButton = page.getByRole("button", { name: "应用此预览", exact: true });
      if (!(await goalApplyButton.isDisabled())) throw new Error("Switching editors retained a stale Goal preview");
      for (const invalid of ["{", '{"schema_version":"hidden"}']) {
        await goalJson.fill(invalid);
        if (!(await goalPreviewButton.isDisabled()) || !(await goalApplyButton.isDisabled())) {
          throw new Error("Invalid or unregistered Goal JSON enabled configuration mutation");
        }
      }
      await goalJson.fill(JSON.stringify({ ...JSON.parse(originalGoalJson), timezone: "Asia/Shanghai" }));
      await page.getByRole("button", { name: "返回表单", exact: true }).click();
      await waitForInputValue(page.getByLabel(/^时区/u), "Asia/Shanghai");
      await goalPreviewButton.click();
      await page.getByText("锁定 revision 的变更预览", { exact: true }).waitFor({ state: "visible" });
      const jsonPreview = api.goalConfigurationRequests.filter((item) => item.phase === "preview").at(-1);
      if (jsonPreview?.configuration?.timezone !== "Asia/Shanghai") throw new Error("Goal JSON changes did not reach the reviewed preview");
      await page.getByRole("button", { name: "应用此预览", exact: true }).click();
      await page.getByText("Goal 值已保存；共享投影仍需修复", { exact: true }).waitFor({ state: "visible" });
      if (!(await page.getByText(/loopx sync-global --goal-id/u).isVisible())) throw new Error("Partial Goal write did not expose its reconciliation action");
      const goalConfigurationApply = api.goalConfigurationRequests.find((item) => item.phase === "apply");
      if (goalConfigurationApply?.expected_plan_revision !== "sha256:goal-plan-periodic_report") throw new Error("Goal configuration apply lost its reviewed plan revision");

      await page.getByRole("button", { name: /自适应子 Agent 容量/u }).click();
      await page.getByRole("heading", { level: 2, name: /^自适应子 Agent 容量/ }).waitFor({ state: "visible" });
      const contextHelp = page.getByTestId("capability-context-phases");
      await contextHelp.locator("summary").click();
      for (const phase of ["before_plan", "before_delegate", "after_delegate_result"]) {
        await contextHelp.getByText(phase, { exact: true }).waitFor({ state: "visible" });
      }
      await contextHelp.getByText(/不能证明某次运行已读取或采纳/u).waitFor({ state: "visible" });
      await page.screenshot({ path: resolve(outputDir, "capability-context-phases-desktop.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)) {
        throw new Error("Capability lifecycle guidance overflows mobile viewport");
      }
      await page.screenshot({ path: resolve(outputDir, "capability-context-phases-mobile.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize({ width: 1440, height: 1000 });
      await contextHelp.locator("summary").click();
      const multiSubagentEnabled = page.getByLabel(/^启用$/u);
      const multiSubagentMaxChildren = page.getByLabel(/^最大子 Agent 数/u);
      const multiSubagentDomains = page.getByLabel(/^允许的职责域/u);
      await multiSubagentEnabled.waitFor({ state: "visible" });
      await waitForInputValue(multiSubagentMaxChildren, "4");
      await multiSubagentEnabled.check();
      await page.getByLabel(/^子 Agent 模型/u).fill("gpt-5.6-luna");
      await page.getByLabel(/^子 Agent 推理档位/u).fill("max");
      await multiSubagentMaxChildren.fill("3");
      await multiSubagentDomains.fill("code\nvalidation");
      await page.screenshot({ path: resolve(outputDir, "goal-subagent-capability-zh-cn.png"), fullPage: false, animations: "disabled" });
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await page.getByText("锁定 revision 的变更预览", { exact: true }).waitFor({ state: "visible" });
      const multiSubagentPreview = api.goalConfigurationRequests.findLast((item) => item.phase === "preview" && item.capability_id === "multi_subagent");
      if (JSON.stringify(multiSubagentPreview?.configuration) !== JSON.stringify({
        enabled: true,
        model: "gpt-5.6-luna",
        reasoning_effort: "max",
        max_children: 3,
        allowed_domains: ["code", "validation"],
      })) {
        throw new Error(`Unified Goal capability preview lost the sub-agent boundary: ${JSON.stringify(multiSubagentPreview)}`);
      }
      await page.getByRole("button", { name: "应用此预览", exact: true }).click();
      const multiSubagentApply = api.goalConfigurationRequests.findLast((item) => item.phase === "apply" && item.capability_id === "multi_subagent");
      if (multiSubagentApply?.expected_plan_revision !== "sha256:goal-plan-multi_subagent") {
        throw new Error(`Unified Goal capability apply lost its reviewed sub-agent revision: ${JSON.stringify(multiSubagentApply)}`);
      }
      await page.locator(".personal-capability-raw-values > summary").click();
      await page.locator(".personal-capability-value-grid section").first().getByText(/validation/u).waitFor({ state: "visible" });
      await page.getByRole("button", { name: "返回工作区", exact: true }).click();
      await page.getByRole("button", { name: /^(Tasks|任务)$/, current: "page" }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: "概览", exact: true }).click();
      await page.getByRole("button", { name: "Goal 信息", exact: true }).click();
      await page.getByText("仓库", { exact: true }).waitFor({ state: "visible" });
      await page.getByText("执行 Session", { exact: true }).waitFor({ state: "visible" });
      await page.locator(".personal-goal-repository").getByText("只读", { exact: true }).waitFor({ state: "visible" });
      if (!(await page.getByText("loopx-ai/loopx", { exact: true }).isVisible())) throw new Error("Goal drawer did not show the read-only repository context");
      await page.getByRole("button", { name: /关闭详情/ }).click();

      await page.locator('.personal-sidebar-utility[aria-label="设置"]').click();
      await page.getByRole("heading", { name: "Lark", exact: true }).waitFor({ state: "visible" });
      if (await page.locator(".personal-workspace-shell:visible").count()) throw new Error("Workspace Settings did not replace the workspace shell");
      if (await page.locator(".personal-channel-composer:visible").count()) throw new Error("Workspace Settings left the chat composer visible");
      if (await page.locator("[data-context-drawer]").count()) throw new Error("Workspace Settings left the context drawer visible");
      await page.screenshot({ path: resolve(outputDir, "workspace-settings.png"), fullPage: false, animations: "disabled" });

      // Two categories, two questions: the model provider holds the operator
      // credential, the global capabilities hold the machine defaults. Sharing
      // one surface is what let the credential panel overlap the workbench.
      // Two invariants per stacking container: its blocks must not overlap, and
      // a block that does not scroll must not spill its own content outside its
      // box. The second one is what a squeezed grid row actually produced: the
      // credential panel kept a 32px box while its content ran over the catalog
      // workbench below it.
      const stackedBlocks = () => page.evaluate(() => {
        for (const selector of [".personal-settings-body", ".personal-capability-settings", ".personal-capability-body"]) {
          const container = document.querySelector(selector);
          if (!container) continue;
          const children = [...container.children].filter((node) => node.getBoundingClientRect().height > 4);
          const blocks = children.map((node) => node.getBoundingClientRect());
          for (let index = 1; index < blocks.length; index += 1) {
            if (blocks[index].top < blocks[index - 1].bottom - 1) {
              return `${selector} blocks overlap`;
            }
          }
          for (const child of children) {
            const overflow = getComputedStyle(child).overflowY;
            if (overflow !== "visible") continue;
            if (child.scrollHeight > child.clientHeight + 2) {
              return `${selector} clips ${child.className.toString().split(/\s+/)[0]} (${child.clientHeight} < ${child.scrollHeight})`;
            }
          }
        }
        return "";
      });
      await page.getByRole("button", { name: /模型 Provider 配置/ }).click();
      await page.getByRole("heading", { level: 1, name: "模型 Provider 配置", exact: true }).waitFor({ state: "visible" });
      await waitForSelectorCount(page, ".personal-operator-credential", 1);
      await page.locator(".personal-operator-credential").first().waitFor({ state: "visible" });
      await page.locator(".personal-operator-credential-readback").waitFor({ state: "visible" });
      for (const label of [/^API key$/u, /^指纹$/u, /^Endpoint base URL$/u]) {
        await page.getByText(label).first().waitFor({ state: "visible" });
      }
      // The readback is redacted by construction: the key shows its fingerprint
      // and the fixture's own value never appears.
      const providerReadback = await page.locator(".personal-operator-credential-readback").innerText();
      if (!providerReadback.includes("已配置") || !providerReadback.includes("3efe046b2b3d")) {
        throw new Error(`Model provider readback lost its redacted projection: ${providerReadback}`);
      }
      if (providerReadback.includes("api-key-fixture")) {
        throw new Error("Model provider readback exposed a credential value");
      }
      if (api.operatorCredentialWrites.length) {
        throw new Error("Opening the model provider category wrote a credential");
      }
      const providerOverlap = await stackedBlocks();
      if (providerOverlap) throw new Error(`Model provider category ${providerOverlap}`);
      await page.screenshot({ path: resolve(outputDir, "model-provider-settings-zh-cn.png"), fullPage: false, animations: "disabled" });

      api.failNextMachineInspection = true;
      await page.getByRole("button", { name: "能力中心", exact: true }).click();
      await page.getByRole("heading", { level: 1, name: "能力中心", exact: true }).waitFor({ state: "visible" });
      const loadError = page.getByRole("alert").filter({ hasText: "无法读取机器配置" });
      await loadError.waitFor({ state: "visible" });
      if (await page.getByText("当前没有注册可在机器作用域配置的能力。", { exact: true }).count()) {
        throw new Error("A failed machine catalog request was presented as an empty registry");
      }
      await loadError.getByRole("button", { name: "重试" }).click();
      // The catalog workbench mounts after its inspection resolves, so the
      // category's contents are asserted only once the workbench itself exists.
      await page.locator(".personal-capability-layout").waitFor({ state: "visible" });
      if (await page.locator(".personal-operator-credential").count()) {
        throw new Error("The global capability category still hosted the operator credential panel");
      }
      const capabilityOverlap = await stackedBlocks();
      if (capabilityOverlap) throw new Error(`Global capability category ${capabilityOverlap}`);
      const machineCatalog = page.getByRole("navigation", { name: "机器能力目录" });
      const firstMachineCapability = machineCatalog.getByRole("button").filter({ hasText: "机器" }).first();
      await firstMachineCapability.waitFor({ state: "visible" });
      const initialMachineTitle = await firstMachineCapability.locator("strong").innerText();
      await page.getByRole("heading", { level: 2, name: initialMachineTitle, exact: true }).waitFor({ state: "visible" });
      if (await firstMachineCapability.getAttribute("aria-current") !== "page") {
        throw new Error("Initial machine selection must follow the visible catalog order, not the API source order");
      }
      if (await page.locator(".personal-capability-editor-status").count()) throw new Error("Editable machine settings must not show internal editor-contract notices");
      // This catalog contains only machine-scoped capabilities outside the
      // steward section. Goal-only entries belong in Goal settings.
      if (await machineCatalog.getByRole("button").count() !== 3) {
        throw new Error("Other machine settings must exclude steward and Goal-only capabilities");
      }
      const requestsBeforeReadOnly = api.machineConfigurationRequests.length;
      if (await machineCatalog.getByRole("button", { name: /^自适应子 Agent 容量/ }).count()
          || await machineCatalog.getByRole("button", { name: /^运行环境/ }).count()
          || await machineCatalog.getByRole("button", { name: /^模型与执行器/ }).count()
          || api.machineConfigurationRequests.length !== requestsBeforeReadOnly) {
        throw new Error("Other machine settings mixed Goal-only or steward controls into the catalog");
      }
      await machineCatalog.getByRole("button", { name: /^Goal 复核周期/ }).click();
      await page.getByLabel(/^两次 Goal 复核间的已完成 Todo 数/u).waitFor({ state: "visible" });
      await page.getByText(/不会创建 Turn、消耗配额或授予权限/u).waitFor({ state: "visible" });
      await machineCatalog.getByRole("button", { name: /^变更质量验证/ }).click();
      for (const label of [/^启用$/u, /^允许一次有界安全修复$/u, /^要求精确 diff 回执$/u]) {
        await page.getByLabel(label).waitFor({ state: "visible" });
      }
      await page.getByText(/不会授予文件、权限或合并权/u).waitFor({ state: "visible" });
      await page.screenshot({ path: resolve(outputDir, "machine-default-capabilities-zh-cn.png"), fullPage: false, animations: "disabled" });
      await machineCatalog.getByRole("button", { name: /^周期报告/ }).click();
      for (const label of [/^启用$/u, /^报告 Profile/u, /^Goal Channel 路由/u, /^时区/u]) {
        await page.getByLabel(label).waitFor({ state: "visible" });
      }
      await page.getByText("开启后将在已验证的阶段节点自动投递", { exact: true }).waitFor({ state: "visible" });
      // Activation authority and failure semantics must be visible before operating Enable.
      await page.getByText(/启用此订阅即授予持续投递权；发送失败或路由漂移会 fail closed/u).waitFor({ state: "visible" });
      await page.locator(".personal-capability-help > summary").click();
      const settingsScrollBounds = await page.evaluate(() => {
        const detail = document.querySelector(".personal-capability-detail");
        const catalog = document.querySelector(".personal-capability-list");
        return {
          viewportHeight: window.innerHeight,
          documentHeight: document.documentElement.scrollHeight,
          detailOverflow: getComputedStyle(detail).overflowY,
          catalogOverflow: getComputedStyle(catalog).overflowY,
          detailBottom: detail.getBoundingClientRect().bottom,
        };
      });
      if (settingsScrollBounds.documentHeight > settingsScrollBounds.viewportHeight + 1
        || settingsScrollBounds.detailBottom > settingsScrollBounds.viewportHeight + 1
        || settingsScrollBounds.detailOverflow !== "auto"
        || settingsScrollBounds.catalogOverflow !== "auto") {
        throw new Error(`Settings escaped their viewport scroll boundaries: ${JSON.stringify(settingsScrollBounds)}`);
      }
      await page.locator(".personal-capability-help > summary").click();
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      await page.getByText("审阅机器配置变更", { exact: true }).waitFor({ state: "visible" });
      const machineConfigurationPreview = api.machineConfigurationRequests.find((item) => item.phase === "preview");
      const machineKeys = Object.keys(machineConfigurationPreview?.namespace_configuration ?? {}).sort((left, right) => left.localeCompare(right));
      if (JSON.stringify(machineKeys) !== JSON.stringify(["enabled", "inheritance", "profile_preset", "route_ref", "schema_version", "timezone"])) {
        throw new Error(`Machine guided editor lost capability-owned hidden fields: ${JSON.stringify(machineConfigurationPreview)}`);
      }
      await page.getByRole("button", { name: "应用已审阅预览", exact: true }).click();
      await page.getByText("机器策略已应用，并通过回读校验。", { exact: true }).waitFor({ state: "visible" });
      const machineConfigurationApply = api.machineConfigurationRequests.find((item) => item.phase === "apply");
      if (machineConfigurationApply?.expected_plan_revision !== "sha256:machine-plan") throw new Error("Machine configuration apply lost its reviewed plan revision");
      await page.screenshot({ path: resolve(outputDir, "machine-capability-behavior-zh-cn.png"), fullPage: false, animations: "disabled" });

      await page.getByRole("button", { name: /语言/ }).click();
      await page.getByRole("radio", { name: /English/ }).click();
      await page.getByRole("button", { name: /Capability Center/ }).click();
      await page.getByRole("heading", { level: 2, name: "Periodic reports", exact: true }).waitFor({ state: "visible" });
      const rawValues = page.locator(".personal-capability-raw-values");
      if (await rawValues.getAttribute("open") !== null) throw new Error("Raw JSON must be collapsed by default");
      if (!await page.locator(".personal-capability-actions").evaluate((actions) => Boolean(actions.compareDocumentPosition(document.querySelector(".personal-capability-raw-values")) & Node.DOCUMENT_POSITION_FOLLOWING))) {
        throw new Error("Readable configuration actions must precede raw JSON diagnostics");
      }
      await rawValues.locator("summary").focus();
      await page.keyboard.press("Enter");
      await rawValues.locator("pre").first().waitFor({ state: "visible" });
      await page.keyboard.press("Enter");
      if (await rawValues.getAttribute("open") !== null) throw new Error("Raw JSON keyboard collapse failed");
      for (const label of [/^Enabled$/u, /^Report profile/u, /^Goal Channel route/u, /^Timezone/u]) {
        await page.getByLabel(label).waitFor({ state: "visible" });
      }
      await page.getByText("Enabled means automatic delivery at validated stage boundaries", { exact: true }).waitFor({ state: "visible" });
      await page.screenshot({ path: resolve(outputDir, "machine-capability-en.png"), fullPage: false, animations: "disabled" });
      await page.getByRole("button", { name: /Goal capabilities/ }).click();
      await page.getByRole("button", { name: /Adaptive child capacity/ }).click();
      await page.getByRole("heading", { level: 2, name: "Adaptive child capacity", exact: true }).waitFor({ state: "visible" });
      for (const label of [/^Enabled$/u, /^Child model/u, /^Child reasoning effort/u, /^Maximum children/u, /^Allowed responsibility domains/u]) {
        await page.getByLabel(label).waitFor({ state: "visible" });
      }
      await page.screenshot({ path: resolve(outputDir, "goal-subagent-capability-en.png"), fullPage: false, animations: "disabled" });
      await page.getByRole("button", { name: /Language/ }).click();
      await page.getByRole("radio", { name: /Simplified Chinese/ }).click();
      await page.getByRole("button", { name: "能力中心", exact: true }).click();
      await page.locator(".personal-settings-body").evaluate((element) => element.scrollTo({ top: 0 }));
      await page.screenshot({ path: resolve(outputDir, "machine-capability-zh-cn.png"), fullPage: false, animations: "disabled" });
      // Steward owns a first-level destination with just model/executor and
      // runtime. The exact reviewed revision still uses the machine store.
      await page.locator(".personal-settings-tabs").getByRole("button", { name: "管家", exact: true }).click();
      await page.getByRole("heading", { level: 2, name: "模型与执行器", exact: true }).waitFor({ state: "visible" });
      const stewardFields = page.locator(".personal-capability-fields");
      // The selects carry their option text inside the same label, so they are
      // matched by prefix rather than by an exact label string.
      await stewardFields.getByLabel(/^选择策略/u).selectOption("preferred");
      await stewardFields.getByLabel(/^模型/u).waitFor({ state: "visible" });
      await stewardFields.getByLabel(/^推理档位/u).waitFor({ state: "visible" });
      await stewardFields.getByLabel(/^首选管家执行器/u).selectOption("dsh");
      if (await stewardFields.getByLabel(/^灵活池可用执行器/u).count()) {
        throw new Error("Preferred steward routing must not show the flexible fallback pool");
      }
      await stewardFields.getByLabel(/^模型/u).fill("deepseek-v4-flash");
      await stewardFields.getByLabel(/^推理档位/u).selectOption("high");
      await page.screenshot({ path: resolve(outputDir, "machine-steward-executor-zh-cn.png"), fullPage: false, animations: "disabled" });
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      const stewardPreview = api.machineConfigurationRequests.findLast(
        (item) => item.phase === "preview" && item.namespace === "steward_executor",
      );
      if (stewardPreview?.namespace_configuration?.executor_endpoint !== "dsh"
        || stewardPreview?.namespace_configuration?.selection_policy !== "preferred"
        || !Array.isArray(stewardPreview?.namespace_configuration?.eligible_endpoints)
        || stewardPreview.namespace_configuration.eligible_endpoints.length !== 0
        || stewardPreview?.namespace_configuration?.executor_model !== "deepseek-v4-flash"
        || stewardPreview?.namespace_configuration?.executor_reasoning_effort !== "high") {
        throw new Error(`The steward executor form did not preview the selected executor: ${JSON.stringify(stewardPreview)}`);
      }
      await page.getByRole("button", { name: "应用已审阅预览", exact: true }).click();
      const stewardApply = api.machineConfigurationRequests.findLast(
        (item) => item.phase === "apply" && item.namespace === "steward_executor",
      );
      if (stewardApply?.expected_plan_revision !== "sha256:machine-plan") {
        throw new Error("The steward executor apply lost its reviewed plan revision");
      }
      await page.locator(".personal-capability-list").getByRole("button", { name: "运行环境", exact: true }).click();
      await page.getByLabel(/^运行模式/u).waitFor({ state: "visible" });
      await page.locator(".personal-capability-help > summary").click();
      await page.getByText(/受保护操作仍单独校验/u).waitFor({ state: "visible" });
      await page.screenshot({ path: resolve(outputDir, "manager-runtime-machine-profile.png"), fullPage: false, animations: "disabled" });
      const settingsViewport = page.viewportSize();
      await page.setViewportSize({ width: 390, height: 844 });
      await page.waitForTimeout(200);
      const machineOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      if (machineOverflow > 1) throw new Error(`Machine capability settings overflow the mobile viewport by ${machineOverflow}px`);
      await page.screenshot({ path: resolve(outputDir, "machine-capability-mobile-zh-cn.png"), fullPage: false, animations: "disabled" });
      await page.setViewportSize(settingsViewport);
      await page.waitForTimeout(200);
      await page.getByRole("button", { name: /Lark/ }).click();

      api.machineInspectionStatus = "invalid";
      api.invalidMachineNamespaces = ["manager_runtime"];
      await page.locator(".personal-settings-tabs").getByRole("button", { name: "管家", exact: true }).click();
      const invalidRepair = page.getByTestId("machine-invalid-repair");
      await invalidRepair.waitFor({ state: "visible" });
      await page.getByRole("heading", { level: 2, name: "运行环境", exact: true }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      const managerRepairPreview = api.machineConfigurationRequests.findLast(
        (item) => item.phase === "preview" && item.namespace === "manager_runtime",
      );
      if (managerRepairPreview?.namespace_configuration?.runtime_profile !== "restricted") {
        throw new Error(`Invalid Manager runtime did not use its safe catalog replacement: ${JSON.stringify(managerRepairPreview)}`);
      }
      await page.getByRole("button", { name: "应用已审阅预览", exact: true }).click();
      await invalidRepair.waitFor({ state: "detached" });
      const managerRepairApply = api.machineConfigurationRequests.findLast(
        (item) => item.phase === "apply" && item.namespace === "manager_runtime",
      );
      if (managerRepairApply?.expected_plan_revision !== "sha256:machine-plan") {
        throw new Error("Invalid Manager runtime repair lost its reviewed plan revision");
      }

      await page.getByRole("button", { name: /Lark/ }).click();
      api.machineInspectionStatus = "invalid";
      api.invalidMachineNamespaces = ["periodic_report"];
      await page.getByRole("button", { name: "能力中心", exact: true }).click();
      await invalidRepair.waitFor({ state: "visible" });
      await page.getByRole("heading", { level: 2, name: "周期报告", exact: true }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: "预览变更", exact: true }).click();
      const periodicRepairPreview = api.machineConfigurationRequests.findLast(
        (item) => item.phase === "preview" && item.namespace === "periodic_report",
      );
      if (!periodicRepairPreview) throw new Error("Invalid sibling namespace did not open the Periodic reports repair path");
      await page.getByRole("button", { name: "应用已审阅预览", exact: true }).click();
      await invalidRepair.waitFor({ state: "detached" });
      await page.screenshot({ path: resolve(outputDir, "machine-invalid-namespace-repaired.png"), fullPage: false, animations: "disabled" });
      await page.getByRole("button", { name: /Lark/ }).click();

      await page.getByRole("button", { name: /连接 Lark App/ }).click();
      const connectDialog = page.getByRole("dialog", { name: "连接 Lark App" });
      await connectDialog.waitFor({ state: "visible" });
      if (await connectDialog.getByLabel("连接用途").inputValue() !== "manager") throw new Error("Machine-level Lark setup must default to the built-in manager");
      await connectDialog.getByLabel("连接用途").selectOption("goal");
      await connectDialog.getByRole("option", { name: "Product group" }).waitFor({ state: "attached" });
      await connectDialog.getByLabel("群聊").selectOption({ label: "Product group" });
      await connectDialog.getByLabel("接收范围").selectOption("configured_chat_all");
      const ingressGroup = connectDialog.getByRole("group", { name: "Agent 入站方式" });
      const ingressOptions = await ingressGroup.locator("input[type=radio]").evaluateAll((options) => options.map((option) => option.value));
      if (JSON.stringify(ingressOptions) !== JSON.stringify(["live_steering", "session_queue", "async_inbox"])) throw new Error(`Lark Agent ingress modes drifted: ${JSON.stringify(ingressOptions)}`);
      await ingressGroup.getByLabel("异步收件箱").check();
      await connectDialog.getByLabel("目标 Agent").waitFor({ state: "visible" });
      await connectDialog.getByLabel("绑定到 Goal").selectOption("multi-agent-projection");
      const agentOptions = await connectDialog.getByLabel("目标 Agent").locator("option").evaluateAll((items) => items.map((item) => item.value));
      if (JSON.stringify([...agentOptions].sort((left, right) => left.localeCompare(right))) !== JSON.stringify(["codex-latest-lane", "codex-older-lane"])) throw new Error(`Lark omitted a peer Agent: ${JSON.stringify(agentOptions)}`);
      await connectDialog.getByLabel("目标 Agent").selectOption("codex-older-lane");
      await connectDialog.getByLabel("回复方式").selectOption("topic_reply");
      await page.screenshot({ path: resolve(outputDir, "lark-routing-modes.png"), fullPage: false, animations: "disabled" });
      await connectDialog.getByRole("button", { name: "连接", exact: true }).click();
      await connectDialog.waitFor({ state: "hidden" });
      const connectionReadback = await page.evaluate(async () => (await fetch("/api/chat/lark/connections")).json());
      if (connectionReadback.connections?.length !== 1) throw new Error(`Lark connection API readback mismatch: ${JSON.stringify(connectionReadback)}`);
      try {
        await page.locator(".personal-lark-table-row", { hasText: "Product group" }).waitFor({ state: "visible", timeout: 10_000 });
      } catch (error) {
        await page.screenshot({ path: resolve(outputDir, "lark-connection-refresh-failed.png"), fullPage: true, animations: "disabled" });
        throw new Error(`${error.message}; body=${(await page.locator("body").innerText()).slice(0, 4000)}`);
      }
      const connectedRow = page.locator(".personal-lark-table-row", { hasText: "Product group" });
      await page.getByText("1 条 Lark 路由尚未验证", { exact: true }).waitFor({ state: "visible" });
      if (!(await connectedRow.getByText("事件订阅待验证", { exact: false }).isVisible())) throw new Error("A zero-event listener was presented as automatic-reply ready");
      if (!(await connectedRow.getByRole("link", { name: "查看飞书事件配置" }).isVisible())) throw new Error("An unverified Lark event subscription lacked repair guidance");
      if (api.larkWrites.length !== 1 || api.larkWrites[0].execute !== true) throw new Error("Lark connect did not perform exactly one approved external write");
      if (api.larkWrites[0].capture_scope !== "configured_chat_all" || api.larkWrites[0].incoming_mode !== "all") throw new Error(`Lark capture mode was not projected: ${JSON.stringify(api.larkWrites[0])}`);
      if (api.larkWrites[0].ingress_mode !== "async_inbox" || !api.larkWrites[0].agent_id) throw new Error(`Lark Agent inbox mode lost its Agent binding: ${JSON.stringify(api.larkWrites[0])}`);
      if (api.larkWrites[0].agent_id !== "codex-older-lane" || api.larkWrites[0].goal_id !== "multi-agent-projection") throw new Error("Lark replaced the selected peer with the default Agent");
      if (api.larkWrites[0].reply_mode !== "topic_reply") throw new Error(`Lark reply mode was not projected: ${JSON.stringify(api.larkWrites[0])}`);
      Object.assign(api.larkConnections[0], {
        event_count: 1,
        health_error_code: "lark_event_route_mismatch",
        last_event_reason: "topic_mismatch",
        last_event_status: "ignored",
      });
      const mismatchReadback = await page.evaluate(async () => (await fetch("/api/chat/lark/connections")).json());
      if (mismatchReadback.connections?.[0]?.last_event_reason !== "topic_mismatch") {
        throw new Error(`Lark route mismatch API readback mismatch: ${JSON.stringify(mismatchReadback)}`);
      }
      await page.getByRole("button", { name: "返回工作区", exact: true }).click();
      await checkpointCoverage();
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible" });
      await page.locator('.personal-sidebar-utility[aria-label="设置"]').click();
      const routeMismatchRow = page.locator(".personal-lark-table-row", { hasText: "Product group" });
      try {
        await routeMismatchRow.getByText("消息未匹配当前 Goal Topic", { exact: false }).waitFor({ state: "visible" });
      } catch (error) {
        throw new Error(`${error.message}; body=${(await page.locator("body").innerText()).slice(0, 4000)}`);
      }
      await routeMismatchRow.getByText("请重新选择群聊并连接该 Goal", { exact: false }).waitFor({ state: "visible" });
      await page.locator(".personal-lark-table-row", { hasText: "Product group" }).getByRole("button", { name: /配置/ }).click();
      const editDialog = page.getByRole("dialog", { name: "编辑 Lark 连接" });
      await editDialog.waitFor({ state: "visible" });
      if (await editDialog.getByLabel("接收范围").inputValue() !== "configured_chat_all") throw new Error("Lark edit mode did not restore capture_scope");
      if (!await editDialog.getByRole("group", { name: "Agent 入站方式" }).getByLabel("异步收件箱").isChecked()) throw new Error("Lark edit mode did not restore ingress_mode");
      if (await editDialog.getByLabel("目标 Agent").inputValue() !== api.larkWrites[0].agent_id) throw new Error("Lark edit mode did not restore agent_id");
      await editDialog.getByRole("button", { name: "取消", exact: true }).click();
      await editDialog.waitFor({ state: "hidden" });
      await page.locator(".personal-lark-toolbar").getByRole("button", { name: /连接 Lark App/ }).click();
      const batchDialog = page.getByRole("dialog", { name: "连接 Lark App" });
      await batchDialog.getByLabel("连接用途").selectOption("goal");
      await batchDialog.getByRole("option", { name: "Product group" }).waitFor({ state: "attached" });
      await batchDialog.getByLabel("群聊").selectOption({ label: "Product group" });
      await batchDialog.getByLabel("绑定到 Goal").selectOption("multi-agent-projection");
      await batchDialog.getByRole("checkbox", { name: "连接全部已注册 Agent" }).check();
      const agentAppGroup = batchDialog.getByRole("group", { name: "每个 Agent 的 Lark App" });
      await agentAppGroup.getByLabel(/codex-older-lane 的 Lark App/).selectOption("mew-research");
      await batchDialog.getByRole("button", { name: "一键连接 2 个 Agent", exact: true }).click();
      await batchDialog.waitFor({ state: "hidden" });
      if (api.larkWrites.length !== 3 || api.larkConnections.length !== 2) throw new Error("Per-Agent App batch did not preserve both Agent routes");
      await page.getByText("2 条 Lark 路由尚未验证", { exact: true }).waitFor({ state: "visible" });
      const perAgentAppWrites = Object.fromEntries(api.larkWrites.slice(1).map((item) => [item.agent_id, item.app_ref]));
      if (perAgentAppWrites["codex-older-lane"] !== "mew-research" || perAgentAppWrites["codex-latest-lane"] !== "mew") throw new Error(`Per-Agent App selection was not preserved: ${JSON.stringify(perAgentAppWrites)}`);
      if (!api.larkConnections.some((item) => item.agent_id === "codex-older-lane") || !api.larkConnections.some((item) => item.agent_id === "codex-latest-lane")) throw new Error("One-click Goal Channel lost a peer Agent route");
      const legacyConnection = api.larkConnections.find((item) => item.agent_id === "codex-older-lane");
      Object.assign(legacyConnection, { ingress_mode: "direct_session", app_ref: "profile-alias-not-in-catalog", app_label: "Original Bot" });
      const legacyId = legacyConnection.connection_id;
      await page.getByRole("button", { name: "返回工作区", exact: true }).click();
      await page.locator('.personal-sidebar-utility[aria-label="设置"]').click();
      const legacyRow = page.locator(".personal-lark-table-row", { hasText: "Original Bot" });
      await legacyRow.getByText("待升级", { exact: true }).waitFor({ state: "visible" });
      await legacyRow.getByRole("button", { name: /配置/ }).click();
      const upgradeDialog = page.getByRole("dialog", { name: "编辑 Lark 连接" });
      await upgradeDialog.getByText("Original Bot", { exact: true }).waitFor({ state: "visible" });
      if (await upgradeDialog.getByRole("combobox", { name: "Lark App", exact: true }).count()) throw new Error("An unknown App alias must not display the first catalog App");
      if (!await upgradeDialog.getByLabel("异步收件箱", { exact: true }).isChecked()) throw new Error("Legacy editing must default to async inbox");
      if (!await upgradeDialog.getByLabel("接收范围", { exact: true }).isDisabled()) throw new Error("Migration must preserve the old capture scope");
      await upgradeDialog.getByRole("button", { name: "保存连接", exact: true }).click();
      await upgradeDialog.waitFor({ state: "hidden" });
      if (legacyConnection.connection_id !== legacyId || legacyConnection.app_ref !== "profile-alias-not-in-catalog" || legacyConnection.ingress_mode !== "async_inbox" || api.larkConnections.length !== 2) throw new Error("Upgrade changed the connection identity or duplicated the route");
      const removedConnection = api.larkConnections.find((item) => item.agent_id === "codex-older-lane");
      const originalAgent = removedConnection.agent_id;
      removedConnection.agent_id = "removed-peer";
      await page.getByRole("button", { name: "返回工作区", exact: true }).click();
      await page.locator('.personal-sidebar-utility[aria-label="设置"]').click();
      await page.locator(".personal-lark-table-row", { hasText: "removed-peer" }).getByRole("button", { name: /配置/ }).click();
      await editDialog.getByRole("alert").filter({ hasText: "不会自动替换" }).waitFor({ state: "visible" });
      if (!(await editDialog.getByRole("button", { name: "保存连接", exact: true }).isDisabled())) throw new Error("Removed recipient remained connectable");
      if (await editDialog.getByLabel("目标 Agent").inputValue() !== "removed-peer") throw new Error("Removed recipient silently fell back to another Agent");
      await editDialog.getByRole("button", { name: "取消" }).click();
      removedConnection.agent_id = originalAgent;
      await page.locator(".personal-lark-toolbar").getByRole("button", { name: /连接 Lark App/ }).click();
      const managerDialog = page.getByRole("dialog", { name: "连接 Lark App" });
      await managerDialog.getByRole("option", { name: "Product group" }).waitFor({ state: "attached" });
      if (await managerDialog.getByLabel("连接用途").inputValue() !== "manager") throw new Error("Machine manager default was not restored for new setup");
      if (await managerDialog.getByLabel("目标 Agent").count()) throw new Error("The built-in manager must not require selecting a worker Agent");
      await managerDialog.getByRole("button", { name: "连接", exact: true }).click();
      await managerDialog.waitFor({ state: "hidden" });
      const managerWrite = api.larkWrites.at(-1);
      if (managerWrite.conversation_kind !== "manager" || managerWrite.ingress_mode !== "session_queue" || managerWrite.agent_bindings) throw new Error("Manager setup did not request its synchronous singleton service");
      await page.getByText("管家 · 同步对话", { exact: true }).waitFor({ state: "visible" });
      await page.screenshot({ path: resolve(outputDir, "lark-goal-connections.png"), fullPage: false, animations: "disabled" });
      await page.getByRole("button", { name: "返回工作区", exact: true }).click();
      await selectProductReleaseGoal();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Tasks|任务)$/ }).click();
      await page.locator(".personal-object-list").first().waitFor({ state: "visible" });
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Files|成果)$/ }).click();
      const reportOutput = page.getByTestId("personal-goal-outputs").getByRole("button", { name: /Product Release milestone report/ });
      await reportOutput.waitFor({ state: "visible" });
      await reportOutput.click();
      await page.getByTestId("personal-periodic-report-detail").getByText("Release candidate verified", { exact: true }).waitFor({ state: "visible" });
      await page.getByTestId("personal-periodic-report-detail").getByText("Rollout plan updated", { exact: true }).waitFor({ state: "visible" });
      if (await page.locator('[data-testid="frontstage-milestone-reports"]').count()) throw new Error("Milestone report still rendered in the deprecated Ops Frontstage");
      await page.getByRole("button", { name: /关闭详情/ }).click();
      await selectFirstGoal();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();

      const composer = page.getByLabel("向 LoopX 发送消息");
      const previewCountBeforeSemanticIntent = api.actionPreviews.length;
      async function expectConversationalProtectedTurn(message, answer, previewError) {
        await composer.fill(message);
        await page.getByRole("button", { name: "发送", exact: true }).click();
        await page.getByText(answer, { exact: true }).last().waitFor({ state: "visible", timeout: 10_000 });
        if (api.actionPreviews.length !== previewCountBeforeSemanticIntent) throw new Error(previewError);
      }
      await expectConversationalProtectedTurn("请只回复：合并后真实回复已收到", "合并后真实回复已收到", "An exact-wording protected-action mention created a typed preview");
      await expectConversationalProtectedTurn("请分析：合并 PR #123 后会有什么风险", "主要风险是检查未完成或目标分支发生变化；这里只做分析，不会创建合并预览。", "Protected-action analysis created a typed preview");
      await expectConversationalProtectedTurn("请合并", "请告诉我要合并的具体 PR 或 MR；在目标明确前不会创建执行预览。", "A targetless protected action created an incomplete preview");
      await expectConversationalProtectedTurn("请合并我刚才说的那个", "这个指代不够明确，请提供具体 PR 或 MR。", "A model-invented protected target created a typed preview");

      await composer.fill("请合并 PR #123");
      await page.getByRole("button", { name: "发送", exact: true }).click();
      await page.getByText("确认执行").waitFor({ state: "visible", timeout: 10_000 });
      const protectedMerge = api.actionPreviews.find((preview) => preview.action_kind === "goal.update" && preview.summary.includes("PR #123"));
      if (!protectedMerge) throw new Error("A clear Agent semantic proposal did not create the protected typed preview");
      await page.screenshot({ path: resolve(outputDir, "semantic-protected-action-preview.png"), fullPage: false, animations: "disabled" });
      await page.getByRole("button", { name: "关闭", exact: true }).click();

      const beforeNaturalTodo = api.actionPreviews.length;
      const naturalTodoRequest = "添加一个「补充回归测试」普通 Todo，并交给 Codex。不要设置 Heartbeat，也不要创建定时检查";
      await composer.fill(naturalTodoRequest);
      await page.getByRole("button", { name: "发送", exact: true }).click();
      await page.waitForFunction(() => !document.querySelector('.personal-quick-prompts button')?.disabled);
      if (api.actionPreviews.length !== beforeNaturalTodo || api.turnRequests.at(-1)?.message !== naturalTodoRequest) throw new Error("Natural Todo request was intercepted before Chat");

      const previewCountBeforeAnalysis = api.actionPreviews.length;
      const turnCountBeforeAnalysis = api.turnRequests.length;
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Tasks|任务)$/ }).click();
      await composer.fill("做一次只读分析：判断刚刚新增的 Todo 是否与当前 Goal 一致，并在当前 Chat 返回两点理由。不要修改状态。");
      await page.getByRole("button", { name: "发送", exact: true }).click();
      const taskConversationReceipt = page.getByRole("region", { name: "最近对话" });
      await taskConversationReceipt.getByText("对话有新回复", { exact: true }).waitFor({ state: "visible", timeout: 10_000 });
      await taskConversationReceipt.getByRole("button", { name: "转为任务草稿", exact: true }).waitFor({ state: "visible" });
      if (await taskConversationReceipt.locator("p").count() !== 1) throw new Error("Tasks repeated the full exchange instead of one reply preview");
      if (api.actionPreviews.length !== previewCountBeforeAnalysis) throw new Error("A read-only reference to an existing Todo created another Todo preview");
      if (api.turnRequests.length <= turnCountBeforeAnalysis) throw new Error("Read-only Todo analysis did not reach the Goal Chat Session");
      await page.screenshot({ path: resolve(outputDir, "task-chat-receipt.png"), fullPage: false, animations: "disabled" });
      await taskConversationReceipt.getByRole("button", { name: "查看回复" }).click();
      await page.getByText("已沿用当前 Goal 与 Agent Session。接下来会先核对状态，再继续推进。", { exact: true }).last().waitFor({ state: "visible", timeout: 10_000 });
      await page.waitForFunction(() => {
        const replies = document.querySelectorAll(".personal-message.is-assistant");
        const reply = replies.item(replies.length - 1);
        const viewport = reply?.closest(".personal-channel-scroll");
        if (!reply || !viewport) return false;
        const answerBox = reply.getBoundingClientRect();
        const viewportBox = viewport.getBoundingClientRect();
        return answerBox.top < viewportBox.bottom && answerBox.bottom > viewportBox.top;
      }, undefined, { timeout: 10_000 });
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Tasks|任务)$/ }).click();
      await page.getByRole("region", { name: "最近对话" }).getByRole("button", { name: "转为任务草稿" }).click();
      const taskDraftForm = page.getByRole("dialog", {name: "创建任务", exact: true});
      if (!(await taskDraftForm.getByLabel("任务内容", {exact: true}).inputValue())) throw new Error("Task form lost the reply");
      await taskDraftForm.getByLabel("任务内容", {exact: true}).fill(`${"长".repeat(200)}\n\n${"文".repeat(201)}`);
      await taskDraftForm.getByRole("status").waitFor();
      if (!(await taskDraftForm.getByRole("button", {name: "检查配置"}).isDisabled())) throw new Error("Over-limit task text reached preview");
      await taskDraftForm.getByRole("button", {name: "取消", exact: true}).click();
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();

      const beforeNaturalBinding = api.actionPreviews.length;
      await composer.fill("让 Claude Code 负责管理这个 Goal");
      await page.getByRole("button", { name: "发送", exact: true }).click();
      await page.waitForFunction(() => !document.querySelector('.personal-quick-prompts button')?.disabled);
      if (api.actionPreviews.length !== beforeNaturalBinding || api.turnRequests.at(-1)?.message !== "让 Claude Code 负责管理这个 Goal") throw new Error("Assignment was interpreted by browser rules");

      const selectedGoalId = new URL(page.url()).searchParams.get("goalId");
      if (!selectedGoalId) throw new Error("Selected Goal URL did not preserve goalId for the Session authority smoke");
      const authoritativeSessionId = `session-authoritative-${selectedGoalId}`;
      page.__loopxRuntime.sessions.set(authoritativeSessionId, {
        session_id: authoritativeSessionId,
        goal_id: selectedGoalId,
        agent_id: "codex",
        adapter_kind: "codex",
        channel_id: "task.todo-session-authority-smoke",
        status: "stale",
        active_turn_id: null,
        last_error_code: null,
        created_at: "2026-08-13T01:00:00Z",
        updated_at: "2026-08-13T01:00:00Z",
        last_activity_at: "2026-08-13T01:00:00Z",
        resumable: true,
      });
      page.__loopxRuntime.messages.set(authoritativeSessionId, []);
      const authoritativeRun = page.locator(".personal-run-row", { hasText: "Agent 执行任务" });
      await authoritativeRun.waitFor({ state: "visible", timeout: 5_000 });
      page.__loopxRuntime.messages.set(authoritativeSessionId, [{
        message_id: "message-authoritative-result",
        turn_id: "turn-authoritative-result",
        role: "agent",
        text: "权威 Session 已完成只读分析，并返回可核验结果。",
        created_at: "2026-08-13T01:00:03Z",
      }]);
      await authoritativeRun.click();
      await page.getByText("执行 Session", { exact: true }).waitFor({ state: "visible" });
      if (await page.getByRole("tab", { name: "执行过程与结果" }).getAttribute("aria-selected") !== "true") throw new Error("Session drawer did not open on the execution record");
      const authoritativeRecord = page.locator(".personal-session-message-record");
      try {
        await authoritativeRecord.locator("header strong").filter({ hasText: "已完成" }).first().waitFor({ state: "visible", timeout: 8_000 });
      } catch (error) {
        await page.screenshot({ path: resolve(outputDir, "session-authority-refresh-failed.png"), fullPage: true, animations: "disabled" });
        throw new Error(`${error.message}; body=${(await page.locator("body").innerText()).slice(-5000)}`);
      }
      await page.getByLabel("执行 Session").getByText("1/1", { exact: true }).waitFor({ state: "visible" });
      await page.getByText("权威 Session 已完成只读分析，并返回可核验结果。", { exact: true }).waitFor({ state: "visible" });
      if (await page.getByText("stale", { exact: true }).count()) throw new Error("Fresh Session result left a stale status visible");
      await page.getByText("运行记录", { exact: true }).waitFor({ state: "visible" });
      await page.screenshot({ path: resolve(outputDir, "session-execution-record.png"), fullPage: false, animations: "disabled" });
      await page.getByRole("tab", { name: "详情与操作" }).click();
      const correction = page.getByLabel("输入纠偏信息");
      const turnCountBeforeCorrection = api.turnRequests.length;
      await correction.fill("先核对权限边界，再继续推进。");
      await page.getByRole("button", { name: "发送纠偏" }).click();
      try {
        const correctionDeadline = Date.now() + 10_000;
        while (api.turnRequests.length <= turnCountBeforeCorrection && Date.now() < correctionDeadline) {
          await page.waitForTimeout(100);
        }
        await page.getByText(/已沿用当前 Goal/).last().waitFor({ state: "visible", timeout: 10_000 });
      } catch (error) {
        await page.screenshot({ path: resolve(outputDir, "run-correction-failed.png"), fullPage: true, animations: "disabled" });
        throw new Error(`${error.message}; turns=${JSON.stringify(api.turnRequests)}; errors=${pageErrors.join(" | ")}; body=${(await page.locator("body").innerText()).slice(0, 4000)}`);
      }
      const firstCorrection = api.turnRequests.slice(turnCountBeforeCorrection).find((turn) => turn.message === "先核对权限边界，再继续推进。");
      if (!firstCorrection?.sessionId || firstCorrection.sessionId.includes("manager")) throw new Error(`Run correction did not use the selected Goal's execution Session: ${JSON.stringify(firstCorrection)}`);
      pass(5, "Run-detail correction used a recoverable Goal-scoped Agent Session.");
      await page.getByRole("button", { name: /关闭详情/ }).click();

      const writesBeforeHeartbeat = api.durableWriteCount;
      await page.getByRole("button", {name: "概览", exact: true}).click();
      await page.getByRole("button", {name: "Goal 信息", exact: true}).click();
      await page.getByRole("button", {name: "设置 Heartbeat", exact: true}).click();
      await page.getByRole("dialog", {name: "Goal Heartbeat", exact: true}).getByRole("button", {name: "检查配置"}).click();
      await page.getByText("确认执行").waitFor({ state: "visible" });
      await page.getByRole("button", { name: "确认并应用", exact: true }).click();
      await page.getByText("需要宿主确认").waitFor({ state: "visible" });
      if (api.durableWriteCount !== writesBeforeHeartbeat) throw new Error("Protected heartbeat gate wrote durable state");
      pass(8, "Agent semantic protected intent creates only a typed preview, while discussion and targetless requests remain conversational and all protected-gate paths perform zero durable writes before confirmation.");
      pass(11, "Heartbeat apply surfaced an explicit host-activation gate.");
      const heartbeatPreview = api.actionPreviews.find((preview) => preview.action_kind === "heartbeat.bind");
      if (!heartbeatPreview) throw new Error("Continuation intent did not map to heartbeat.bind");
      await page.getByRole("button", { name: "关闭", exact: true }).click();

      await page.getByRole("button", { name: "概览", exact: true }).click();
      await page.getByRole("button", { name: "Goal 信息", exact: true }).click();
      await page.getByRole("button", { name: /^(Tasks|任务)$/ }).click();
      const taskCards = page.locator(".personal-object-list", { hasText: "进行中" }).locator(".personal-task-card");
      const taskRow = taskCards.first().locator(":scope > button");
      await taskRow.click();
      const taskInspector = page.getByRole("dialog", { name: "Todo 详情" });
      await taskInspector.waitFor({ state: "visible" });
      await page.waitForFunction(() => document.querySelector('[data-context-drawer]')?.contains(document.activeElement));
      const mainBox = await page.locator(".personal-workspace-main").boundingBox();
      const inspectorBox = await page.locator('[data-context-drawer][data-drawer-mode="inspector"]').boundingBox();
      if (!mainBox || !inspectorBox || mainBox.x + mainBox.width > inspectorBox.x + 1) throw new Error("Half-screen Todo inspector covered the task board instead of occupying its own layout column");
      if (!(await page.locator(".personal-task-card.is-selected").isVisible())) throw new Error("Opening a Todo did not keep its selected card visible in the board viewport");
      await page.getByRole("button", { name: "切换到全屏", exact: true }).click();
      if (await page.locator(".personal-workspace-main").isVisible()) throw new Error("Full-screen Todo inspector left the board visible");
      await page.getByRole("button", { name: "切换到半屏", exact: true }).click();
      if (!(await page.locator(".personal-workspace-main").isVisible())) throw new Error("Half-screen Todo inspector did not restore the board");
      if (await taskCards.count() < 2) throw new Error("Todo focus smoke requires two task cards");
      const secondTaskRow = taskCards.nth(1).locator(":scope > button");
      await secondTaskRow.click();
      await page.waitForFunction(() => document.activeElement?.id === "personal-drawer-title");
      await page.getByRole("button", { name: /关闭详情/ }).click();
      await page.waitForFunction(() => document.activeElement?.closest(".personal-task-card") === document.querySelectorAll(".personal-task-card")[1]);
      await taskRow.click();
      let taskManagement = page.locator("details.personal-task-management");
      await taskManagement.locator("summary").click();
      await taskManagement.locator(".personal-inline-agent-select", { hasText: "改派给" }).getByRole("button", { name: "查看处理方式", exact: true }).click();
      await page.getByText("确认执行").waitFor({ state: "visible" });
      if (!api.actionPreviews.some((preview) => preview.action_kind === "todo.update" && preview.normalized_parameters.operation === "reassign")) throw new Error("Todo reassign did not create a typed preview");
      await page.getByRole("button", { name: "关闭", exact: true }).click();
      await taskRow.click();
      taskManagement = page.locator("details.personal-task-management");
      await taskManagement.locator("summary").click();
      await page.getByLabel("优先级", {exact: true}).selectOption("P4");
      await page.screenshot({path: resolve(outputDir, "todo-priority-edit.png"), fullPage: false, animations: "disabled"});
      await taskManagement.locator("label", {has: page.getByLabel("优先级", {exact: true})}).getByRole("button").click();
      await page.getByText("确认执行").waitFor({state: "visible"});
      const priorityEdit = api.actionPreviews.findLast(preview => preview.action_kind === "todo.update" && preview.normalized_parameters.priority === "P4");
      if (!priorityEdit || priorityEdit.normalized_parameters.text !== undefined) throw new Error("Priority edit must be structured, without a text rewrite");
      await page.getByRole("button", {name: "关闭", exact: true}).click();
      await taskRow.click();
      taskManagement = page.locator("details.personal-task-management");
      await taskManagement.locator("summary").click();
      await page.getByLabel("Todo 暂缓恢复条件").fill("pr_merged:huangruiteng/loopx#3399");
      await page.screenshot({ path: resolve(outputDir, "todo-defer-resume-condition.png"), fullPage: false, animations: "disabled" });
      await taskManagement.locator(".personal-inline-resume-when").getByRole("button", { name: "检查暂缓" }).click();
      await page.getByText("确认执行").waitFor({ state: "visible" });
      const explicitDefer = api.actionPreviews.findLast((preview) => preview.action_kind === "todo.update" && preview.normalized_parameters.operation === "defer");
      if (explicitDefer?.normalized_parameters.resume_when !== "pr_merged:huangruiteng/loopx#3399") throw new Error(`Todo defer did not preserve its supported resume condition: ${JSON.stringify(explicitDefer)}`);
      if (JSON.stringify(api.actionPreviews).includes("owner_resume")) throw new Error("Personal Workspace emitted the unsupported owner_resume sentinel");
      await page.getByRole("button", { name: "关闭", exact: true }).click();
      for (const [label, actionKind, operation, managementAction] of [
        ["标记阻塞", "todo.update", "block", true],
        ["标记完成", "todo.update", "complete", false],
        ["创建后续 Todo", "todo.create", null, true],
      ]) {
        await taskRow.click();
        if (managementAction) await page.locator("details.personal-task-management").locator("summary").click();
        await page.getByRole("button", { name: label, exact: true }).click();
        await page.getByText("确认执行").waitFor({ state: "visible" });
        if (!api.actionPreviews.some((preview) => preview.action_kind === actionKind && (operation === null || preview.normalized_parameters.operation === operation))) throw new Error(`Todo ${label} did not create the expected typed preview`);
        await page.getByRole("button", { name: "关闭", exact: true }).click();
      }
      api.nextActionPreviewDelayMs = 900;
      const quickComplete = taskCards.first().getByRole("button", { name: /^标记完成：/u });
      const quickPreviewCount = api.actionPreviews.length;
      await quickComplete.click();
      await page.waitForFunction(
        () => document.querySelector('button[aria-label^="标记完成："]')?.getAttribute("aria-busy") === "true",
        null,
        { timeout: 600 },
      );
      if (!(await quickComplete.isDisabled())) throw new Error("Quick Todo completion remained clickable while preview creation was pending");
      await page.getByText(/^正在准备确认预览：/u).waitFor({ state: "visible", timeout: 600 });
      await page.getByText("确认执行").waitFor({ state: "visible", timeout: 2_000 });
      if (api.actionPreviews.length !== quickPreviewCount + 1) throw new Error("Quick Todo completion did not create exactly one typed preview");
      const quickPreview = api.actionPreviews.at(-1);
      if (quickPreview?.action_kind !== "todo.update" || quickPreview.normalized_parameters.operation !== "complete") throw new Error(`Quick Todo completion created the wrong typed preview: ${JSON.stringify(quickPreview)}`);
      await page.getByRole("button", { name: "关闭", exact: true }).click();
      api.failNextActionPreview = true;
      api.nextActionPreviewDelayMs = 300;
      await quickComplete.click();
      await page.getByText(/^无法准备确认预览：/u).waitFor({ state: "visible", timeout: 1_000 });
      if (await quickComplete.isDisabled()) throw new Error("Quick Todo completion stayed disabled after a preview failure");
      if (api.actionPreviews.length !== quickPreviewCount + 1) throw new Error("A rejected quick completion preview was recorded as ready");
      await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Chat|对话)$/ }).click();
      await page.getByRole("dialog").filter({ hasText: "确认执行" }).waitFor({ state: "hidden" });

      const writesBeforeMonitorShortcut = api.durableWriteCount;
      if (await page.locator(".personal-composer-tools").getAttribute("open") === null) await page.locator(".personal-composer-tools > summary").click();
      await page.getByRole("button", { name: "配置定时检查" }).click();
      await page.getByRole("dialog", {name: "配置定时检查", exact: true}).getByRole("button", {name: "检查配置"}).click();
      await page.getByText("确认执行").waitFor({ state: "visible" });
      const monitorShortcut = api.actionPreviews.findLast((preview) => preview.action_kind === "monitor.create");
      if (monitorShortcut?.normalized_parameters.cadence !== "2h") throw new Error(`定时检查快捷方式频率漂移：${JSON.stringify(monitorShortcut?.normalized_parameters)}`);
      if (monitorShortcut?.normalized_parameters.stop_condition !== "goal_complete") throw new Error(`定时检查快捷方式停止条件漂移：${JSON.stringify(monitorShortcut?.normalized_parameters)}`);
      if (monitorShortcut?.normalized_parameters.target !== "检查当前 Goal 的阻塞、进度与新产出") throw new Error(`定时检查快捷方式未回落到本地化默认检查目标：${JSON.stringify(monitorShortcut?.normalized_parameters)}`);
      if (api.durableWriteCount !== writesBeforeMonitorShortcut) throw new Error("定时检查快捷方式在确认前写入了持久状态");
      await page.getByRole("button", { name: "关闭", exact: true }).click();
      await page.getByRole("button", {name: "配置定时检查"}).click();
      const monitorForm = page.getByRole("dialog", {name: "配置定时检查", exact: true});
      const previewsBeforeUnsupportedSchedule = api.actionPreviews.length;
      await monitorForm.getByLabel("检查间隔", {exact: true}).fill("0");
      await monitorForm.getByRole("button", {name: "检查配置"}).click();
      if (api.actionPreviews.length !== previewsBeforeUnsupportedSchedule) throw new Error("Invalid interval created a preview");
      await monitorForm.getByLabel("检查间隔", {exact: true}).fill("2");
      await monitorForm.getByLabel("检查内容", {exact: true}).fill("复盘是否包含已完成、阻塞、下周计划");
      await monitorForm.getByRole("button", {name: "检查配置"}).click();
      await page.getByText("确认执行").waitFor({ state: "visible" });
      const monitorCreate = api.actionPreviews.findLast((preview) => preview.action_kind === "monitor.create");
      if (!monitorCreate) throw new Error("Bounded monitor configuration did not map to monitor.create");
      if (monitorCreate.normalized_parameters.cadence !== "2h") throw new Error(`Monitor cadence drifted: ${JSON.stringify(monitorCreate.normalized_parameters)}`);
      if (monitorCreate.normalized_parameters.target !== "复盘是否包含已完成、阻塞、下周计划") throw new Error(`Monitor target drifted: ${JSON.stringify(monitorCreate.normalized_parameters)}`);
      await page.getByRole("button", { name: "关闭", exact: true }).click();

      await goalNavigation.getByRole("button", { name: /^(Chat|对话)$/ }).click();
      const schedule = page.locator(".personal-schedule-row").first();
      if (!await schedule.isVisible()) await page.locator(".personal-activity-summary > summary").click();
      for (const [label, operation] of [["立即运行", "run_now"], ["暂停", "pause"], ["改为每 2 小时", "edit"], ["停止定时检查", "stop"]]) {
        await schedule.click();
        await page.getByText("定时检查", { exact: true }).last().waitFor({ state: "visible" });
        await page.getByRole("button", { name: label, exact: true }).click();
        await page.getByText("确认执行").waitFor({ state: "visible" });
        const monitorUpdate = api.actionPreviews.find((preview) => preview.action_kind === "monitor.update" && preview.normalized_parameters.operation === operation);
        if (!monitorUpdate) throw new Error(`Monitor ${operation} did not map to monitor.update`);
        if (operation === "pause") {
          const writesBeforeApply = api.durableWriteCount;
          await page.getByRole("button", { name: "确认并应用", exact: true }).click();
          await page.getByText("执行结果", { exact: true }).waitFor({ state: "visible" });
          await page.getByText("已应用，LoopX 状态将刷新。").waitFor({ state: "visible" });
          if (api.durableWriteCount !== writesBeforeApply + 1) throw new Error("Monitor confirmation did not produce exactly one durable write");
          if (!api.actionApplies.includes(monitorUpdate.proposalId)) throw new Error("Monitor confirmation did not apply the previewed proposal");
          api.nextStatusDelayMs = 1_600;
          await page.getByRole("button", { name: "查看更新后的 Goal", exact: true }).click();
          await page.getByRole("dialog").filter({ hasText: "执行结果" }).waitFor({ state: "hidden", timeout: 600 });
          await goalNavigation.getByRole("button", { name: /^(Tasks|任务)$/, current: "page" }).waitFor({ state: "visible", timeout: 600 });
          await goalNavigation.getByRole("button", { name: /^(Chat|对话)$/ }).click();
        } else {
          await page.getByRole("button", { name: "关闭", exact: true }).click();
        }
      }
      pass(10, "Explicit Heartbeat and monitor controls preview, gate, apply and read back typed schedules.");

      const agentSelect = page.getByRole("combobox", { name: "选择聊天 Runtime" });
      await agentSelect.click();
      const agentListbox = page.getByRole("listbox", { name: "选择聊天 Runtime" });
      const unavailableAgent = agentListbox.getByRole("option", { name: /Offline Agent · 不可用/ });
      if ((await unavailableAgent.count()) !== 1) throw new Error(`Unavailable Agent missing; options=${await agentListbox.getByRole("option").allTextContents()}`);
      const unavailableDisabled = await unavailableAgent.isDisabled();
      const unavailableLabel = await unavailableAgent.textContent();
      if (!unavailableDisabled || !unavailableLabel?.includes("不可用")) {
        throw new Error(`Unavailable Agent is selectable or lacks explanation; disabled=${unavailableDisabled}; label=${unavailableLabel}`);
      }
      await page.screenshot({ path: resolve(outputDir, "agent-select-open.png"), fullPage: false, animations: "disabled" });
      await page.keyboard.press("ArrowDown");
      const focusedAgentOption = await page.locator(":focus").textContent();
      if (!focusedAgentOption?.includes("Claude Code")) throw new Error(`Agent keyboard navigation did not advance: ${focusedAgentOption}`);
      await page.keyboard.press("Escape");
      if (await agentListbox.isVisible().catch(() => false)) throw new Error("Agent menu did not close on Escape");
      if (!(await agentSelect.evaluate((element) => element === document.activeElement))) throw new Error("Agent menu did not restore trigger focus");
      pass(14, "With no declared steward executor the shipped Codex default held, and the unavailable Agent option was disabled with explanation.");
      await agentSelect.click();
      const reopenedAgentListbox = page.getByRole("listbox", { name: "选择聊天 Runtime" });
      await reopenedAgentListbox.getByRole("option", { name: "Claude Code", exact: true }).click();
      if ((await agentSelect.getAttribute("data-value")) !== "claude-code") throw new Error("Healthy Agent selection did not update");
      await page.getByRole("button", { name: "刷新状态" }).click();

      // Activity groups are sorted by presentation, not executor ownership.
      // Correct the selected runtime's run; historical task Sessions keep theirs.
      const currentRuntimeRun = page.locator(".personal-run-row").filter({has: page.locator(".personal-row-copy > small", {hasText: /^Claude Code$/})}).first();
      await currentRuntimeRun.waitFor({state: "attached"});
      if (!await currentRuntimeRun.isVisible()) await page.locator(".personal-activity-summary > summary").click();
      await currentRuntimeRun.click();
      await page.getByRole("tab", { name: "详情与操作" }).click();
      const runningCorrection = page.getByLabel("输入纠偏信息");
      await runningCorrection.fill("保持运行，等我检查中断控制。 ");
      await page.getByRole("button", { name: "发送纠偏" }).click();
      await page.getByText("更多运行操作").click();
      const interruptButton = page.getByRole("button", { name: "中断本次运行" });
      try {
        await interruptButton.waitFor({ state: "visible", timeout: 8_000 });
      } catch (error) {
        await page.screenshot({ path: resolve(outputDir, "interrupt-state-failed.png"), fullPage: true, animations: "disabled" });
        throw new Error(`${error.message}; body=${(await page.locator("body").innerText()).slice(-4000)}`);
      }
      await interruptButton.click();
      const secondCorrection = api.turnRequests.find((turn) => turn.message.includes("中断控制"));
      if (!secondCorrection || secondCorrection.sessionId === firstCorrection.sessionId) {
        throw new Error("Agent change reused the earlier Agent Session or failed to start the second correction");
      }
      if (!api.interrupts.some((turn) => turn.sessionId === secondCorrection.sessionId && turn.turnId === secondCorrection.turnId)) {
        throw new Error("Interrupt did not target the active Session and Turn");
      }
      await page.getByRole("button", { name: /关闭详情/ }).click();
      await page.locator('[data-goal-panel="chat"]').getByText("已中断。你可以在当前会话继续发送消息。", { exact: true }).waitFor({ state: "visible", timeout: 10_000 });

      if (!await currentRuntimeRun.isVisible()) await page.locator(".personal-activity-summary > summary").click();
      await currentRuntimeRun.click();
      const rowHandle = currentRuntimeRun;
      await page.getByRole("button", { name: /关闭详情/ }).press("Escape");
      await rowHandle.waitFor({ state: "visible" });
      await page.waitForFunction(
        () => document.activeElement?.classList.contains("personal-run-row"),
        null,
        { timeout: 2_000 },
      );
      if (!(await rowHandle.evaluate((element) => element === document.activeElement))) throw new Error("Drawer Escape did not restore focus to the selected row");

      await page.getByRole("button", { name: /LoopX 管家/ }).first().click();
      const needsYouCard = page.getByTestId("personal-home-lane-needs_you").locator(".personal-home-goal-card").first();
      const needsYouSource = await needsYouCard.locator("strong").innerText();
      const needsYouAction = await needsYouCard.locator("p").innerText();
      await needsYouCard.click();
      await page.getByRole("heading", { name: needsYouSource }).waitFor({ state: "visible" }).catch(() => {});
      await page.getByText(needsYouAction, { exact: true }).first().waitFor({ state: "visible" });
      await page.locator(".personal-object-list").first().getByRole("button").first().click();
      await page.getByText("需要你", { exact: true }).last().waitFor({ state: "visible" });
      await page.getByText("更多决定").click();
      await page.getByRole("button", { name: "稍后决定", exact: true }).click();
      await page.getByText("确认执行").waitFor({ state: "visible" });
      const deferredDecision = api.actionPreviews.find((preview) => preview.action_kind === "gate.resolve" && preview.normalized_parameters.decision === "defer");
      if (!deferredDecision) throw new Error("Decision defer did not create a Gate preview");
      await page.getByRole("button", { name: "稍后", exact: true }).click();
      await page.getByText(/已暂缓/).waitFor({ state: "visible" });
      if (!api.actionTransitions.some((transition) => transition.transition === "defer")) throw new Error("Proposal defer transition was not sent");
      await page.getByRole("button", { name: "关闭", exact: true }).click();
      await page.locator(".personal-manager-link").first().click();
      const sourceGoalCard = page.locator(".personal-home-goal-card").first();
      await sourceGoalCard.click();
      await goalNavigation.getByRole("button", { name: /^(Chat|对话)$/ }).click();
      if (!(await page.locator(".personal-run-row").count())) throw new Error("Source Goal did not expose its execution row after direct navigation");
      pass(3, "Needs-you and running cards navigate directly to their source Goal and expose typed details.");

      const visibleText = await page.locator("body").innerText();
      if (/session-goal-|turn-\d{6,}|\/Users\/|credential|provider payload|tool output/u.test(visibleText)) {
        fail(12, "Default surface exposes a raw runtime identifier, path, credential, or provider/tool payload.");
      } else {
        pass(12, "Default surface kept raw runtime ids, paths, credentials, and provider/tool payloads hidden.");
      }
      pass(13, "Manager cards retain Goal source lineage and Goal views retain Agent, schedule, and execution lineage.");

      if (failures.length) throw new Error(failures.join(" | "));
    } finally {
      await context.close();
    }
    return {
      coverageEntries: context.coverageEntries,
      note: notes.join(" "),
    };
  },
};
