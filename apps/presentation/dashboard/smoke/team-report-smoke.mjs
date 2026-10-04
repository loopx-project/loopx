import assert from "node:assert/strict";
import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {MarkdownText} from "../src/features/personal-workspace/markdown.tsx";
import {TeamArtifactContent} from "../src/features/personal-workspace/team-artifact-content.tsx";

const text = '# Research\n\n| Measure | Value |\n|---|---:|\n| **Cash** | 75 |\n| Escaped \\| label | 25 |\n\n[Source](https://example.org/report)\n<script>window.pwned=true</script>\n[Unsafe](javascript:alert(1))\n![Tracking](https://example.org/pixel)';
const artifact = {ref: "report.md", sha256: "a".repeat(64), text};
const html = renderToStaticMarkup(createElement(TeamArtifactContent, {artifact, label: "Report"}));
assert.match(html, /<table>/); assert.match(html, /scope="col"/); assert.match(html, /<strong>Cash<\/strong>/);
assert.match(html, /Escaped \| label/); assert.match(html, /href="https:\/\/example.org\/report"/);
assert.doesNotMatch(html, /<script|<img|href="javascript:/);
assert.match(html, /&lt;script&gt;/);
const raw = renderToStaticMarkup(createElement(TeamArtifactContent, {artifact, label: "Report", raw: true}));
assert.doesNotMatch(raw, /<table>|<a /); assert.match(raw, /\| Measure \| Value \|/);
const chat = renderToStaticMarkup(createElement(MarkdownText, {text}));
assert.match(chat, /<table>/, "A conversation renders the same safe report table as an artifact");
assert.doesNotMatch(chat, /<script|<img|href="javascript:/);
assert.match(chat, /&lt;script&gt;/);
const malformed = renderToStaticMarkup(createElement(MarkdownText, {text: "| One | Two |\n|---|\n| kept | intact |"}));
assert.doesNotMatch(malformed, /<table>/); assert.match(malformed, /kept/);
const links = renderToStaticMarkup(createElement(MarkdownText, {text: [
  '中文：https://example.org/forms/demo。English: https://example.org/forms/en，下一步。',
  'Source: https://example.org/reports/cash_flow_(2025)?lang=en&view=all.',
  '[Named source](https://example.org/named)',
  '`https://example.org/inline-code`',
  '```sh\nhttps://example.org/fenced-code\n```',
  'example.org someone@example.org ftp://example.org/file javascript:alert(1)',
].join('\n\n')}));
assert.match(links, /href="https:\/\/example.org\/forms\/demo"/);
assert.match(links, /href="https:\/\/example.org\/forms\/en"/);
assert.match(links, /href="https:\/\/example.org\/reports\/cash_flow_\(2025\)\?lang=en&amp;view=all"/);
assert.match(links, /href="https:\/\/example.org\/named"[^>]*>Named source<\/a>/);
assert.equal((links.match(/<a /g) ?? []).length, 4, 'Only explicit web destinations are links; code, email and inferred domains stay text');
assert.equal((links.match(/target="_blank"/g) ?? []).length, 4, 'Opening a destination preserves the current conversation');
assert.match(links, /<\/a>。English:/);
assert.doesNotMatch(links, /<script|<img|href="(?:javascript:|ftp:|mailto:)/);
const json = renderToStaticMarkup(createElement(TeamArtifactContent, {artifact: {...artifact, ref: "report.json"}, label: "JSON"}));
assert.doesNotMatch(json, /<table>/, "File suffix does not turn JSON evidence into a report");
console.log("report rendering: readable table, raw fidelity, safe links and inert HTML, chat parity passed");
