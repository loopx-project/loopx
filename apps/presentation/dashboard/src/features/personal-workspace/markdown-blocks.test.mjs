import assert from "node:assert/strict";
import {test} from "node:test";
import {markdownExcerpt, markdownOutline, parseMarkdownBlocks} from "./markdown-blocks.ts";

test("document anchors are stable and unique across repeated headings and suffix collisions", () => {
  const text = "# 阅读\n\n## 阅读\n\n## 阅读-2\n\n### Evidence\n\n###### Detail";
  const outline = markdownOutline(parseMarkdownBlocks(text), "answer");
  assert.deepEqual(outline.map(row => row.id), ["answer-阅读", "answer-阅读-2", "answer-阅读-2-2", "answer-evidence", "answer-detail"]);
  assert.deepEqual(outline.map(row => row.level), [2, 3, 3, 4, 6]);
  assert.deepEqual(markdownOutline(parseMarkdownBlocks(text), "answer"), outline, "Reload keeps the same fragment destinations");
  assert.deepEqual(markdownOutline(parseMarkdownBlocks("#### Start\n\n###### Detail"), "second").map(row => row.level), [2, 4]);
});

test("an excerpt is a verbatim prefix ending outside code, list and table blocks", () => {
  const code = ["````text", ...Array.from({length: 200}, (_, n) => `line ${n}: exact source`), "```", "still inside the four-backtick fence", "````"].join("\n");
  const suffix = `\n\n## Next\n\n${"后续记录。".repeat(600)}`;
  const text = code + suffix;
  assert.equal(markdownExcerpt(text), code, "The full 200-line fence remains intact even beyond the preview budget");
  const parsed = parseMarkdownBlocks(text);
  assert.equal(parsed[0].type, "code");
  assert.ok(parsed[0].text.endsWith("```\nstill inside the four-backtick fence"));

  for (const block of [
    Array.from({length: 90}, (_, n) => `- requirement ${n}: keep the complete list`).join("\n"),
    ["| Measure | Value |", "|---|---|", ...Array.from({length: 90}, (_, n) => `| item ${n} | exact value |`)].join("\n"),
  ]) {
    assert.equal(markdownExcerpt(block + suffix), block);
  }
});

test("single long blocks and short answers stay complete; heading-only prefixes are not excerpts", () => {
  assert.equal(markdownExcerpt("短答复。"), null);
  assert.equal(markdownExcerpt("unbroken answer ".repeat(500)), null);
  const introduction = "首段完整陈述。".repeat(200);
  const text = `# 原标题\n\n${introduction}\n\n## 后续\n\n${"更多完整正文。".repeat(300)}`;
  assert.equal(markdownExcerpt(text), `# 原标题\n\n${introduction}`);
});

test("unclosed and tilde fences retain their literal content", () => {
  const text = "~~~text\n# Not a heading\n| Not | a table |\n~~~\n\n## Actual heading";
  assert.deepEqual(markdownOutline(parseMarkdownBlocks(text), "answer").map(row => row.text), ["Actual heading"]);
  assert.equal(markdownExcerpt("```\n" + "code\n".repeat(1000)), null, "An unclosed block cannot be advertised as a complete excerpt");
});
