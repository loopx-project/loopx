import assert from "node:assert/strict";
import {readFile, access} from "node:fs/promises";
import {join, resolve} from "node:path";
import {outputDir} from "./fixture.mjs";

export async function exerciseConfigurationComparison({page, panel, beforeBytes, afterBytes, root}) {
  const before = JSON.parse(beforeBytes), after = JSON.parse(afterBytes);
  const registryBefore = await readFile(join(root, "registry.json"));
  const machineBefore = await readFile(join(root, "runtime/machine/configuration.json"));
  const comparison = () => panel.getByRole("region", {name: "比较配置快照", exact: true});
  const upload = (label, bytes, name) => comparison().getByLabel(label, {exact: true}).setInputFiles({name, mimeType: "application/json", buffer: bytes});
  const fill = async () => {
    await upload("比较前文件", beforeBytes, "before.json");
    await upload("比较后文件", afterBytes, "after.json");
  };
  const compare = async () => {
    await comparison().getByRole("button", {name: "比较两个文件", exact: true}).click();
    await comparison().getByRole("status").filter({hasText: "2 项差异"}).waitFor();
  };
  await fill();
  await upload("比较后文件", Buffer.from("invalid json"), "invalid.json");
  await comparison().getByRole("alert").waitFor();
  assert.equal(await comparison().getByRole("button", {name: "比较两个文件", exact: true}).isDisabled(), true);
  await upload("比较后文件", Buffer.from(JSON.stringify({...after, sha256: "changed"})), "damaged.json");
  await comparison().getByRole("button", {name: "比较两个文件", exact: true}).click();
  await comparison().getByRole("alert").waitFor();
  assert.equal(await comparison().getByRole("button", {name: "核对比较后快照以恢复"}).count(), 0);
  await upload("比较后文件", afterBytes, "after.json");
  const endpoint = "**/api/chat/configuration-backup/compare";
  await page.route(endpoint, route => route.fulfill({status: 503, contentType: "application/json", body: JSON.stringify({ok: false, error: "Synthetic comparison unavailable"})}));
  await comparison().getByRole("button", {name: "比较两个文件", exact: true}).click();
  await comparison().getByRole("alert").waitFor();
  await page.unroute(endpoint);
  const compareButton = comparison().getByRole("button", {name: "比较两个文件", exact: true});
  await compareButton.focus();
  await page.keyboard.press("Enter");
  await comparison().getByRole("status").filter({hasText: "2 项差异"}).waitFor();
  assert.equal(await panel.getByRole("button", {name: "恢复为隔离检查点"}).count(), 0, "Comparison alone must not select a restore candidate");
  await assert.rejects(access(join(root, "runtime/backups/configuration")));
  const hashes = comparison().locator("details").filter({has: page.getByText("散列与备份范围", {exact: true})});
  await hashes.nth(0).locator("summary").click();
  await hashes.nth(1).locator("summary").click();
  await comparison().getByText(before.sha256, {exact: false}).waitFor();
  await comparison().getByText(after.sha256, {exact: false}).waitFor();
  await hashes.nth(1).getByText("仅在此文件: synthetic-next", {exact: true}).waitFor();
  const differences = comparison().getByLabel("配置差异", {exact: true});
  await differences.locator("summary").filter({hasText: "/machine_configuration/namespaces/goal_storage/new_goal_provider"}).click();
  assert.match(await differences.innerText(), /sqlite/);
  assert.match(await differences.innerText(), /file/);
  await comparison().scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "configuration-comparison-desktop.png"), animations: "disabled"});
  await page.setViewportSize({width: 390, height: 844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await comparison().getByRole("status").scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "configuration-comparison-mobile.png"), animations: "disabled"});
  await comparison().getByRole("button", {name: "核对比较后快照以恢复"}).click();
  await panel.getByRole("button", {name: "恢复为隔离检查点"}).waitFor();
  async function assertPreviewFits(sha, name) {
    const status = panel.getByRole("status").last();
    const digest = status.locator("code");
    assert.equal(await digest.textContent(), `SHA-256: ${sha}`, "The complete reviewed digest must remain available");
    await status.scrollIntoViewIfNeeded();
    const geometry = await digest.evaluate(element => {
      const panel = element.closest(".personal-detail-card").getBoundingClientRect();
      const button = element.closest(".personal-detail-card").querySelector(".personal-primary-action").getBoundingClientRect();
      const glyphs = [];
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      for (let text = walker.nextNode(); text; text = walker.nextNode()) {
        for (let index = 0; index < text.textContent.length; index++) {
          const range = document.createRange(); range.setStart(text, index); range.setEnd(text, index + 1);
          for (const rect of range.getClientRects()) glyphs.push({left: rect.left, right: rect.right});
        }
      }
      return {width: innerWidth, panel: {left: panel.left, right: panel.right}, button: {left: button.left, right: button.right}, glyphs};
    });
    console.log(`configuration-preview-${name}=${JSON.stringify({width: geometry.width, panel: geometry.panel, button: geometry.button, glyphCount: geometry.glyphs.length, glyphLeft: Math.min(...geometry.glyphs.map(rect => rect.left)), glyphRight: Math.max(...geometry.glyphs.map(rect => rect.right))})}`);
    await page.screenshot({path: resolve(outputDir, `configuration-preview-${name}-mobile.png`), animations: "disabled"});
    assert.equal(geometry.width, 390);
    assert(geometry.panel.left >= 0 && geometry.panel.right <= geometry.width, "The preview must not widen its clipped settings panel");
    assert(geometry.button.left >= geometry.panel.left && geometry.button.right <= geometry.panel.right, "The restore action must fit the panel");
    assert.equal(geometry.glyphs.length, `SHA-256: ${sha}`.length);
    assert(geometry.glyphs.every(rect => rect.left >= geometry.panel.left && rect.right <= Math.min(geometry.panel.right, geometry.width)), "Every digest glyph must be inside the visible content width");
  }
  await assertPreviewFits(after.sha256, "after");
  await comparison().getByRole("button", {name: "核对比较前快照以恢复"}).click();
  await panel.getByRole("status").last().filter({hasText: before.sha256}).waitFor();
  await assertPreviewFits(before.sha256, "before");
  await upload("比较前文件", beforeBytes, "before-again.json");
  assert.equal(await panel.getByRole("button", {name: "恢复为隔离检查点"}).count(), 0, "Replacing either input invalidates the reviewed restore candidate");

  // Leave the context before a real owner response is delivered; returning to
  // the same scope must not resurrect old files, comparison or recovery review.
  async function remountWhilePending(path, start) {
    let release, entered;
    const gate = new Promise(resolve => {release = resolve;});
    const requested = new Promise(resolve => {entered = resolve;});
    await page.route(path, async route => {entered(); await gate; await route.fallback();});
    await start(); await requested;
    await page.getByRole("radio", {name: "Goal 设置", exact: true}).check();
    await page.getByRole("radio", {name: "本机设置", exact: true}).check();
    await panel.locator(":scope > summary").click();
    const response = page.waitForResponse(response => new URL(response.url()).pathname.endsWith(path.split("/").at(-1)));
    release(); await response;
    await page.unroute(path);
    assert.equal(await comparison().getByRole("status").count(), 0);
    assert.equal(await panel.getByRole("button", {name: "恢复为隔离检查点"}).count(), 0);
    assert.equal(await comparison().getByText("尚未选择", {exact: true}).count(), 2);
  }
  await remountWhilePending(endpoint, () => comparison().getByRole("button", {name: "比较两个文件", exact: true}).click());
  await fill(); await compare();
  await remountWhilePending("**/api/chat/configuration-backup/restore", () => comparison().getByRole("button", {name: "核对比较后快照以恢复"}).click());
  await fill(); await compare();
  await comparison().getByRole("button", {name: "核对比较后快照以恢复"}).click();
  await panel.getByRole("button", {name: "恢复为隔离检查点"}).waitFor();
  await assertPreviewFits(after.sha256, "selected-after");
  await panel.getByRole("button", {name: "恢复为隔离检查点"}).click();
  await panel.getByText("检查点已恢复并核对；当前设置及存储 provider 未改变。", {exact: false}).waitFor();
  assert.deepEqual(JSON.parse(await readFile(join(root, "runtime/backups/configuration", after.sha256, "configuration-backup.json"))), after);
  await page.setViewportSize({width: 1512, height: 982});
  await page.getByRole("button", {name: "语言", exact: true}).click();
  await page.getByRole("radio", {name: "English", exact: true}).check();
  await page.locator(".personal-settings-tabs").getByRole("button", {name: /Capabilit/}).click();
  const englishPanel = page.locator("details").filter({has: page.getByText("Configuration backup and recovery", {exact: true})});
  await englishPanel.locator(":scope > summary").click();
  const english = englishPanel.getByRole("region", {name: "Compare configuration snapshots", exact: true});
  await english.getByLabel("Before snapshot", {exact: true}).setInputFiles({name: "before.json", mimeType: "application/json", buffer: beforeBytes});
  await english.getByLabel("After snapshot", {exact: true}).setInputFiles({name: "after.json", mimeType: "application/json", buffer: afterBytes});
  await english.getByRole("button", {name: "Compare files", exact: true}).click();
  await english.getByRole("status").filter({hasText: "Compared: 2 differences"}).waitFor();
  for (const summary of await english.getByText("Hash and cohort", {exact: true}).all()) await summary.click();
  await english.locator("summary").filter({hasText: "/machine_configuration/namespaces/goal_storage/new_goal_provider"}).click();
  await english.scrollIntoViewIfNeeded();
  await page.screenshot({path: resolve(outputDir, "configuration-comparison-english.png"), animations: "disabled"});
  await page.getByRole("button", {name: "Language", exact: true}).click();
  await page.getByRole("radio", {name: "Simplified Chinese", exact: true}).check();
  await page.locator(".personal-settings-tabs").getByRole("button", {name: "能力中心", exact: true}).click();
  await panel.locator(":scope > summary").click();
  assert.deepEqual(await readFile(join(root, "registry.json")), registryBefore);
  assert.deepEqual(await readFile(join(root, "runtime/machine/configuration.json")), machineBefore);
}
