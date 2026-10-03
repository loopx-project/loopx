import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {resolve} from "node:path";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {outputDir, repoRoot} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";

// Packaged composer/ordering evidence. Canonical operation fixtures come from
// the real store; model responses are synthetic. HTTP+Codex image transport is
// independently exercised by tests/test_chat_image_http.py.
export const conversationImageRequestScenario = {
  id: "conversation-image-request",
  async run({browser, url}) {
    const fixtures = JSON.parse(execFileSync(resolveTestPython(), [
      resolve(repoRoot, "examples/personal-workspace-browser/confirmed-operation-fixtures.py"),
    ], {cwd: repoRoot, env: {...process.env, PYTHONPATH: repoRoot}, encoding: "utf8"}));
    const oldCards = [fixtures.prepared, fixtures.reconciled].map((proposal, index) => ({
      ...proposal, proposal_id: `older-operation-${index}`,
      created_at: "2026-08-12T01:00:00Z", updated_at: "2027-01-01T00:00:00Z",
      normalized_parameters: {...proposal.normalized_parameters,
        projection: {...proposal.normalized_parameters.projection, title: `Earlier operation ${index + 1}`}},
    }));
    for (const width of [1512, 390]) {
      const ui = await openWorkspacePage(browser, url, {viewport: {width, height: 982},
        apiOptions: {initialActionProposals: oldCards}});
      const {page, api} = ui;
      const sent = [];
      try {
        await page.getByRole("navigation", {name: "管家视图"}).getByRole("button", {name: /^(Chat|对话)$/}).click();
        await page.route("**/api/chat/sessions/*/turns", async route => {
          sent.push(route.request().postDataJSON());
          await route.fallback();
        });
        const png = await page.evaluate(() => {
          const canvas = document.createElement("canvas");
          canvas.width = 640; canvas.height = 320;
          const ctx = canvas.getContext("2d");
          const pixels = ctx.createImageData(640, 320);
          let seed = 3;
          for (let i = 0; i < pixels.data.length; i += 4) {
            seed = (seed * 1664525 + 1013904223) >>> 0;
            pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = 245 + seed % 11;
            pixels.data[i + 3] = 255;
          }
          ctx.putImageData(pixels, 0, 0);
          ctx.fillStyle = "#171717"; ctx.font = "bold 32px sans-serif";
          ctx.fillText("Community contributors: 100", 30, 55);
          for (let i = 0; i < 12; i++) {
            ctx.beginPath(); ctx.arc(60 + i % 6 * 95, 115 + Math.floor(i / 6) * 80, 25, 0, Math.PI * 2);
            ctx.fillStyle = ["#888", "#507a94", "#a58a68"][i % 3]; ctx.fill();
          }
          return canvas.toDataURL("image/png").split(",")[1];
        });
        const bytes = Buffer.from(png, "base64");
        assert.ok(bytes.length > 64_000, "Use a normal screenshot larger than the old HTTP ceiling");
        await page.locator('input[type="file"]').setInputFiles({name: "contributors.png", mimeType: "image/png", buffer: bytes});
        const composer = page.getByLabel("向 LoopX 发送消息");
        const request = "看看截图，帮我写一份社区感谢草稿。";
        await composer.fill(request);
        await page.getByRole("button", {name: "发送", exact: true}).click();
        await page.locator(".personal-message-pending").waitFor({state: "hidden"});
        await page.waitForFunction(() => document.querySelectorAll(".personal-message.is-user").length > 0);
        assert.equal(sent.length, 1);
        assert.equal(sent[0].message, request);
        assert.equal(sent[0].attachments[0].size, bytes.length);
        assert.equal(sent[0].attachments[0].data_url, `data:image/png;base64,${png}`);
        const order = await page.locator(".personal-channel-timeline").evaluate(element => [...element.children].map(child => ({
          text: child.textContent, card: child.classList.contains("personal-proposal-row"), user: child.classList.contains("is-user"),
        })));
        const userIndex = order.findIndex(row => row.user && row.text.includes("社区感谢草稿"));
        assert.ok(userIndex >= 0 && order.every((row, index) => !row.card || index < userIndex),
          `Previously stored operations must not be appended after the new conversation: ${JSON.stringify(order)}`);
        const answer = page.locator(".personal-message.is-assistant").last();
        await answer.waitFor({state: "visible"});
        const box = await answer.boundingBox();
        assert.ok(box && box.y >= 0 && box.y + box.height <= 982, "Sending keeps the current reply in view");
        assert.equal(api.durableWriteCount, 0, "Reading operations and sending a chat message cannot execute an operation");
        await page.screenshot({path: resolve(outputDir, `conversation-image-${width}.png`), animations: "disabled"});
        // A confirmed pre-admission rejection retains the exact draft/image.
        await page.route("**/api/chat/sessions/*/turns", route => route.fulfill({status: 400, json: {
          ok: false, error: "Synthetic invalid attachment", turn_replay_safe: true, delivery_state: "not_delivered",
        }}));
        await page.locator('input[type="file"]').setInputFiles({name: "retry.png", mimeType: "image/png", buffer: bytes});
        await composer.fill("保留这张图，修改后再发。");
        await page.getByRole("button", {name: "发送", exact: true}).click();
        await page.locator(".personal-message.is-assistant").last().getByText(/请求未提交；草稿和图片已保留/).waitFor();
        assert.equal(await composer.inputValue(), "保留这张图，修改后再发。");
        assert.equal(await page.locator(".personal-composer-images img").count(), 1);
        assert.equal(sent.length, 1, "Rejected request must not replay itself");
      } finally {await ui.close();}
    }
    return {coverageEntries: [], note: "Packaged image send preserves bytes; old canonical operation cards precede new messages; rejected requests retain draft/images without replay. Desktop/mobile."};
  },
};
