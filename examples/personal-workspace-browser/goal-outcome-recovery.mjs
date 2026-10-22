// Packaged outcome recovery against real disposable SQLite/HTTP/CLI authority.
// Refinement and replay use the native owner; final projection cases test UI isolation.
import assert from "node:assert/strict";
import {spawn, execFileSync} from "node:child_process";
import {createServer} from "node:net";
import {createInterface} from "node:readline";
import {mkdtemp, mkdir, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {fileURLToPath} from "node:url";
import {resolve} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {launchBrowser, loadPlaywright, waitForHttp} from "../dashboard-browser-smoke-support.mjs";

const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
const python = resolveTestPython();
const root = await mkdtemp(resolve(tmpdir(), "loopx-outcome-recovery-"));
const environment = {...process.env, LOOPX_USAGE_PING: "0", TMPDIR: root, TEMP: root, TMP: root};
const output = resolve(repoRoot, "output/playwright/goal-outcome-recovery");
const reservation = createServer();
await new Promise(accept => reservation.listen(0, "127.0.0.1", accept));
const port = reservation.address().port;
await new Promise(accept => reservation.close(accept));
const child = spawn(python, ["-u", "examples/personal-workspace-browser/goal-outcome-backend.py", root, String(port), "--without-check-plan",
  ...(process.env.LOOPX_GOAL_OUTCOME_ASSETS ? ["--assets-dir", process.env.LOOPX_GOAL_OUTCOME_ASSETS] : [])],
  {cwd: repoRoot, env: environment, stdio: ["ignore", "pipe", "pipe"]});
let diagnostic = "", browser, page, releaseDelivery;
child.stderr.on("data", chunk => diagnostic = (diagnostic + chunk).slice(-4000));
const lines = createInterface({input: child.stdout});
try {
  const authority = await new Promise((accept, reject) => {
    // Cold native CLI/SQLite fixture setup can exceed 30s; this is a bounded
    // test bootstrap allowance, not an acceptance operation timeout.
    const timer = setTimeout(() => reject(Error(`Outcome fixture did not start: ${diagnostic}`)), 90000);
    child.once("exit", () => {clearTimeout(timer); reject(Error(`Outcome fixture exited: ${diagnostic}`));});
    lines.once("line", line => {clearTimeout(timer); accept(JSON.parse(line));});
  });
  const cli = (...args) => JSON.parse(execFileSync(python, ["-m", "loopx.cli", "--format", "json",
    "--registry", authority.registry, "--runtime-root", authority.runtime, "goal-acceptance", ...args,
    "--goal-id", "product-launch"], {cwd: repoRoot, env: environment, encoding: "utf8"}));
  const outcomeId = "handoff-guide";
  const outcome = () => cli("inspect").goal_acceptance_contract.outcome_requirements.outcomes.find(item => item.id === outcomeId);
  const requests = [], errors = [];
  let readsFail = false, loseReply = false, deliveryFails = false, holdDelivery = false, deliveryStarted;
  let deliverySnapshot, projectedAcceptance, remoteSnapshot;
  let rejectRefinement = false, acceptanceReads = 0;
  const url = `http://127.0.0.1:${port}/chat/`;
  const remoteUrl = new URL("/remote-status.json", url).href;
  await waitForHttp(url);
  browser = await launchBrowser(loadPlaywright().chromium);
  page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  await mkdir(output, {recursive: true});
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(statusUrl => {
    localStorage.setItem("loopx-pw-locale", "en");
    localStorage.setItem("loopx-status-source-catalog-v1", JSON.stringify({schemaVersion: 1,
      sources: [{kind: "ssh_tunnel", label: "Remote snapshot", statusUrl}]}));
  }, remoteUrl);
  await page.route("**/remote-status.json*", route => route.fulfill({json: remoteSnapshot}));
  await page.route("**/api/chat/delivery-review?*", async route => {
    try {
      if (!deliverySnapshot) deliverySnapshot = await (await route.fetch()).json();
      if (holdDelivery) {
        deliveryStarted?.();
        await new Promise(accept => {releaseDelivery = accept;});
      }
      return await route.fulfill({status: deliveryFails ? 503 : 200, json: deliveryFails
        ? {ok: false, error: "Synthetic delivery interruption"} : deliverySnapshot});
    } catch (error) {
      if (!page.isClosed()) throw error;
    }
  });
  await page.route("**/api/chat/goal-acceptance**", async route => {
    if (route.request().method() === "GET") {
      acceptanceReads++;
      if (readsFail) return route.fulfill({status: 503, json: {ok: false, error: "Synthetic read interruption"}});
      if (projectedAcceptance) return route.fulfill({json: projectedAcceptance});
      return route.continue();
    }
    requests.push(route.request().postDataJSON());
    if (rejectRefinement) return route.fulfill({status: 503, json: {ok: false, error: "Synthetic rejected refinement"}});
    const response = await route.fetch();
    if (loseReply) {
      assert.equal(response.status(), 200, "The native write commits before its reply is lost");
      loseReply = false; readsFail = true;
      return route.fulfill({status: 503, json: {ok: false, error: "Synthetic lost reply"}});
    }
    return route.fulfill({response});
  });
  const firstDelivery = page.waitForResponse(response => response.url().includes("/api/chat/delivery-review?"));
  await page.goto(url, {waitUntil: "domcontentloaded"});
  await page.locator(".personal-goal-link").first().click();
  await page.getByRole("button", {name: "Overview", exact: true}).click();
  const outcomes = page.locator(".goal-outcomes");
  const expandAll = async () => {
    const more = outcomes.locator(":scope > .goal-outcomes-more");
    if (await more.getAttribute("aria-expanded") === "false") await more.click();
  };
  await expandAll();
  const openingGroup = outcomes.locator(".goal-outcome-status-group").filter({has: page.getByText("Retain the approved introduction", {exact: true})}).first();
  if (await openingGroup.getAttribute("open") === null) await openingGroup.locator(":scope > summary").click();
  await outcomes.locator(".goal-overview-criteria").getByText("Retain the approved introduction", {exact: true}).waitFor();
  await firstDelivery;
  assert.ok(deliverySnapshot?.acceptance, "Retain the real initial delivery snapshot before advancing the native owner");
  const evidence = page.locator(".goal-overview-evidence");
  assert.equal(await evidence.getAttribute("open"), null, "Secondary evidence starts collapsed without losing its reader");
  await evidence.locator(":scope > summary").focus();
  await page.keyboard.press("Enter");
  await page.locator(".delivery-review-toolbar").waitFor({state: "visible"});
  assert.equal(await page.locator(".delivery-acceptance-contract").count(), 0, "Start on the default review path without automated checks");
  assert.ok(await page.locator(".research-history").count() <= 1, "Overview updates retain one history reader");
  // The native owner can advance beyond the retained delivery snapshot. A
  // transient delivery read must not unmount a newer outcome and its request.
  const newOutcome = {outcome_id: outcomeId, owner_agent_id: "author", objective: "Deliver a useful handoff guide",
    requirements: [{id: "handoff-opening", description: "Retain the handoff introduction"}],
    todo_ids: ["todo_tour"], source: {ref: "fixture:handoff", summary: "An additional supporting outcome"}};
  const newOutcomePath = resolve(root, "handoff.json");
  await writeFile(newOutcomePath, JSON.stringify(newOutcome));
  cli("refine", "--agent-id", "author", "--document", newOutcomePath, "--operation-id", "handoff-guide",
    "--expected-provider-revision", cli("inspect").provider_revision, "--execute");
  const initialCount = outcome().requirements.length;
  assert.equal(deliverySnapshot.acceptance.goal_acceptance_contract.outcome_requirements.outcomes.some(item => item.id === outcomeId), false);
  const original = outcomes.locator(".goal-outcome").filter({has: page.getByRole("heading", {name: "Deliver a clear product tour", exact: true})});
  await original.getByText("Add a requirement", {exact: true}).click();
  await original.getByRole("button", {name: "Refresh result", exact: true}).click();
  const region = outcomes.locator(".goal-outcome").filter({has: page.getByRole("heading", {name: newOutcome.objective, exact: true})});
  await region.waitFor();
  const form = region.locator("form");
  await region.getByText("Add a requirement", {exact: true}).click();
  const text = form.locator("textarea");
  const save = form.getByRole("button", {name: "Retain requirement", exact: true});
  const refresh = form.getByRole("button", {name: "Refresh result", exact: true});
  const rebase = form.getByRole("button", {name: "Rebase on current version", exact: true});
  const waitForSave = async () => page.waitForFunction(button => !button.disabled, await save.elementHandle());
  const refreshDelivery = page.locator(".delivery-review-toolbar").getByRole("button", {name: "Refresh snapshot", exact: true});
  const interruptDelivery = async () => {
    holdDelivery = true; deliveryFails = true;
    const started = new Promise(accept => {deliveryStarted = accept;});
    await refreshDelivery.click(); await started;
    await outcomes.scrollIntoViewIfNeeded();
    await page.screenshot({path: resolve(output, requests.length ? "pending-delivery-loading.png" : "draft-delivery-loading.png"), animations: "disabled"});
    assert.equal(await text.count(), 1, "Delivery loading preserves the newer outcome editor");
    assert.equal(await save.isDisabled(), true, "Loading delivery cannot authorize a write");
    holdDelivery = false; releaseDelivery();
    await page.locator(".delivery-review > [role=alert]").first().waitFor();
    assert.equal(await text.count(), 1, "A failed delivery read preserves the newer outcome editor");
    assert.equal(await save.isDisabled(), true);
  };
  const recoverDelivery = async () => {
    deliveryFails = false;
    await refreshDelivery.click();
    await page.waitForFunction(() => !document.querySelector(".delivery-review-toolbar button")?.disabled);
  };
  const recovered = async () => {
    readsFail = false;
    await refresh.click();
    await save.waitFor({state: "visible"});
    await waitForSave();
  };
  const draft = "Keep the conclusion understandable without prior context";
  await text.fill(draft);
  await interruptDelivery();
  assert.equal(await text.inputValue(), draft, "Unsubmitted draft survives delivery loading and error");
  assert.equal(requests.length, 0);
  await recoverDelivery();
  readsFail = true;
  await refresh.click();
  await page.locator(".delivery-acceptance-contract [role=alert]").waitFor();
  assert.equal(await text.inputValue(), draft, "Unsubmitted draft survives a failed refetch");
  assert.equal(await save.isDisabled(), true, "An unavailable basis cannot authorize a write");
  assert.equal(requests.length, 0);
  await recovered();
  loseReply = true;
  await save.focus(); await page.keyboard.press("Enter");
  await region.getByRole("status").filter({hasText: "Result unconfirmed"}).waitFor();
  await page.locator(".delivery-acceptance-contract [role=alert]").waitFor();
  assert.equal(await text.inputValue(), draft);
  assert.equal(await text.isDisabled(), true, "Pending text belongs to the original request");
  assert.equal(await rebase.isDisabled(), true, "Failed reads cannot replace the original operation identity");
  assert.equal(outcome().requirements.length, initialCount + 1, "Exactly one real native refinement committed");
  assert.equal(requests.length, 1);
  await interruptDelivery();
  assert.equal(await text.inputValue(), draft, "Pending text survives a failed independent delivery read");
  assert.equal(await text.isDisabled(), true);
  assert.equal(await rebase.isDisabled(), true);
  assert.equal(requests.length, 1, "Delivery recovery never resubmits the original request");
  await page.screenshot({path: resolve(output, "read-failure-desktop.png"), animations: "disabled"});
  await page.setViewportSize({width: 390, height: 844});
  await form.scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(output, "read-failure-narrow.png"), animations: "disabled"});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await recoverDelivery();
  await recovered();
  assert.equal(await text.inputValue(), draft);
  assert.equal(await text.isDisabled(), true);
  await save.click();
  await region.getByRole("status").filter({hasText: "Requirement retained alongside earlier requirements"}).waitFor();
  assert.equal(await text.inputValue(), "");
  assert.equal(await text.isDisabled(), false);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[1], requests[0], "Retry preserves the entire original operation and requirement identity");
  assert.equal(outcome().requirements.length, initialCount + 1, "Replay cannot duplicate the requirement");
  // A genuine concurrent native write invalidates the UI basis. Only explicit
  // rebase changes the operation; the user's pending requirement stays stable.
  await text.fill("Keep the closing summary concise");
  const external = {outcome_id: outcomeId, requirements: [{id: "concurrent-requirement", description: "Retain a readable title"}],
    source: {ref: "fixture:concurrent-author", summary: "An independent author refinement"}};
  const path = resolve(root, "concurrent.json");
  await writeFile(path, JSON.stringify(external));
  cli("refine", "--agent-id", "author", "--document", path, "--operation-id", "concurrent-author",
    "--expected-provider-revision", cli("inspect").provider_revision, "--execute");
  await save.click();
  await region.getByRole("status").filter({hasText: "Result unconfirmed"}).waitFor();
  await waitForSave();
  const rejected = requests.at(-1);
  assert.equal(await text.inputValue(), "Keep the closing summary concise");
  await rebase.click(); await save.click();
  await region.getByRole("status").filter({hasText: "Requirement retained alongside earlier requirements"}).waitFor();
  const rebased = requests.at(-1);
  assert.notEqual(rebased.operation_id, rejected.operation_id);
  assert.notEqual(rebased.expected_provider_revision, rejected.expected_provider_revision);
  assert.deepEqual(rebased.refinement, rejected.refinement);
  assert.equal(outcome().requirements.length, initialCount + 3);

  // These final cases exercise source/lifetime projection handling only. Their
  // writes are refused before the native owner; the native state above remains
  // the independent oracle for successful writes, replay and concurrency.
  const native = cli("inspect");
  projectedAcceptance = {ok: true, goal_id: "product-launch", provider_revision: native.provider_revision,
    goal_acceptance_contract: {...native.goal_acceptance_contract, lifecycle_state: "active",
      goal_ref: {goal_id: "product-launch", goal_instance_id: "instance-a"}}};
  await refresh.click();
  await text.waitFor({state: "hidden"});
  await expandAll();
  await region.getByText("Add a requirement", {exact: true}).click();
  await text.fill("This pending requirement belongs to the original lifetime");
  rejectRefinement = true;
  await save.click();
  await region.getByRole("status").filter({hasText: "Result unconfirmed"}).waitFor();
  assert.equal(await text.isDisabled(), true);
  // Optional check configuration has its own unsaved document. The same
  // delivery gate must preserve it without enabling a write or losing the
  // pending Outcome request alongside it. This remains a projection-only case.
  projectedAcceptance = {...projectedAcceptance, goal_acceptance_contract: {...projectedAcceptance.goal_acceptance_contract,
    outcome_id: outcomeId, revision: 1, digest: "c".repeat(64), objective: "Check the handoff introduction",
    criteria: [{id: "handoff-opening", description: "Retain the handoff introduction"}],
    status: "unverified", non_goals: [], tasks: [], held_todo_ids: [], verification: null}};
  await refresh.click();
  const inspection = page.locator(".delivery-check-inspection");
  await inspection.waitFor();
  if (await inspection.getAttribute("open") === null) await inspection.locator(":scope > summary").click();
  const configuration = inspection.locator(".delivery-check-advanced");
  await configuration.locator(":scope > summary").click();
  const configurationDraft = {outcome_id: outcomeId, criteria: [
    {id: "handoff-opening", validation_argv: ["python3", "check-tour.py", "opening"]}]};
  await configuration.locator("input[type=file]").setInputFiles({name: "checks-draft.json", mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(configurationDraft))});
  const configurationPreview = configuration.locator("pre");
  const saveConfiguration = configuration.getByRole("button", {name: "Save configuration", exact: true});
  await configurationPreview.waitFor();
  const writesBeforeConfiguration = requests.length;
  const retainedConfiguration = async (state, disabled) => {
    assert.equal(await configurationPreview.count(), 1, `${state}: keep the imported check configuration`);
    assert.deepEqual(JSON.parse(await configurationPreview.innerText()), configurationDraft);
    assert.ok((await configurationPreview.locator("..").locator(":scope > summary").innerText()).includes("checks-draft.json"));
    assert.equal(await saveConfiguration.isDisabled(), disabled, `${state}: configuration writes require a current basis`);
    assert.equal(requests.length, writesBeforeConfiguration, "Configuration recovery is read-only");
  };
  holdDelivery = true; deliveryFails = true;
  const configurationReadStarted = new Promise(accept => {deliveryStarted = accept;});
  await refreshDelivery.click(); await configurationReadStarted;
  await page.waitForFunction(button => button.disabled, await saveConfiguration.elementHandle());
  await retainedConfiguration("delivery loading", true);
  holdDelivery = false; releaseDelivery();
  await page.locator(".delivery-review > [role=alert]").first().waitFor();
  await retainedConfiguration("delivery error", true);
  await recoverDelivery();
  await page.waitForFunction(button => !button.disabled, await saveConfiguration.elementHandle());
  await retainedConfiguration("delivery recovered", false);
  assert.equal(await text.inputValue(), "This pending requirement belongs to the original lifetime");
  assert.equal(await text.isDisabled(), true);
  const writesBeforeLifetimeChange = requests.length;
  projectedAcceptance = {...projectedAcceptance, goal_acceptance_contract: {...projectedAcceptance.goal_acceptance_contract,
    goal_ref: {goal_id: "product-launch", goal_instance_id: "instance-b"}}};
  await refresh.click();
  await text.waitFor({state: "hidden"});
  await expandAll();
  await region.getByText("Add a requirement", {exact: true}).click();
  assert.equal(await text.inputValue(), "", "A reused Goal/outcome id cannot inherit another lifetime's pending request");
  assert.equal(await rebase.count(), 0, "The old request cannot be rebased into the successor lifetime");
  assert.equal(await configurationPreview.count(), 0, "A successor lifetime cannot inherit an imported check configuration");
  await text.fill("A draft in the selected lifetime");
  projectedAcceptance = {...projectedAcceptance, goal_acceptance_contract: {...projectedAcceptance.goal_acceptance_contract,
    outcome_requirements: {...projectedAcceptance.goal_acceptance_contract.outcome_requirements, current_lifetime: false}}};
  await refresh.click();
  await form.waitFor({state: "detached"});
  assert.equal(await region.getByRole("button", {name: "Retain requirement", exact: true}).count(), 0);
  assert.equal(requests.length, writesBeforeLifetimeChange, "Lifetime changes perform no mutation or automatic rebase");
  assert.equal(outcome().requirements.length, initialCount + 3);

  remoteSnapshot = await (await page.request.get(new URL("/status.json", url).href)).json();
  const remoteGoal = remoteSnapshot.run_history.goals.find(goal => goal.id === "product-launch");
  remoteGoal.display_name = "Remote handoff workspace";
  remoteGoal.acceptance_observation.goal_acceptance_contract = {outcome_requirements: {
    ...native.goal_acceptance_contract.outcome_requirements,
    outcomes: [{...outcome(), objective: "Read the remote handoff", requirements: [{
      ...outcome().requirements[0], description: "Only this remote source declaration"}], sources: [], work: []}],
  }};
  await page.setViewportSize({width: 1440, height: 1000});
  const readsBeforeSourceChange = acceptanceReads;
  await page.getByLabel("Select control plane source", {exact: true}).click();
  await page.getByRole("option", {name: "Remote snapshot", exact: true}).click();
  await page.getByRole("heading", {name: "Remote handoff workspace", exact: true}).waitFor();
  await expandAll();
  const remoteGroup = outcomes.locator(".goal-outcome-status-group").first();
  if (await remoteGroup.getAttribute("open") === null) await remoteGroup.locator(":scope > summary").click();
  await outcomes.getByText("Only this remote source declaration", {exact: true}).waitFor();
  assert.equal(await outcomes.getByText(newOutcome.objective, {exact: true}).count(), 0,
    "The same Goal id in a remote source cannot consume the local acceptance cache");
  assert.equal(await outcomes.locator("form").count(), 0, "Remote declarations expose no local refinement action");
  assert.equal(acceptanceReads, readsBeforeSourceChange, "Opening a remote source does not request local acceptance");
  assert.equal(requests.length, writesBeforeLifetimeChange);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({status: "PASS", boundary: "Packaged UI with real disposable SQLite/HTTP/CLI; synthetic transport and source/lifetime projections, no model or installed App",
    checks: ["newer native outcome/older delivery snapshot", "draft and pending request/delivery loading and error", "unsubmitted draft/read failure", "committed write/lost reply/failed reconciliation", "stale basis disables rebase", "same-operation replay without duplication", "native concurrent write/explicit rebase", "imported check configuration/delivery loading and error", "synthetic lifetime change clears pending and imported configuration", "remote same-id source/cache/write isolation", "390px", "keyboard"]}));
} catch (error) {
  console.error(error);
  if (diagnostic) console.error(diagnostic);
  if (page && !page.isClosed()) await page.screenshot({path: resolve(output, "failure.png"), animations: "disabled"});
  throw error;
} finally {
  releaseDelivery?.();
  await page?.close(); await browser?.close();
  lines.close(); child.kill("SIGTERM");
  await new Promise(accept => {if (child.exitCode !== null) accept(); else child.once("exit", accept);});
  execFileSync(python, ["-c", "from loopx.control_plane.effect_runtime import restart_effect_runtime; restart_effect_runtime(wait_for_exit=True)"],
    {cwd: repoRoot, env: environment, timeout: 15000, stdio: "pipe"});
  await rm(root, {recursive: true, force: true});
}
