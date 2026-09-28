// Frontend-first steward journey acceptance.
//
// The existing scenarios cover one surface each. This one walks the owner
// journey through the workspace in order: the first screen, asking the steward
// for work, the admitted team plan card, and confirming it. Beats that the
// product does not yet prove from these surfaces are recorded as typed gaps
// with the probe that looked for them, so the journey reports current truth
// instead of asserting a happy path the workspace cannot show.
//
// Everything here is synthetic: the fixture substitutes the agent turn, and no
// live workspace, Goal, agent or local path is read or captured.

import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

import { outputDir, packaged, repoRoot } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

const GOAL_ID = "product-release";
const GOAL_TITLE = "Product Release";
const PROPOSAL_ID = "proposal-steward-journey-fixture";
const PLAN_SUMMARY = "为 product-release 分配 2 项任务";
const READY_TODO = "Implement the bounded intake";
const GAP_TODO = "Independently review the intake";
const STEWARD_PROMPT = "找下一步";

function teamPlanProposal() {
  return {
    schema_version: "loopx_chat_action_proposal_v1",
    proposal_id: PROPOSAL_ID,
    action_kind: "team.plan",
    summary: PLAN_SUMMARY,
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
    expected_state_fingerprint: "fixture-steward-journey",
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
 * Probes that look for a surface the journey needs but the workspace may not
 * expose yet. A probe records what it searched for and what it found, so a gap
 * stays evidence-bearing instead of becoming a claim about the whole product.
 */
async function probe(page, { beat, need, selectors = [], phrases = [] }) {
  const foundSelectors = [];
  for (const selector of selectors) {
    if (await page.locator(selector).count() > 0) foundSelectors.push(selector);
  }
  const bodyText = await page.locator("body").innerText();
  const foundPhrases = phrases.filter((phrase) => bodyText.includes(phrase));
  return {
    beat,
    need,
    status: foundSelectors.length > 0 || foundPhrases.length > 0 ? "present" : "gap",
    probe: { selectors, phrases },
    observed: { matched_selectors: foundSelectors, matched_phrases: foundPhrases },
  };
}

export const stewardJourneyScenario = {
  id: "steward-journey",
  async run({ browser, collectCoverage, url }) {
    const failures = [];
    const beats = [];
    const gaps = [];
    // The lane's acceptance is that this journey is reproducible from the
    // *packaged* frontend, not only from the development server. Recording the
    // mode keeps that claim checkable instead of implied by whoever ran it, and
    // the bundle assertion catches a packaged run that silently served source.
    const servedMode = packaged ? "packaged" : "development";
    if (packaged) {
      if (!url.includes("/chat/")) {
        failures.push(`packaged run did not serve the built bundle (${url})`);
      }
    }
    const record = (beat, detail) => beats.push({ beat, detail });
    const check = (condition, message) => {
      if (!condition) failures.push(message);
    };
    const context = await openWorkspacePage(browser, url, {
      apiOptions: { initialActionProposals: [teamPlanProposal()] },
      collectCoverage,
    });
    try {
      const { api, page } = context;

      // Beat 1: the first screen the owner sees before asking for anything.
      await page.locator(".personal-home-board").waitFor({ state: "visible", timeout: 15_000 });
      const homeText = await page.locator(".personal-home-board").innerText();
      const laneCounts = {};
      for (const lane of ["needs_you", "running", "observing", "scheduled"]) {
        const section = page.locator(`[data-testid="personal-home-lane-${lane}"]`);
        laneCounts[lane] = await section.count() ? await section.locator(".personal-home-goal-card").count() : 0;
      }
      const stewardCard = page.locator(".personal-home-goal-card", { hasText: GOAL_TITLE });
      check(await stewardCard.count() >= 1, "the registered steward Goal is visible on the first screen");
      check(
        homeText.includes("需要你") || homeText.includes("执行中"),
        "the first screen separates what needs the owner from what is running",
      );
      record("1-first-screen", {
        goal_visible: await stewardCard.count() >= 1,
        lane_counts: laneCounts,
        card_text: await stewardCard.first().innerText().catch(() => ""),
        screenshot: "steward-journey-1-first-screen.png",
      });
      await page.screenshot({
        path: resolve(outputDir, "steward-journey-1-first-screen.png"),
        fullPage: false,
        animations: "disabled",
      });

      // Beat 2: ask the steward for work in the Goal conversation and keep the
      // answer and the plan card in the conversation the owner asked in.
      await stewardCard.first().click();
      const goalNavigation = page.getByRole("navigation", { name: "Goal 视图" });
      await goalNavigation.getByRole("button", { name: /^(Chat|对话)$/ }).click();
      // Suggestions remain one disclosure away, without occupying the first
      // screen. They still send into this persistent conversation.
      await page.locator(".personal-composer-tools > summary").click();
      const composer = page.getByLabel("向 LoopX 发送消息");
      const promptRow = page.locator(".personal-quick-prompts");
      await promptRow.first().waitFor({ state: "visible", timeout: 15_000 });
      const promptLabels = (await promptRow.first().locator("button").allInnerTexts())
        .map((label) => label.replace(/\s+/g, " ").trim())
        .filter(Boolean);
      const expectedPromptLabels = [
        "询问下一步",
        "向 Agent 获取进度报告",
        "配置定时检查",
        "看阻塞",
        "查证据",
      ];
      const missingPromptLabels = expectedPromptLabels.filter(
        (label) => !promptLabels.some((observed) => observed.includes(label)),
      );
      check(
        missingPromptLabels.length === 0,
        `the shipped quick-prompt row exposes the steward prompt set (missing: ${missingPromptLabels.join(" / ") || "none"}; observed: ${promptLabels.join(" / ") || "none"})`,
      );
      const gatePrompt = "当前 Goal 有哪些 Gate 或阻塞？哪些需要我决定？";
      const gateChip = promptRow.first().getByRole("button", { name: "看阻塞" });
      const gateChipPresent = (await gateChip.count()) > 0;
      check(gateChipPresent, "the 看阻塞 steward chip is rendered in the quick-prompt row");
      let chipTurn;
      if (gateChipPresent) {
        await gateChip.click();
        for (let attempt = 0; attempt < 40 && !chipTurn; attempt += 1) {
          chipTurn = api.turnRequests.find((request) => request.message === gatePrompt);
          if (!chipTurn) await page.waitForTimeout(50);
        }
      }
      check(Boolean(chipTurn), "clicking the 看阻塞 chip posts its message as an accepted Turn");
      const composerAfterChip = await composer.inputValue();
      check(
        composerAfterChip === "",
        `a chip click sends immediately and leaves no draft behind (composer: ${JSON.stringify(composerAfterChip)})`,
      );
      record("2-steward-prompts", {
        prompt_labels: promptLabels,
        gate_prompt_sent: Boolean(chipTurn),
        composer_after_click: composerAfterChip,
      });
      await composer.fill(`${STEWARD_PROMPT}：请给我一份当前 Goal 的下一步。`);
      await page.getByRole("button", { name: "发送", exact: true }).click();
      let turn;
      for (let attempt = 0; attempt < 40 && !turn; attempt += 1) {
        turn = api.turnRequests.find((request) => request.message.includes(STEWARD_PROMPT));
        if (!turn) await page.waitForTimeout(50);
      }
      check(Boolean(turn), "the steward prompt reaches the Goal conversation as an accepted Turn");
      const row = page.locator('.personal-proposal-row.is-ready[data-action-kind="team.plan"]')
        .filter({ hasText: GOAL_ID });
      await row.waitFor({ state: "visible", timeout: 15_000 });
      await row.click();
      const drawer = page.locator('.personal-context-drawer[data-context-kind="proposal"]');
      await drawer.getByText("配额包络", { exact: true }).waitFor({ state: "visible", timeout: 15_000 });
      const previewText = await drawer.innerText();
      check(previewText.includes("agent-backend"), "a ready lane names the Agent that runs it");
      check(
        previewText.includes(`P1 · implement · ${READY_TODO}`),
        "a ready lane shows its first bounded Todo with priority and action kind",
      );
      check(
        previewText.includes("待安排") && previewText.includes(GAP_TODO),
        "a gap lane says it is unstaffed and keeps the work it did not staff",
      );
      check(
        previewText.includes("确认后分配可安排的任务"),
        "the card states that confirmation assigns available tasks",
      );
      record("2-ask-and-plan-card", {
        accepted_turn: turn?.turnId ?? null,
        plan_summary: PLAN_SUMMARY,
        ready_lane: "agent-backend",
        gap_lane_reason: "agent_not_registered",
        asked_from: "composer",
        screenshot: "steward-journey-2-plan-card.png",
      });
      await page.screenshot({
        path: resolve(outputDir, "steward-journey-2-plan-card.png"),
        fullPage: false,
        animations: "disabled",
      });

      // Beat 3: confirm, and record what the workspace actually reports after
      // the canonical owner ran.
      const confirm = drawer.getByRole("button", { name: "确认分配", exact: true });
      check(await confirm.count() === 1, "exactly one confirmation control is offered");
      await confirm.click();
      for (let attempt = 0; attempt < 60 && api.actionApplies.length === 0; attempt += 1) {
        await page.waitForTimeout(50);
      }
      check(
        api.actionApplies.filter((proposalId) => proposalId === PROPOSAL_ID).length === 1,
        "confirming sends exactly one apply for the confirmed proposal",
      );
      check(api.durableWriteCount === 1, "the confirmed apply performed exactly one durable write");
      const applied = drawer.getByRole("heading", { name: "已分配 1 项，1 项待安排", exact: true });
      await applied.waitFor({ state: "visible", timeout: 15_000 });
      const resultText = await drawer.locator(".personal-team-plan-result").innerText();
      const assignmentVisible = resultText.includes("agent-backend") && resultText.includes(READY_TODO);
      const gapVisible = resultText.includes("agent-reviewer") && resultText.includes(GAP_TODO)
        && resultText.includes("待安排 · 尚未加入此目标");
      check(assignmentVisible && gapVisible, "the result names assigned work and pending work with its reason");
      check(await confirm.count() === 0, "the completed result removes its confirmation control");
      record("3-confirm", {
        applies: api.actionApplies.length,
        durable_writes: api.durableWriteCount,
        outcome_text: await applied.innerText(),
        outcome_fidelity: "assigned task and pending task with reason; execution remains unverified",
      });
      gaps.push({
        beat: "3-confirm",
        need: "partial assignment result names assigned and pending work with a reason",
        status: assignmentVisible && gapVisible ? "present" : "gap",
        probe: { selectors: [".personal-team-plan-result"], phrases: [READY_TODO, GAP_TODO, "尚未加入此目标"] },
        observed: {
          matched_phrases: [READY_TODO, GAP_TODO, "尚未加入此目标"].filter((phrase) => resultText.includes(phrase)),
          surface_text: resultText,
        },
        owner_hint: "steward assignment result; receiver adoption and execution require separate evidence",
      });
      await page.screenshot({
        path: resolve(outputDir, "steward-journey-3-confirmed.png"),
        fullPage: false,
        animations: "disabled",
      });

      // Beats 4-7: record what these surfaces do and do not yet prove.
      gaps.push(await probe(page, {
        beat: "4-readiness",
        need: "a per-lane readiness ladder (registered -> bound -> launchable -> executing)",
        selectors: ["[data-lane-readiness]", "[data-testid='personal-lane-readiness']"],
        phrases: ["可启动", "已绑定", "未绑定执行器"],
      }));
      gaps.push(await probe(page, {
        beat: "5-correction",
        need: "correcting a confirmed lane commitment (pause / supersede) from the same conversation",
        selectors: ["[data-testid='personal-lane-correction']", "[data-lane-correction]"],
        phrases: ["撤销该 lane", "暂停 lane"],
      }));
      gaps.push(await probe(page, {
        beat: "6-recovery",
        need: "a failed lane naming its blocker owner and next step on the workspace",
        selectors: ["[data-testid='personal-lane-blocker']", "[data-lane-blocker-owner]"],
        phrases: ["该 lane 阻塞", "负责重新派发"],
      }));
      gaps.push(await probe(page, {
        beat: "7-return",
        need: "completion judged by the lane's returned result, not by the conversation",
        selectors: ["[data-testid='personal-lane-return']", "[data-lane-return-receipt]"],
        phrases: ["按回执完成", "交付回执已核验"],
      }));

      const scriptErrors = context.errors.filter((message) => !message.startsWith("Failed to load resource"));
      check(scriptErrors.length === 0, `no client-side exception was raised (${scriptErrors.join(" | ")})`);

      await writeFile(
        resolve(outputDir, "steward-journey-report.json"),
        `${JSON.stringify(
          {
            beats,
            gaps,
            mode: servedMode,
            served_root: packaged ? "loopx/web/chat" : "vite-development-server",
            url,
            scenario: "steward-journey",
          },
          null,
          2,
        )}\n`,
        "utf8",
      );
      const gapBeats = gaps.filter((entry) => entry.status === "gap").map((entry) => entry.beat);
      console.log(
        `steward-journey mode=${servedMode} beats=${beats.length} gaps=${gapBeats.join(",") || "none"}`,
      );

      // The product case is the owner-facing reading of this run, and it drifted
      // silently once already: a lane delivered the confirm result while the
      // table still called it a gap. The tables are checked here rather than in
      // review because the run is the only thing that knows which gaps are open.
      for (const [caseDoc, gapHeading] of [
        ["docs/product/use-cases/steward/README.md", "Recorded Gaps And Owners"],
        ["docs/product/use-cases/steward/README.zh-CN.md", "已记录缺口与归属"],
      ]) {
        const text = await readFile(resolve(repoRoot, caseDoc), "utf8");
        const headingAt = text.indexOf(`## ${gapHeading}`);
        check(headingAt >= 0, `${caseDoc} keeps its gap section heading`);
        const section = headingAt < 0 ? "" : text.slice(headingAt).split(/\n## /)[0];
        const gapRows = section.split("\n").filter((line) => /^\| \d+ \| /.test(line));
        check(
          gapRows.length === gapBeats.length,
          `${caseDoc} lists ${gapRows.length} gaps but this run recorded ${gapBeats.length} (${gapBeats.join(", ") || "none"})`,
        );
        check(
          !text.includes("the only outcome sentence is the generic applied notice")
            && !text.includes("只有一条通用的“已应用”提示"),
          `${caseDoc} still keeps the confirm-result gap this run proves present`,
        );
      }
    } finally {
      await context.close();
    }
    if (failures.length) throw new Error(failures.join(" | "));
    return {
      coverageEntries: context.coverageEntries,
      note: `frontend journey beats recorded; unproven beats reported as gaps (${gaps.filter((entry) => entry.status === "gap").map((entry) => entry.beat).join(", ") || "none"})`,
    };
  },
};
