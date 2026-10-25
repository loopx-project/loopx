import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {outputDir, repoRoot} from "./fixture.mjs";
import {startHistoryOwner} from "./research-history.mjs";

// Real registration, native projection and HTTP readback. Independent expected
// values protect role identity: a highlighted best is never invented retention.
export const researchNativeReadingScenario = {
  id: "research-native-reading",
  async run({browser, collectCoverage}) {
    const cases = [
      {id: "lower-negative", values: [-1, -3, -2], higher: false, retained: 0, expected: [-3, -1, -2, -1]},
      {id: "higher-zero", values: [-3, 0, -1], higher: true, retained: 0, expected: [0, -3, -1, -3]},
      {id: "invalid-first", values: [null, 0, -1], higher: true, retained: null, expected: [0, null, -1, null]},
      {id: "all-invalid", values: [null, null], higher: false, retained: null, expected: [null, null, null, null]},
      {id: "partial", values: [8, 7, 9], higher: true, retained: 1, partial: true, expected: [9, null, 9, 7]},
      {id: "empty", values: [], higher: true, retained: null, partial: true, expected: [null, null, null, null]},
      {id: "long-unit", values: [3, 1, 2], higher: false, retained: 0, unit: "synthetic-unit-".repeat(5), expected: [1, 3, 2, 3]},
      {id: "decimal", values: [0.8123456789012345, 0.8765432109876543, 0.8123456789012345], higher: true, retained: 1,
        expected: [0.8765432109876543, 0.8123456789012345, 0.8123456789012345, 0.8765432109876543], display: ["0.8765", "0.8123", "0.8123", "0.8765"], gain: "+0.0642"},
      {id: "tiny", values: [0.0000001234567890123456, 0.0000001123456789012345], higher: true, retained: 1,
        expected: [0.0000001234567890123456, 0.0000001234567890123456, 0.0000001123456789012345, 0.0000001123456789012345], display: ["1.235E-7", "1.235E-7", "1.123E-7", "1.123E-7"], gain: "-1.111E-8"},
      {id: "large", values: [123456789.12345679], higher: true, retained: 0,
        expected: [123456789.12345679, 123456789.12345679, 123456789.12345679, 123456789.12345679], display: ["1.235E8", "1.235E8", "1.235E8", "1.235E8"], gain: "0"},
      ...["command", "mixed", "damaged", "empty-output", "unsafe-exit", "tool-text", "tool-shell", "tool-pending",
        "tool-function", "tool-mixed", "tool-damaged", "tool-unsafe-exit", "tool-empty"].map(source => ({id: `${source}-source`, source,
        values: [2, 1], higher: false, retained: 0, expected: [1, 2, 1, 2]})),
    ];
    const root = await mkdtemp(resolve(tmpdir(), "loopx-native-reading-"));
    let owner, page;
    try {
      const fixture = JSON.parse(await readFile(resolve(repoRoot, "apps/presentation/dashboard/src/data/fixtures/research-native-readback.json"), "utf8"));
      const original = fixture.analyses[0];
      fixture.events = [];
      fixture.analyses = cases.map(c => {
        const a = structuredClone(original), native = JSON.parse(a.artifacts["native.json"]);
        a.title = c.id;
        // These supplied outputs use the legacy assessment format, independent
        // of the current registration default for newly generated reports.
        a.config = {model: "fixture", effort: "max", service_tier: "fast", research_synthesis: false};
        a.input.scope.run_id = c.id;
        native.scope = {...a.input.scope};
        native.complete = !c.partial;
        native.metric.higher_is_better = c.higher;
        if (c.unit) native.metric.unit = c.unit;
        native.retained_result_id = c.retained === null ? null : `result-${c.retained}`;
        native.samples = c.values.map((value, index) => ({
          submission_index: index + (c.partial ? 7 : 1), result_id: `result-${index}`, candidate_ref: `candidate-${index}`,
          submitted_at: null, observed_at: `2026-01-01T00:0${index + 1}:00Z`, valid: value !== null, value,
          citation: {source_id: "facts", first_line: index + 1, last_line: index + 1},
        }));
        if (c.gain) native.comparison = {baseline_result_id: "result-0",
          retention_citation: {source_id: "facts", first_line: c.retained + 1, last_line: c.retained + 1}};
        let text = c.values.map((v, i) => `Synthetic observation ${i}: value ${v ?? "unknown"}; valid ${v !== null}.`).join("\n") || "No measurements captured.";
        if (c.source) {
          const command = (output, exitCode) => JSON.stringify({type: "event_msg", ordinal: exitCode + 1,
            payload: {type: "item_completed", item: {type: "CommandExecution", status: "completed", exit_code: exitCode,
              command: ["python", "-c", "print('synthetic output')"], aggregated_output: output}}});
          const output = JSON.stringify({cases: 8, improved: 3, regressed: 2, unchanged: 3, complete_cost: null}, null, 2);
          const longFailure = "Validation failed\n0 accepted updates\n<script>window.sourceExecuted=true</script>\n"
            + Array.from({length: 32}, (_, i) => `Synthetic case ${i + 1}: negative result retained; 完整成本仍未知。`).join("\n");
          text = [command(c.source === "empty-output" ? "" : output, 0), c.source === "mixed"
            ? JSON.stringify({type: "unknown_event", text: "Unrecognized source MUST_REMAIN_LITERAL"})
            : c.source === "damaged" ? "{malformed MUST_REMAIN_LITERAL"
            : command(c.source === "empty-output" ? "" : longFailure, c.source === "unsafe-exit" ? Number.MAX_SAFE_INTEGER + 1 : 17)].join("\n");
          if (c.source.startsWith("tool-")) {
            const shell = (text, exit_code) => JSON.stringify({chunk_id: "synthetic", wall_time_seconds: 0.25, exit_code, output: text});
            const tool = output => JSON.stringify({type: "response_item", payload: {
              type: c.source === "tool-function" ? "function_call_output" : "custom_tool_call_output", output}});
            const part = text => ({type: "input_text", text});
            const first = ["tool-text", "tool-function"].includes(c.source) ? output : c.source === "tool-empty" ? ""
              : [part("Recorded tool response\n"), part(c.source === "tool-unsafe-exit"
                ? '{"chunk_id":"synthetic","wall_time_seconds":0.25,"exit_code":9007199254740993,"output":"MUST_REMAIN_LITERAL"}'
                : shell(output, c.source === "tool-pending" ? null : 0))];
            const second = c.source === "tool-mixed" ? [part(longFailure), {type: "input_image", image_url: "synthetic"}]
              : c.source === "tool-damaged" ? [part(longFailure), {type: "input_text", text: 12}]
              : c.source === "tool-empty" ? "" : [part(shell(longFailure, 17))];
            text = [tool(first), tool(second)].join("\n");
          }
          native.samples[0].citation.last_line = 2;
        }
        a.artifacts["facts.txt"] = text;
        a.artifacts["native.json"] = JSON.stringify(native);
        Object.assign(a.input.sources[0], {text, artifact_sha256: createHash("sha256").update(text).digest("hex")});
        return a;
      });
      const path = resolve(root, "cases.json");
      await writeFile(path, JSON.stringify(fixture));
      // Same public synthetic data for reproducible before/after inspection.
      await writeFile(resolve(outputDir, "research-citation-reader-fixture.json"), JSON.stringify({...fixture,
        analyses: fixture.analyses.filter(a => ["command-source", "tool-shell-source"].includes(a.title))}));
      owner = await startHistoryOwner(path);
      page = await browser.newPage({viewport: {width: 1280, height: 900}, hasTouch: true});
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      if (collectCoverage) await page.coverage.startJSCoverage({resetOnNavigation: false});
      await page.addInitScript(() => {if (!localStorage.getItem("loopx-pw-locale")) localStorage.setItem("loopx-pw-locale", "zh-CN");});
      await page.goto(`${owner.base}/chat/?goalId=${fixture.goal_id}&view=research&statusUrl=%2Fstatus.json`, {waitUntil: "domcontentloaded"});
      const native = page.getByRole("region", {name: "原生测量", exact: true});
      const selector = page.locator(".research-progress select");
      for (const c of cases) {
        await selector.selectOption({label: c.id});
        const selected = await selector.inputValue();
        await page.locator(`.research-progress-scope[data-pack-id="${selected}"]`).waitFor();
        await native.waitFor();
        for (const [index, key] of ["best_valid", "initial", "final_valid", "retained"].entries()) {
          const reading = native.locator(`[data-native-point="${key}"]`);
          if (c.expected[index] === null) {
            assert.equal(await reading.locator("button").count(), 0);
            assert.match(await reading.innerText(), /未知/);
          } else {
            assert.equal(await reading.locator("strong").textContent(), c.display?.[index] ?? String(c.expected[index]));
            assert((await reading.locator("button").getAttribute("aria-label")).includes(String(c.expected[index])), "The exact native value remains bound to the evidence action");
          }
        }
        if (c.gain) assert.equal(await page.locator("[data-retained-improvement]").textContent(), c.gain);
        assert.match(await native.innerText(), c.higher ? /越高越好/ : /越低越好/);
        assert.match(await native.locator(".research-native-best .research-native-unit").textContent(), new RegExp(c.unit ?? "points"), "The raw unit remains visible even when no valid measurement exists");
        assert.match(await native.locator(".research-native-scope").innerText(), new RegExp(`${c.values.filter(v => v !== null).length} / ${c.values.length}.*有效结果`));
        if (c.partial) assert.match(await native.innerText(), /部分窗口 · 起点未知/);
        await page.setViewportSize({width: 390, height: 844});
        const details = native.locator(".research-native-basis");
        if (await details.evaluate(e => e.open)) await details.locator(":scope > summary").click();
        await details.locator(":scope > summary").focus();
        await page.keyboard.press("Enter");
        assert.match(await details.innerText(), /2026-01-01T00:00:00Z/);
        if (c.display) for (const value of c.expected) assert((await details.innerText()).includes(String(value)), "Expanded sources retain the full native precision");
        assert(await details.evaluate(e => parseFloat(getComputedStyle(e).fontSize) >= 14));
        assert(await native.evaluate(e => e.scrollWidth <= e.clientWidth + 1));
        if (c.id === "lower-negative") {
          await native.evaluate(e => e.scrollIntoView({block: "start"}));
          await page.screenshot({path: resolve(outputDir, "research-native-sources-phone.png"), animations: "disabled"});
          await page.setViewportSize({width: 1280, height: 900});
          await page.screenshot({path: resolve(outputDir, "research-native-sources-desktop.png"), animations: "disabled"});
          await page.setViewportSize({width: 390, height: 844});
          await details.locator("li").last().scrollIntoViewIfNeeded();
          await page.screenshot({path: resolve(outputDir, "research-native-source-tail-phone.png"), animations: "disabled"});
        }
        await details.locator(":scope > summary").tap();
        if (c.id === "lower-negative") {
          const retained = native.getByRole("button", {name: /原生保留结果.*查看原始证据/});
          await retained.focus(); await retained.press("Enter");
          const citation = page.getByRole("region", {name: "引用原文", exact: true});
          await citation.waitFor();
          await citation.getByText(/Synthetic observation 0: value -1/).waitFor();
          assert.match(await citation.innerText(), /Synthetic observation 0: value -1/);
          await citation.press("Escape");
          await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label")?.startsWith("原生保留结果:"));
          await page.screenshot({path: resolve(outputDir, "research-native-negative-phone.png"), animations: "disabled"});
        }
        if (c.source) {
          const retained = native.getByRole("button", {name: /原生保留结果.*查看原始证据/});
          await retained.focus(); await retained.press("Enter");
          const citation = page.getByRole("region", {name: "引用原文", exact: true});
          await citation.waitFor();
          const literal = fixture.analyses.find(a => a.title === c.id).artifacts["facts.txt"];
          if (["mixed", "damaged", "unsafe-exit", "tool-mixed", "tool-damaged"].includes(c.source)) {
            await citation.locator("pre").waitFor();
            assert.equal(await citation.locator("pre").textContent(), literal, "Never discard unsupported or malformed neighboring evidence");
            assert.equal(await citation.locator("details").count(), 0);
          } else {
            await citation.locator(".analysis-source-content").waitFor();
            assert.equal(await citation.locator(".analysis-source-command").count(), 2);
            await citation.getByText("行 2", {exact: true}).waitFor();
            if (c.source !== "tool-empty") await citation.getByText("退出码 17", {exact: true}).waitFor();
            if (c.source === "tool-pending") await citation.getByText("尚无退出码", {exact: true}).waitFor();
            const raw = citation.locator(".analysis-source-raw");
            assert.equal(await raw.getAttribute("open"), null);
            await raw.locator("summary").tap();
            assert.equal(await raw.locator("pre").textContent(), literal, "Raw evidence remains byte-for-byte available");
            await raw.locator("summary").tap();
            if (["empty-output", "tool-empty"].includes(c.source)) assert.equal(await citation.getByText("输出为空", {exact: true}).count(), 2);
            else {
              const records = literal.split("\n").map(line => JSON.parse(line).payload);
              const outputs = await citation.locator(".analysis-source-output").allTextContents();
              if (c.source.startsWith("tool-")) {
                const main = JSON.stringify({cases: 8, improved: 3, regressed: 2, unchanged: 3, complete_cost: null}, null, 2);
                assert(outputs.includes(c.source === "tool-unsafe-exit" ? records[0].output[1].text : main));
                if (c.source === "tool-unsafe-exit") {
                  assert.equal(await citation.getByText("退出码 9007199254740992", {exact: true}).count(), 0, "Unsafe numeric metadata must not be rounded into a display fact");
                  assert(outputs.some(text => text.includes("9007199254740993")));
                }
                if (["tool-shell", "tool-pending", "tool-unsafe-exit"].includes(c.source)) assert.equal(outputs[0], "Recorded tool response\n", "Preserve text blocks and order, including surrounding caveats");
                assert.equal(outputs.at(-1), JSON.parse(records[1].output[0].text).output);
              } else {
                for (const [i, record] of records.entries()) assert.equal(outputs[i], record.item.aggregated_output);
              }
              assert.equal(await citation.locator("script").count(), 0);
              assert.equal(await page.evaluate(() => window.sourceExecuted), undefined);
              for (const width of [1280, 390]) {
                await page.setViewportSize({width, height: 900});
                await citation.evaluate(e => e.scrollIntoView({block: "start"}));
                assert(await citation.evaluate(e => e.scrollWidth <= e.clientWidth + 1));
                assert.equal(await citation.locator(".analysis-source-output").first().evaluate(e => parseFloat(getComputedStyle(e).fontSize)), 16);
                assert(await citation.locator(".analysis-source-output").last().evaluate(e => e.clientHeight > 360), "Full long output uses page reading, not an inner clipped wall");
                await page.screenshot({path: resolve(outputDir, `research-citation-output-${width}.png`), animations: "disabled"});
              }
            }
          }
          await citation.press("Escape");
          await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label")?.startsWith("原生保留结果:"));
        }
        if (c.unit) {
          await page.evaluate(() => document.documentElement.style.fontSize = "200%");
          assert(await native.evaluate(e => e.scrollWidth <= e.clientWidth + 1), "Long unit remains readable at 200% text");
          await page.screenshot({path: resolve(outputDir, "research-native-large-text-phone.png"), animations: "disabled"});
          await page.evaluate(() => document.documentElement.style.removeProperty("font-size"));
        }
        await page.setViewportSize({width: 1280, height: 900});
      }
      await page.evaluate(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.reload({waitUntil: "domcontentloaded"});
      await page.getByRole("region", {name: "Native measurements", exact: true}).waitFor();
      await selector.selectOption({label: "decimal"});
      await page.waitForFunction(() => document.querySelector('[data-native-point="best_valid"] strong')?.textContent === "0.8765");
      assert.equal(await page.locator("[data-retained-improvement]").textContent(), "+0.0642");
      await page.getByRole("button", {name: "About native measurements", exact: true}).focus();
      await page.getByRole("tooltip").waitFor({state: "visible"});
      await page.keyboard.press("Escape");
      await selector.selectOption({label: "tool-pending-source"});
      const selected = await selector.inputValue();
      await page.locator(`.research-progress-scope[data-pack-id="${selected}"]`).waitFor();
      await page.getByRole("region", {name: "Native measurements", exact: true}).getByRole("button", {name: /Native retained result.*Read original evidence/}).click();
      const originalPanel = page.getByRole("region", {name: "Cited original", exact: true});
      await originalPanel.getByText("Exit code 17", {exact: true}).waitFor();
      await originalPanel.getByText("No exit code yet", {exact: true}).waitFor();
      await originalPanel.getByText("Complete raw record", {exact: true}).waitFor();
      await originalPanel.press("Escape");
      const readback = await (await page.request.get(`${owner.base}/fixture/readback`)).json();
      assert.equal(readback.source_unchanged, true);
      assert.equal(readback.mutation_attempts, 0);
      assert.deepEqual(errors, []);
      return {coverageEntries: collectCoverage ? await page.coverage.stopJSCoverage() : [],
        note: "real-owner native roles and citations; literal command output, failure/empty/long text, raw preservation, unknown/malformed fallback, exact focus return, desktop/390 and Chinese/English; no mutation"};
    } finally {
      await page?.close();
      await owner?.close();
      await rm(root, {recursive: true, force: true});
    }
  },
};
