import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

export const performanceDiagnosisScenario = {
  id: "performance-diagnosis",
  async run({ browser, collectCoverage, url }) {
    const context = await openWorkspacePage(browser, url, { collectCoverage });
    const { page } = context;
    const marker = "private-profile-fixture-not-for-transmission";
    const transmitted = [];
    page.on("request", request => {
      if ((request.postData() ?? "").includes(marker) || request.url().includes(marker)) transmitted.push(request.url());
    });
    try {
      await page.getByRole("button", { name: "设置", exact: true }).click();
      await page.locator(".personal-settings-tabs").getByRole("button", { name: "能力中心", exact: true }).click();
      const panel = page.getByTestId("performance-diagnosis");
      await panel.locator("summary").first().click();
      const input = panel.locator('input[type="file"]');
      const capture = {
        $schema: "https://www.speedscope.app/file-format-schema.json",
        shared: { frames: [{ name: marker, file: null, line: null }, { name: "readSnapshot", file: "fixture.ts", line: 7 }] },
        profiles: [
          { type: "sampled", name: "main thread", unit: "milliseconds", startValue: 0, endValue: 100, samples: [[0, 1]], weights: [100] },
          { type: "evented", name: "worker thread", unit: "milliseconds", startValue: 0, endValue: 150,
            events: [{ type: "O", at: 0, frame: 0 }, { type: "C", at: 150, frame: 0 }] },
        ],
      };
      await input.setInputFiles({ name: "capture.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(capture)) });
      await panel.getByRole("status").filter({ hasText: "已解析 2 份独立采样" }).waitFor();
      if (!await panel.getByText("main thread · 100.00 ms", { exact: true }).count()
        || !await panel.getByText("worker thread · 150.00 ms", { exact: true }).count()) throw new Error("Independent capture weights changed");
      await panel.getByLabel("热点排序").selectOption("inclusive");
      if (!await panel.locator("tbody").first().getByText(marker, { exact: true }).count()) throw new Error("Inclusive-only ancestor disappeared");
      await panel.evaluate(element => { element.scrollTop = 0; });
      await page.screenshot({ path: resolve(outputDir, "performance-diagnosis-desktop.png"), animations: "disabled" });
      await page.setViewportSize({ width: 390, height: 844 });
      await panel.locator("summary").first().focus();
      await panel.evaluate(element => { element.scrollTop = 0; });
      await page.screenshot({ path: resolve(outputDir, "performance-diagnosis-mobile.png"), animations: "disabled" });
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw new Error("Diagnostics overflow mobile viewport");
      await input.setInputFiles({ name: "broken.json", mimeType: "application/json", buffer: Buffer.from('{"profiles":[]}') });
      await panel.getByRole("alert").waitFor();
      if (await panel.getByRole("table").count()) throw new Error("Failed capture retained the previous success");
      const events = [];
      for (let i = 0; i < 1000; i++) events.push({type: "O", frame: 0, at: i});
      for (let i = 0; i < 2000; i++) events.push({type: i % 2 ? "C" : "O", frame: 0, at: 1000 + i});
      for (let i = 0; i < 1000; i++) events.push({type: "C", frame: 0, at: 3000 + i});
      const costly = {...capture, profiles: [{type: "evented", unit: "milliseconds", startValue: 0, endValue: 4000, events}]};
      await input.setInputFiles({name: "deep.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(costly))});
      await panel.getByRole("alert").filter({hasText: "analysis work limit"}).waitFor();
      if (await panel.getByRole("table").count()) throw new Error("Expensive capture retained partial success");
      const amplified = { ...capture, shared: { frames: [{ name: "x".repeat(8192) }] },
        profiles: Array.from({ length: 65 }, () => ({ type: "sampled", name: "thread", unit: "milliseconds", startValue: 0, endValue: 1, samples: [[0]], weights: [1] })) };
      await input.setInputFiles({ name: "many-threads.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(amplified)) });
      await panel.getByRole("alert").filter({ hasText: "summary text exceeds" }).waitFor();
      if (await panel.getByRole("table").count()) throw new Error("Rejected summary was silently truncated");
      await input.setInputFiles({ name: "too-large.json", mimeType: "application/json", buffer: Buffer.alloc(16 * 1024 * 1024 + 1, 32) });
      await panel.getByRole("alert").filter({ hasText: "文件超过 16 MiB" }).waitFor();
      const clear = panel.getByRole("button", { name: "清除 / 取消", exact: true });
      await clear.focus(); await page.keyboard.press("Enter");
      if (await panel.getByRole("alert").count() || await panel.getByRole("status").count() || await input.inputValue()) throw new Error("Clear did not discard local evidence");
      if (transmitted.length) throw new Error("A local capture escaped into a request");
      await page.setViewportSize({ width: 1512, height: 982 });
      await page.getByRole("button", { name: "返回工作区", exact: true }).click();
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.reload({ waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      await page.locator(".personal-settings-tabs").getByRole("button", { name: "Capability Center", exact: true }).click();
      await panel.locator("summary").first().click();
      await panel.getByLabel("Choose a local profile (up to 16 MiB)").setInputFiles({ name: "capture.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(capture)) });
      await panel.getByRole("status").filter({ hasText: "Inspected 2 independent profiles" }).waitFor();
      await panel.getByRole("button", { name: "Clear / cancel", exact: true }).click();
      if (await panel.getByRole("table").count()) throw new Error("English clear retained observations");
      if (context.errors.length) throw new Error(context.errors.join(" | "));
      return { coverageEntries: await context.close(), note: "Packaged local worker shares CLI parser; independent profiles and inclusive ancestors preserved; invalid/oversized captures clear results; keyboard clear and narrow layout; raw capture never transmitted." };
    } catch (error) { await context.close(); throw error; }
  },
};
