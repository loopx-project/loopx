import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";

// Packaged UI with synthetic HTTP; Python tests exercise real HTTP/store/native input.
export const conversationDocumentRequestScenario = {
  id: "conversation-document-request",
  async run({browser, url, collectCoverage}) {
    const source = "\ufeff# 需求原文\r\n\r\n    print('a  b')\r\n\r\n" + "Keep the existing scope.\r\n".repeat(25) + "最后约束：不要安装或发布。";
    const bytes = Buffer.from(source, "utf8");
    const coverageEntries = [];
    for (const width of [1512, 390]) {
      const ui = await openWorkspacePage(browser, url, {collectCoverage, viewport: {width, height: 982}});
      const {page} = ui;
      try {
        if (width < 640) await page.locator(".personal-mobile-menu").click();
        await page.locator(".personal-goal-link").first().click();
        await page.getByRole("navigation", {name: "Goal 视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
        const composer = page.getByLabel("向 LoopX 发送消息");
        const sent = [];
        await page.route("**/api/chat/sessions/*/turns", async route => {
          sent.push(route.request().postDataJSON());
          await route.fallback();
        });
        await page.locator('input[type="file"]').setInputFiles({name: "requirements.md", mimeType: "text/markdown", buffer: bytes});
        const pending = page.locator(".personal-composer-images .personal-document-attachment");
        await pending.getByRole("button").click();
        assert.equal(await pending.locator("pre").textContent(), source);
        await composer.fill("按照附件检查当前实现，保留原要求。");
        await page.screenshot({path: resolve(outputDir, `document-preview-${width}.png`), animations: "disabled"});
        await page.getByRole("button", {name: "发送", exact: true}).click();
        const delivered = page.locator(".personal-message.is-user .personal-document-attachment").last();
        await delivered.waitFor();
        assert.equal(sent.length, 1);
        assert.equal(sent[0].attachments[0].data_url, `data:text/markdown;base64,${bytes.toString("base64")}`);
        await delivered.getByRole("button").click();
        assert.equal(await delivered.locator("pre").textContent(), source);
        const downloadPromise = page.waitForEvent("download");
        await delivered.getByRole("link", {name: "下载原文"}).click();
        const download = await downloadPromise;
        assert.equal(download.suggestedFilename(), "requirements.md");
        assert.deepEqual(await readFile(await download.path()), bytes);
        await page.screenshot({path: resolve(outputDir, `document-delivered-${width}.png`), animations: "disabled"});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await page.locator('input[type="file"]').setInputFiles({name: "invalid.txt", mimeType: "text/plain", buffer: Buffer.from([255])});
        await page.locator(".personal-composer-error").waitFor();
        assert.equal(sent.length, 1);
        await page.route("**/api/chat/sessions/*/turns", route => route.fulfill({status: 400, json: {
          ok: false, error: "Text documents require a local managed Codex conversation", turn_replay_safe: true, delivery_state: "not_delivered",
        }}));
        await page.locator('input[type="file"]').setInputFiles({name: "retry.txt", mimeType: "text/plain", buffer: bytes});
        await composer.fill("保留原件再试。");
        await page.getByRole("button", {name: "发送", exact: true}).click();
        await page.locator(".personal-message.is-assistant").last().getByText(/请求未提交；草稿和附件已保留/).waitFor();
        assert.equal(await composer.inputValue(), "保留原件再试。");
        const retry = page.locator(".personal-composer-images .personal-document-attachment");
        await retry.getByRole("button").click();
        assert.equal(await retry.locator("pre").textContent(), source);
        await page.screenshot({path: resolve(outputDir, `document-rejected-${width}.png`), animations: "disabled"});
        assert.equal(sent.length, 1);
      } finally { coverageEntries.push(...await ui.close()); }
    }
    return {coverageEntries, note: "UTF-8 documents: packaged desktop/mobile picker, preview, send, exact-byte download, invalid UTF-8 and rejected draft recovery. Synthetic HTTP."};
  },
};
