#!/usr/bin/env node
// Isolated browser acceptance scenarios for the personal Agent workspace.

import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

import {
  launchBrowser,
  loadPlaywright,
  waitForHttp,
} from "./dashboard-browser-smoke-support.mjs";
import { writeDashboardBrowserCoverage } from "./dashboard-browser-coverage.mjs";
import { conversationActivityScenario } from "./personal-workspace-browser/conversation-activity.mjs";
import { chatRecoveryScenario } from "./personal-workspace-browser/chat-recovery.mjs";
import { executionChipScenario } from "./personal-workspace-browser/execution-chip.mjs";
import {
  collectCoverage,
  dashboardDir,
  outputDir,
  packaged,
  port,
  repoRoot,
  startServer,
} from "./personal-workspace-browser/fixture.mjs";
import { navigationSortingScenario } from "./personal-workspace-browser/navigation-sorting.mjs";
import { automationCadenceScenario } from "./personal-workspace-browser/automation-cadence.mjs";
import { teamEvidenceScenario } from "./personal-workspace-browser/team-evidence.mjs";
import { managedGoalResultsScenario } from "./personal-workspace-browser/managed-goal-results.mjs";
import { loopxModeScenario } from "./personal-workspace-browser/loopx-mode.mjs";
import { progressiveLoadingScenario } from "./personal-workspace-browser/progressive-loading.mjs";
import { stewardJourneyScenario } from "./personal-workspace-browser/steward-journey.mjs";
import { teamPlanScenario } from "./personal-workspace-browser/team-plan.mjs";
import { typedActionsScenario } from "./personal-workspace-browser/typed-actions.mjs";
import { stewardModelSettingsScenario } from "./personal-workspace-browser/steward-model-settings.mjs";
import { workspaceLocaleScenario } from "./personal-workspace-browser/workspace-locale.mjs";
import { answerPresentationScenario } from "./personal-workspace-browser/answer-presentation.mjs";

import { conversationInputScenario } from "./personal-workspace-browser/conversation-input.mjs";
import { goalActivityScenario } from "./personal-workspace-browser/goal-activity.mjs";

const scenarioCatalog = [conversationInputScenario, goalActivityScenario, conversationActivityScenario, navigationSortingScenario, automationCadenceScenario, chatRecoveryScenario, answerPresentationScenario, loopxModeScenario, teamEvidenceScenario, managedGoalResultsScenario, typedActionsScenario, teamPlanScenario, stewardJourneyScenario, executionChipScenario, stewardModelSettingsScenario, progressiveLoadingScenario, workspaceLocaleScenario];
const requestedScenario = process.env.LOOPX_PERSONAL_WORKSPACE_SCENARIO;
const scenarios = requestedScenario
  ? scenarioCatalog.filter((scenario) => scenario.id === requestedScenario)
  : scenarioCatalog;

async function main() {
  if (collectCoverage && packaged) {
    throw new Error("Source coverage requires the development smoke with source maps");
  }
  if (scenarios.length === 0) {
    throw new Error(
      `Unknown personal workspace scenario ${requestedScenario}; expected one of ${scenarioCatalog.map((scenario) => scenario.id).join(", ")}`,
    );
  }
  await mkdir(outputDir, { recursive: true });
  const server = startServer();
  let browser;
  const results = {};
  const coverageEntries = [];
  try {
    const url = `http://127.0.0.1:${port}/${packaged ? "chat/" : ""}?statusUrl=/status.json`;
    await waitForHttp(url);
    browser = await launchBrowser(loadPlaywright().chromium);
    for (const scenario of scenarios) {
      const startedAt = Date.now();
      try {
        // Existing scenarios assert Chinese copy; the locale scenario exercises
        // browser preferences explicitly and receives the unmodified browser.
        const scenarioBrowser = scenario.id === "workspace-locale"
          ? browser
          : { newPage: (options = {}) => browser.newPage({ locale: "zh-CN", ...options }) };
        const result = await scenario.run({ browser: scenarioBrowser, collectCoverage, url });
        coverageEntries.push(...result.coverageEntries);
        results[scenario.id] = {
          status: "PASS",
          note: result.note,
          duration_ms: Date.now() - startedAt,
        };
      } catch (error) {
        results[scenario.id] = {
          status: "FAIL",
          note: error instanceof Error ? error.message : String(error),
          duration_ms: Date.now() - startedAt,
        };
        throw error;
      } finally {
        await writeFile(
          resolve(outputDir, "acceptance-results.json"),
          `${JSON.stringify({ scenarios: results }, null, 2)}\n`,
          "utf8",
        );
      }
    }
    if (collectCoverage) {
      await writeDashboardBrowserCoverage(coverageEntries, {
        repoRoot,
        dashboardDir,
        outputDir: resolve(repoRoot, "coverage/dashboard"),
      });
    }
    console.log(
      `personal-workspace-browser-smoke (${packaged ? "packaged" : "development"}): ok\n`
      + `preview=${url}\n`
      + `scenarios=${scenarios.map((scenario) => scenario.id).join(",")}`,
    );
  } finally {
    await browser?.close();
    server.kill("SIGTERM");
  }
}

await main();
