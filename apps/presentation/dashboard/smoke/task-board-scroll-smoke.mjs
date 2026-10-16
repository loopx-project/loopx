import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const dashboardDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(dashboardDir, "../../..");
const port = Number(process.env.LOOPX_TASK_BOARD_SCROLL_PORT ?? "5199");

const source = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const tasks = source("../src/features/personal-workspace/goal-tasks-view.tsx");
const styles = source("../src/features/personal-workspace/personal-workspace.css");

assert.match(
  tasks,
  /className=\{`personal-task-lane-scroll[\s\S]*role="region"[\s\S]*tabIndex=\{count > 0 \? 0 : -1\}/,
  "Task lanes expose independently keyboard-scrollable regions",
);
assert.match(
  tasks,
  /current\.after === next\.after && current\.before === next\.before \? current : next/,
  "Overflow measurement preserves state identity when a cue boundary did not change",
);
assert.match(
  tasks,
  /resizeObserverRef\.current = observer[\s\S]*\}, \[listView, syncOverflow\]\);/,
  "The lane ResizeObserver lifecycle stays stable across card renders",
);
assert.match(
  styles,
  /\.personal-channel-scroll\[data-active-goal-view="tasks"\]:has\(\.personal-task-board\) \{ overflow: hidden; \}/,
  "Desktop Tasks keeps the page chrome fixed while lanes scroll",
);
assert.match(
  styles,
  /\.personal-task-lane-scroll \{[^}]*overflow-y: auto;[^}]*overscroll-behavior-y: contain;[^}]*scrollbar-gutter: stable;/,
  "Task lanes own stable visible scrolling without chaining into the page",
);
assert.match(
  styles,
  /\.personal-task-lane-scroll:focus-visible \{[^}]*outline:/,
  "Keyboard users receive a visible focus indicator on a scrollable lane",
);
assert.match(
  styles,
  /\.personal-task-lane-scroll\.has-overflow-after \{[^}]*box-shadow: inset/,
  "Overflowing lanes show a visual continuation cue",
);
assert.match(
  styles,
  /@media \(max-width: 720px\)[\s\S]*\.personal-channel-scroll\[data-active-goal-view="tasks"\]:has\(\.personal-task-board\) \{ overflow-y: auto; \}[\s\S]*\.personal-task-lane-scroll,[^}]*\{[^}]*overflow: visible;/,
  "Narrow screens fall back to one page scroller instead of nested scroll traps",
);
assert.match(
  styles,
  /\[data-pw-theme="brutal"\] \.personal-task-kanban \.personal-task-lane-scroll > button[^}]*border: 2px solid #141414;/,
  "The brutal theme follows direct task buttons into the lane scroller",
);

function loadPlaywright() {
  try {
    return require("playwright");
  } catch {
    return require(resolve(homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright"));
  }
}

async function waitForHttp(url) {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      // Vite has not started accepting requests yet.
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 200));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

function statusFixture() {
  const fixture = structuredClone(require(resolve(repoRoot, "examples/status.example.json")));
  const attention = fixture.attention_queue.items.find((item) => item.goal_id === "loopx-meta");
  assert.ok(attention, "Expected the public loopx-meta fixture");
  const goal = fixture.run_history.goals.find((item) => item.id === "loopx-meta");
  assert.ok(goal, "Expected the public loopx-meta run-history fixture");
  goal.display_name = "Task board scroll fixture";
  attention.project_asset ??= {};
  attention.project_asset.owner ??= "codex-side-bypass";
  attention.project_asset.gate ??= "none";
  attention.project_asset.next_action ??= "Validate the task board interaction";
  attention.project_asset.stop_condition ??= "The task board interaction is verified";
  attention.project_asset.agent_todos = {
    advancement_done_count: 0,
    done: 0,
    open: 18,
    total: 18,
    items: Array.from({ length: 18 }, (_, index) => ({
      claimed_by: "codex-side-bypass",
      done: false,
      goal_id: "loopx-meta",
      index,
      priority: index < 2 ? "P0" : "P1",
      role: "agent",
      status: "open",
      task_class: "advancement_task",
      text: `Public task board scroll proof ${index + 1}`,
      title: `Public task board scroll proof ${index + 1}`,
      todo_id: `todo-task-board-${index + 1}`,
    })),
    recent_completed_advancement_items: [],
  };
  attention.agent_todos = {
    done_count: 0,
    open_count: 18,
    source_section: "Agent Todo",
    total_count: 18,
    items: attention.project_asset.agent_todos.items,
  };
  return fixture;
}

async function main() {
  const { chromium } = loadPlaywright();
  const viteBin = resolve(dashboardDir, "node_modules/vite/bin/vite.js");
  const server = spawn(process.execPath, [viteBin, "--host", "127.0.0.1", "--port", String(port), "--strictPort", "--force"], {
    cwd: dashboardDir,
    env: { ...process.env },
    stdio: "ignore",
  });
  let browser;
  try {
    const url = `http://127.0.0.1:${port}/?statusUrl=/status.json`;
    await waitForHttp(url);
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ locale: "zh-CN", viewport: { width: 1512, height: 760 } });
    const fixture = statusFixture();
    await page.route(`http://127.0.0.1:${port}/status.json**`, (route) => route.fulfill({ contentType: "application/json", json: fixture, status: 200 }));
    await page.route("**/api/**", (route) => route.fulfill({ contentType: "application/json", json: { ok: true }, status: 200 }));
    await page.goto(url, { waitUntil: "networkidle" });
    await page.locator(".personal-goal-link").filter({ hasText: "Task board scroll fixture" }).click();
    await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: /^(Tasks|任务)$/ }).click();

    const lane = page.getByRole("region", { name: "待执行 / 进行中" });
    await lane.waitFor({ state: "visible" });
    const initial = await lane.evaluate((element) => ({
      clientHeight: element.clientHeight,
      overflowY: getComputedStyle(element).overflowY,
      pageScrollTop: element.closest(".personal-channel-scroll")?.scrollTop ?? -1,
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
    }));
    assert.equal(initial.overflowY, "auto", "Desktop task lanes must own vertical scrolling");
    assert.ok(initial.scrollHeight > initial.clientHeight, `Expected lane overflow: ${JSON.stringify(initial)}`);
    assert.ok(await lane.evaluate((element) => element.classList.contains("has-overflow-after")), "Overflowing lane must show a continuation cue");

    const shell = page.locator(".personal-workspace-shell");
    await shell.evaluate((element) => { element.dataset.pwTheme = "brutal"; });
    const directTaskButton = page.getByRole("region", { name: "待你确认" }).locator(":scope > button").first();
    await directTaskButton.waitFor({ state: "visible" });
    const brutalRest = await directTaskButton.evaluate((element) => {
      const style = getComputedStyle(element);
      return { borderWidth: style.borderWidth, boxShadow: style.boxShadow };
    });
    assert.equal(brutalRest.borderWidth, "2px", "Brutal task buttons must retain their high-contrast border");
    assert.notEqual(brutalRest.boxShadow, "none", "Brutal task buttons must retain their hard shadow");
    await directTaskButton.hover();
    const brutalHover = await directTaskButton.evaluate((element) => getComputedStyle(element).boxShadow);
    assert.notEqual(brutalHover, brutalRest.boxShadow, "Brutal task buttons must retain their hover feedback");
    await shell.evaluate((element) => { element.dataset.pwTheme = "paper"; });

    await lane.focus();
    await page.keyboard.press("End");
    await page.waitForTimeout(100);
    const afterKeyboard = await lane.evaluate((element) => ({
      focused: element === document.activeElement,
      scrollTop: element.scrollTop,
    }));
    assert.ok(afterKeyboard.focused, "Keyboard scrolling must retain lane focus");
    assert.ok(afterKeyboard.scrollTop > initial.scrollTop, "End must advance the focused lane");
    assert.ok(await lane.evaluate((element) => element.classList.contains("has-overflow-before")), "A scrolled lane must show the reverse continuation cue");

    await lane.evaluate((element) => { element.scrollTop = 0; });
    const laneBox = await lane.boundingBox();
    assert.ok(laneBox, "Scrollable lane must have visible geometry");
    await page.mouse.move(laneBox.x + laneBox.width / 2, laneBox.y + laneBox.height / 2);
    await page.mouse.wheel(0, 520);
    await page.waitForTimeout(100);
    const afterWheel = await lane.evaluate((element) => ({
      pageScrollTop: element.closest(".personal-channel-scroll")?.scrollTop ?? -1,
      scrollTop: element.scrollTop,
    }));
    assert.ok(afterWheel.scrollTop > 0, "Wheel input must advance the hovered lane");
    assert.equal(afterWheel.pageScrollTop, initial.pageScrollTop, "Lane scrolling must not move the desktop page scroller");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(200);
    const mobile = await lane.evaluate((element) => {
      const pageScroller = element.closest(".personal-channel-scroll");
      return {
        horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        laneOverflow: getComputedStyle(element).overflowY,
        pageCanScroll: Boolean(pageScroller && pageScroller.scrollHeight > pageScroller.clientHeight),
      };
    });
    assert.equal(mobile.laneOverflow, "visible", "Narrow screens must remove nested lane scrolling");
    assert.ok(mobile.pageCanScroll, "Narrow screens must retain one page-level scroller");
    assert.ok(mobile.horizontalOverflow <= 1, `Narrow task board has ${mobile.horizontalOverflow}px horizontal overflow`);
    console.log("task board scroll smoke passed");
  } finally {
    await browser?.close();
    server.kill("SIGTERM");
  }
}

await main();
