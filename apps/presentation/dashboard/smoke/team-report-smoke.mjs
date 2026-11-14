import assert from "node:assert/strict";
import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {MarkdownText} from "../src/features/personal-workspace/markdown.tsx";
import {TeamArtifactContent} from "../src/features/personal-workspace/team-artifact-content.tsx";
import {adoptLoopXTeamWork, fetchLoopXTeamWork, readLoopXTeamWork, revalidateLoopXTeamWork} from "../src/data/chat.ts";

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
assert.match(chat, /<h2[^>]*class="personal-md-heading is-h1"[^>]*>Research<\/h2>/,
  "A document heading is navigable semantic HTML beneath the page title");
assert.doesNotMatch(chat, /<h1\b/, "Embedded documents do not create a second page title");
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
const adjacentNamedAndPlainLinks = renderToStaticMarkup(createElement(MarkdownText, {text:
  '[Topic](https://en.wikipedia.org/wiki/Reference_(mathematics)) https://example.org/after'}));
assert.match(adjacentNamedAndPlainLinks,
  /href="https:\/\/en.wikipedia.org\/wiki\/Reference_\(mathematics\)"[^>]*>Topic<\/a> <a/);
assert.match(adjacentNamedAndPlainLinks, /href="https:\/\/example.org\/after"/);
assert.equal((adjacentNamedAndPlainLinks.match(/<a /g) ?? []).length, 2,
  'A balanced-parenthesis Markdown destination does not consume an adjacent plain URL');
const nestedParenthesesLink = renderToStaticMarkup(createElement(MarkdownText, {text:
  '[Nested](https://example.org/a_(b_(c)))'}));
assert.match(nestedParenthesesLink, /href="https:\/\/example.org\/a_\(b_\(c\)\)"[^>]*>Nested<\/a>/);
// linkify-it 5.0.2 stops scanning userinfo at its bound, so an automatic match
// can end inside a longer address and point at the prefix host. The renderer
// must link only the addresses it matched completely.
const badge = "a".repeat(39);
const longUserinfo = `${badge}.example.org`;
const boundary = renderToStaticMarkup(createElement(MarkdownText, {text: [
  `At the bound: https://${"a".repeat(50)}@trusted.example/path`,
  `Past the bound: https://${longUserinfo}@trusted.example/path`,
  `Named past the bound: [Named long form](https://${longUserinfo}@trusted.example/path)`,
  `Recovered: https://example.org/after`,
].join('\n\n')}));
assert.match(boundary, new RegExp(`href="https://${"a".repeat(50)}@trusted\\.example/path"`),
  "Userinfo at the linkify-it bound still opens the address the reader sees");
assert.doesNotMatch(boundary, new RegExp(`href="https://${badge}\\.example\\.org"`),
  "A match that stopped inside userinfo never becomes a link to the prefix host");
assert.ok(boundary.includes(`${longUserinfo}@trusted.example/path`),
  "The incomplete address stays visible as inert text");
assert.match(boundary, new RegExp(`href="https://${longUserinfo}@trusted\\.example/path"[^>]*>Named long form</a>`),
  "An explicit Markdown destination keeps its literal URL");
assert.match(boundary, /href="https:\/\/example\.org\/after"/, "Normal links after the boundary still render");
assert.equal((boundary.match(/<a /g) ?? []).length, 3,
  "Only the complete addresses are links: at the bound, the named form and the recovered one");
assert.doesNotMatch(boundary, /href="(?:javascript:|ftp:|mailto:)/);
const json = renderToStaticMarkup(createElement(TeamArtifactContent, {artifact: {...artifact, ref: "report.json"}, label: "JSON"}));
assert.doesNotMatch(json, /<table>/, "File suffix does not turn JSON evidence into a report");
console.log("report rendering: readable table, raw fidelity, safe links and inert HTML, chat parity passed");

const readback = {operation_id: "source", request_id: "request", agent_id: "agent", todo_id: null,
  status: "accepted", worker_active: false, recovery_required: false, artifacts: [artifact], extension: {preserved: true}};
const originalFetch = globalThis.fetch;
try {
  let response = readback;
  let operation;
  globalThis.fetch = async (_url, init) => {
    assert.equal(JSON.parse(init.body).operation, operation);
    return new Response(JSON.stringify(response));
  };
  for (const [name, read] of [
    ["read", () => readLoopXTeamWork("session", "source")],
    ["revalidate", () => revalidateLoopXTeamWork("session", "source")],
    ["adopt", () => adoptLoopXTeamWork("session", "source", "consumer")],
  ]) {
    operation = name;
    for (const invalid of [
      {...readback, ok: false}, {...readback, operation_id: "other"}, {...readback, request_id: null},
      {...readback, artifacts: [{...artifact, text: null}]}, {...readback, dependencies: "invalid"},
      {...readback, current_use: {state: "unknown"}},
      {...readback, adoptions: [{consumer_operation_id: "consumer", source_artifacts: null}]},
    ]) {
      response = invalid;
      await assert.rejects(read, /cannot be verified/);
    }
    response = readback;
    assert.deepEqual(await read(), readback, `${name} retries keep the complete original readback`);
  }
  operation = "operations";
  const row = {record_id: "record", operation_id: "source", status: "accepted", recovery_required: false,
    artifacts: [{ref: artifact.ref, sha256: artifact.sha256}]};
  const page = {items: [row], has_more: true, next_cursor: "next", page_readback_complete: true, extension: "preserved"};
  for (const invalid of [
    {...page, page_readback_complete: "false"}, {...page, has_more: "false"}, {...page, items: "invalid"},
    {...page, items: [{...row, recovery_required: "false"}]}, {...page, next_cursor: null},
    {...page, items: [{...row, status: "unavailable", recovery_required: null}]},
    {...page, schema_version: "unsupported"}, {...page, ok: false},
  ]) {
    response = invalid;
    await assert.rejects(() => fetchLoopXTeamWork("session"), /cannot be verified/);
  }
  response = page;
  assert.deepEqual(await fetchLoopXTeamWork("session"), page);
  response = {...page, has_more: false, next_cursor: null};
  assert.deepEqual(await fetchLoopXTeamWork("session", "next"), response, "A later page remains readable");
} finally {globalThis.fetch = originalFetch;}
