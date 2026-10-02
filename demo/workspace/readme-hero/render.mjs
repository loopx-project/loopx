// Capture the packaged App on a fresh, isolated Workspace stories demo.
// All graph nodes, recorded relations and pending decisions use real demo state.
// No Agent execution, conversation or team record is manufactured for the image.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../../..");
const { chromium } = createRequire(resolve(repo, "apps/presentation/dashboard/package.json"))("playwright");
const { values: args } = parseArgs({ options: {
  url: { type: "string", default: "http://127.0.0.1:8791" },
  "demo-root": { type: "string" },
  out: { type: "string", default: resolve(repo, "output/playwright/readme-hero") },
} });
const goal = "community-day";

async function decisionState() {
  const response = await fetch(new URL("/status.json", args.url));
  assert.ok(response.ok, "The demo status must be readable");
  const status = await response.json();
  assert.ok(status.ok && status.todo_index?.schema_version === "todo_index_v0");
  return status.todo_index.items.map(({ goal_id, todo_id, role, task_class, status, done }) => ({ goal_id, todo_id, role, task_class, status, done }))
    .sort((a, b) => a.todo_id.localeCompare(b.todo_id));
}

// Preview only in the disposable workspace identified by the demo manifest.
// Never resolve the gate or manufacture an approval card with a route mock.
async function prepareDecision() {
  assert.ok(args["demo-root"], "Pass --demo-root for a fresh Workspace stories directory");
  const root = resolve(args["demo-root"]);
  const manifest = JSON.parse(readFileSync(resolve(root, ".workspace-story-demo.json"), "utf8"));
  assert.equal(manifest.schema_version, "workspace_story_demo_v3");
  assert.equal(manifest.root, root);
  const venue = manifest.goals.find((item) => item.id === goal)?.gates.venue;
  assert.ok(venue?.todo_id, "The demo manifest must contain the venue decision");
  const url = new URL(args.url);
  assert.ok(url.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname), "Use the isolated loopback demo server");
  const before = await decisionState();
  const gate = before.find((item) => item.todo_id === venue.todo_id);
  assert.ok(gate?.role === "user" && gate.task_class === "user_gate" && gate.status === "open" && !gate.done, "Use a fresh demo with its venue decision still open");
  const response = await fetch(new URL("/api/actions/preview", url), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      action_kind: "gate.resolve",
      summary: "Choose Riverside Hall: $5400 total, step-free access, 120 seats; hold expires Friday.",
      normalized_parameters: { goal_id: goal, todo_id: venue.todo_id, agent_id: venue.agent, decision: "approve", note: "README illustration; approval stays pending." },
      context: { kind: "goal", goal_id: goal },
      idempotency_key: `readme-hero-venue-${venue.todo_id}`,
    }),
  });
  const reply = await response.json();
  assert.ok(response.ok && reply.ok, `Venue preview failed: ${reply.error ?? response.status}`);
  assert.equal(reply.proposal.status, "preview_ready", "The packaged App must support pending venue approval; do not render from an older or already advanced demo");
  assert.equal(reply.proposal.receipt, null);
  assert.ok(reply.proposal.available_transitions.includes("apply"));
  return before;
}

const locales = {
  "en-US": { file: "workspace-hero.webp", hero: {
    eyebrow: "LoopX Personal Agent Workspace",
    headline: "Delegate long-running work to an Agent team.",
    subline: "See dependencies. Decide in place.",
    chrome: "LoopX · Riverside Community Day",
  } },
  "zh-CN": { file: "workspace-hero.zh-CN.webp", hero: {
    eyebrow: "LoopX 个人 Agent 工作区",
    headline: "把长程工作交给 Agent 团队。",
    subline: "看清依赖，就地决策。",
    chrome: "LoopX · Riverside Community Day",
  } },
};

async function capture(browser, locale, dir) {
  const response = await fetch(new URL(`/api/chat/delivery-review?goal_id=${goal}`, args.url));
  assert.ok(response.ok, "The real delivery review must be readable");
  const { goal_map: map } = await response.json();
  assert.equal(map?.schema_version, "goal_task_map_v0", "Build the App with Work map support before rendering");
  assert.equal(map.mode, "read_only");
  assert.ok(map.limits.topology_complete, "Use a complete demo graph; never hide missing work in the illustration");
  const work = map.nodes.filter(node => node.kind === "deliverable");
  const done = work.filter(node => node.state === "done");
  const context = await browser.newContext({ viewport: { width: 1440, height: 860 }, deviceScaleFactor: 2, locale, serviceWorkers: "block" });
  const page = await context.newPage();
  try {
    const zh = locale.startsWith("zh");
    await page.goto(`${args.url}/chat/?goalId=${goal}&statusUrl=%2Fstatus.json&view=overview`, { waitUntil: "load" });
    await page.getByRole("heading", { name: zh ? "工作地图" : "Work map", exact: true }).waitFor();
    const dismiss = page.getByRole("button", { name: /Dismiss statistics notice|收起统计告知/ });
    if (await dismiss.count()) await dismiss.click();
    await page.waitForFunction(expected => document.querySelector(".work-map-summary")?.textContent.includes(expected), `${done.length}/${work.length}`);
    assert.match(await page.locator(".work-map-summary").innerText(), new RegExp(`${done.length}/${work.length}`));
    assert.equal(await page.locator('.work-map-scroll .work-map-node[data-kind="gate"]').count(), 2);
    await page.locator(".work-map").evaluate(element => element.scrollIntoView({ block: "start" }));
    await page.screenshot({ path: `${dir}/workspace.png`, animations: "disabled" });
    // The decision remains a real pending preview, without clicking apply.
    await page.getByRole("button", { name: zh ? "对话" : "Chat", exact: true }).click();
    const approval = page.getByRole("button", { name: zh ? /确认批准/ : /Confirm approval/ });
    await approval.waitFor({ state: "visible" });
    assert.equal(await approval.count(), 1);
    assert.equal(await page.locator(".personal-message.is-assistant").count(), 0, "No simulated Agent reply belongs in this capture");
  } finally {
    await context.close();
  }
}

async function compose(browser, locale, hero, dir, target) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 940 }, deviceScaleFactor: 2 });
  await page.goto(pathToFileURL(resolve(here, "hero.html")).href);
  await page.evaluate(({ lang, hero, shots }) => {
    document.documentElement.lang = lang;
    for (const node of document.querySelectorAll("[data-copy]")) node.textContent = hero[node.dataset.copy];
    for (const node of document.querySelectorAll("[data-shot]")) node.src = `${shots}/${node.dataset.shot}.png`;
  }, { lang: locale === "zh-CN" ? "zh-CN" : "en", hero, shots: pathToFileURL(dir).href });
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map((image) => image.decode()));
  });
  const png = await page.screenshot({ omitBackground: true });
  writeFileSync(`${dir}/hero.png`, png);
  const webp = await page.evaluate(async (dataUrl) => {
    const image = new Image();
    image.src = dataUrl;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = 1920;
    canvas.height = Math.round((image.height * 1920) / image.width);
    const context = canvas.getContext("2d");
    context.imageSmoothingQuality = "high";
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/webp", 0.86).split(",")[1];
  }, `data:image/png;base64,${png.toString("base64")}`);
  writeFileSync(target, Buffer.from(webp, "base64"));
  await page.close();
}

const before = await prepareDecision();
mkdirSync(args.out, { recursive: true });
const browser = await chromium.launch();
try {
  for (const [locale, { file, hero }] of Object.entries(locales)) {
    const dir = resolve(repo, "output/playwright/readme-hero/frames", locale);
    mkdirSync(dir, { recursive: true });
    await capture(browser, locale, dir);
    await compose(browser, locale, hero, dir, resolve(args.out, file));
    console.log(`${locale}: ${resolve(args.out, file)}`);
  }
  assert.deepEqual(await decisionState(), before, "Rendering must leave all projected Todo and decision states unchanged");
} finally {
  await browser.close();
}
