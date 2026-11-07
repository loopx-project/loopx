import assert from "node:assert/strict";
import { resolve } from "node:path";

// Exercise the shipped provider and Settings UI against the shared synthetic API.
export async function verifyWorkspaceLocales({ browser, url, installApi, outputDir }) {
  const cases = [
    { name: "fresh English", locale: "en-US", expected: "en" },
    { name: "fresh Chinese", locale: "zh-CN", expected: "zh-CN" },
    { name: "Chinese region", locale: "zh-TW", expected: "zh-CN" },
    { name: "unsupported language", locale: "fr-FR", expected: "en" },
    { name: "ordered preferences", languages: ["fr-FR", "en-GB", "zh-CN"], expected: "en" },
    { name: "secondary Chinese", languages: ["fr-FR", "zh-CN", "en"], expected: "zh-CN" },
    { name: "language fallback", languages: [], locale: "zh-CN", expected: "zh-CN" },
    { name: "missing language", languages: [], language: "", expected: "en" },
    { name: "saved English", stored: "en", locale: "zh-CN", expected: "en" },
    { name: "saved Chinese", stored: "zh-CN", locale: "en-US", expected: "zh-CN" },
    { name: "invalid preference", stored: "invalid", locale: "en-US", expected: "en" },
    { name: "locale storage failure English", blocked: true, locale: "en-US", expected: "en" },
    { name: "locale storage failure Chinese", blocked: true, locale: "zh-CN", expected: "zh-CN" },
  ];
  for (const scenario of cases) {
    const page = await browser.newPage({ locale: scenario.locale ?? "en-US" });
    try {
      await installApi(page);
      await page.addInitScript(({ stored, blocked, languages, language }) => {
        if (stored !== undefined) localStorage.setItem("loopx-pw-locale", stored);
        if (languages !== undefined) Object.defineProperty(navigator, "languages", { value: languages });
        if (language !== undefined) Object.defineProperty(navigator, "language", { value: language });
        if (blocked) {
          const getItem = Storage.prototype.getItem;
          const setItem = Storage.prototype.setItem;
          Storage.prototype.getItem = function (key) {
            if (key === "loopx-pw-locale") throw new DOMException("Storage unavailable", "SecurityError");
            return getItem.call(this, key);
          };
          Storage.prototype.setItem = function (key, value) {
            if (key === "loopx-pw-locale") throw new DOMException("Storage unavailable", "SecurityError");
            return setItem.call(this, key, value);
          };
        }
      }, scenario);
      await page.goto(url, { waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor();
      assert.equal(await page.locator("html").getAttribute("lang"), scenario.expected, scenario.name);
      const label = scenario.expected === "en" ? "Settings" : "设置";
      assert.ok(await page.getByRole("button", { name: label, exact: true }).isVisible(), scenario.name);
      if (!scenario.blocked) {
        assert.equal(await page.evaluate(() => localStorage.getItem("loopx-pw-locale")), scenario.stored ?? null,
          `${scenario.name}: detection must not overwrite a saved choice`);
      }
      if (scenario.blocked) {
        await page.getByRole("button", { name: label, exact: true }).click();
        await page.getByRole("button", { name: /Language|语言/ }).click();
        const next = scenario.expected === "en" ? "zh-CN" : "en";
        await page.getByRole("radio", { name: next === "en" ? /English/ : /Simplified Chinese/ }).click();
        assert.equal(await page.locator("html").getAttribute("lang"), next,
          "A locale write failure still permits changing the current session");
      }
      console.log(`workspace locale: ${scenario.name}: ok`);
    } finally {
      await page.close();
    }
  }

  const page = await browser.newPage({ locale: "en-US", viewport: { width: 1440, height: 960 } });
  try {
    const api = await installApi(page);
    await page.goto(url, { waitUntil: "networkidle" });
    await page.getByTestId("personal-goal-home").waitFor();
    assert.match(await page.getByRole("region", { name: "Control plane source" }).innerText(), /This machine/);
    const englishText = await page.locator("body").innerText();
    for (const chinese of ["本机", "仅查状态"]) assert.ok(!englishText.includes(chinese), `English Workspace shows ${chinese}`);
    await page.screenshot({ path: resolve(outputDir, "locale-default-english.png") });
    const statusOnly = page.getByRole("button", { name: "Status only · No model calls", exact: true });
    assert.ok(await statusOnly.isVisible(), "The independent status-only control follows the selected language");
    assert.equal(await statusOnly.getAttribute("aria-pressed"), "false");
    await statusOnly.click();
    assert.equal(await statusOnly.getAttribute("aria-pressed"), "true");
    await page.getByLabel("Send a message to LoopX").fill("Show the current state.");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    const managerViews = page.getByRole("navigation", { name: "Manager view", exact: true });
    const overview = managerViews.getByRole("button", { name: "Overview", exact: true });
    assert.equal(await overview.getAttribute("aria-current"), "page", "A reply preserves the selected overview");
    assert.equal(await page.locator(".personal-manager-conversation-tray").count(), 0, "Status replies do not cover the overview");
    await managerViews.getByRole("button", { name: "Chat", exact: true }).click();
    await page.locator(".personal-channel-timeline article.is-assistant header strong").getByText("Status only", { exact: true }).waitFor();
    assert.equal(api.turnRequests.length, 0, "Localizing the status-only route must not start an Agent Turn");
    assert.ok(!(await page.locator("body").innerText()).includes("LoopX 状态投影"), "The status-only reply source follows the selected language");
    await overview.click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator(".personal-channel-scroll").evaluate((element) => { element.scrollTop = 0; });
    const clippedStats = await page.locator(".personal-digest-stats span").evaluateAll((stats) =>
      stats.filter((stat) => {
        const rect = stat.getBoundingClientRect();
        return rect.left < 0 || rect.right > window.innerWidth;
      }).length,
    );
    assert.equal(clippedStats, 0, "English summary statistics fit the mobile viewport");
    await page.screenshot({ path: resolve(outputDir, "locale-default-english-mobile.png") });
    await page.setViewportSize({ width: 1440, height: 960 });
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("button", { name: /Language/ }).click();
    await page.getByRole("radio", { name: /Simplified Chinese/ }).click();
    assert.equal(await page.evaluate(() => localStorage.getItem("loopx-pw-locale")), "zh-CN");
    await page.reload({ waitUntil: "networkidle" });
    await page.getByRole("button", { name: "返回工作区", exact: true }).click();
    await page.getByTestId("personal-goal-home").waitFor();
    assert.equal(await page.locator("html").getAttribute("lang"), "zh-CN");
    assert.match(await page.getByRole("region", { name: "控制面来源" }).innerText(), /本机/);
    await page.screenshot({ path: resolve(outputDir, "locale-saved-chinese.png") });
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.getByRole("button", { name: /语言/ }).click();
    await page.getByRole("radio", { name: /English/ }).click();
    await page.reload({ waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Back to workspace", exact: true }).click();
    await page.getByTestId("personal-goal-home").waitFor();
    assert.equal(await page.locator("html").getAttribute("lang"), "en");
    assert.equal(await page.evaluate(() => localStorage.getItem("loopx-pw-locale")), "en");
    await page.getByRole("button", {name: "Settings", exact: true}).click();
    await page.getByRole("button", {name: /Appearance/}).click();
    const themes = page.locator('.personal-settings-choice-group');
    const themeOptions = themes.getByRole('radio');
    await page.screenshot({path: resolve(outputDir, 'settings-choice-desktop.png'), animations: 'disabled'});
    assert.equal(await themes.locator('[role="radio"][tabindex="0"]').count(), 1, 'Only the selected theme is a Tab stop');
    await themeOptions.nth(0).focus(); await page.keyboard.press('ArrowRight');
    assert.equal(await themeOptions.nth(1).getAttribute('aria-checked'), 'true');
    assert(await themeOptions.nth(1).evaluate(element => element === document.activeElement));
    await page.keyboard.press('End');
    assert.equal(await themeOptions.nth(2).getAttribute('aria-checked'), 'true');
    await page.keyboard.press('ArrowRight');
    assert.equal(await themeOptions.nth(0).getAttribute('aria-checked'), 'true', 'Arrow navigation wraps');
    await page.keyboard.press('ArrowLeft');
    assert.equal(await themeOptions.nth(2).getAttribute('aria-checked'), 'true');
    await page.keyboard.press('Home');
    assert.equal(await themeOptions.nth(0).getAttribute('aria-checked'), 'true');
    await page.keyboard.press('Tab');
    assert(!await themes.evaluate(element => element.contains(document.activeElement)), 'Tab leaves the group');
    await page.setViewportSize({width: 960, height: 640});
    await themeOptions.nth(0).focus();
    assert(await themeOptions.nth(0).evaluate(element => getComputedStyle(element).outlineStyle !== 'none'));
    await page.screenshot({path: resolve(outputDir, 'settings-choice-minimum.png'), animations: 'disabled'});
    await page.getByRole('button', {name: /Language/}).click();
    const languages = page.locator('.personal-language-options');
    const english = languages.getByRole('radio', {name: /English/});
    await english.focus(); await page.keyboard.press('ArrowDown');
    assert.equal(await page.evaluate(() => localStorage.getItem('loopx-pw-locale')), 'zh-CN');
    assert.equal(await languages.locator('[role="radio"][tabindex="0"]').count(), 1);
    assert(await languages.locator('[aria-checked="true"]').evaluate(element => element === document.activeElement));
    await page.keyboard.press('ArrowUp');
    assert.equal(await page.evaluate(() => localStorage.getItem('loopx-pw-locale')), 'en');
    await page.reload({waitUntil: 'networkidle'});
    assert.equal(await english.getAttribute('aria-checked'), 'true', 'Keyboard selection survives reload');
    assert.equal(api.turnRequests.length, 0, 'Preferences never start an Agent');
    console.log("workspace locale: Settings choices survive reload in both directions: ok");
  } finally {
    await page.close();
  }
}
