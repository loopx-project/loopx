#!/usr/bin/env node
// The expected tokens below come from style semantics, not from scanner output.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import { collectDefinitions } from "./check-css-custom-properties.mjs";

const fixturePath = (name) => fileURLToPath(new URL(`./fixtures/css-custom-properties/${name}`, import.meta.url));
const definitionsFor = (...names) => collectDefinitions(names.map(fixturePath)).definitions;
const realSinks = ["--fixture-jsx", "--fixture-cast", "--fixture-annotated", "--fixture-imperative"];
const inertTokens = [
  "--fixture-commented", "--fixture-block-commented", "--fixture-stringified", "--fixture-unbound",
  "--fixture-quoted-jsx", "--fixture-quoted-call", "--fixture-quoted-cast", "--fixture-quoted-annotation",
  "--fixture-template-jsx", "--fixture-regex-jsx", "--fixture-unrelated-call", "--fixture-nested-value",
  "--fixture-string-key", "--fixture-css-text", "--fixture-css-string", "--fixture-css-value",
];

// Independent semantic oracle: execute trusted fixtures as JavaScript and observe
// the values passed to JSX style props / imperative writes. No scanner functions
// or AST selection rules supply these observations. Inert examples never write.
function executeFixture(name) {
  const observed = new Set();
  const observeStyle = (style) => {
    for (const key of Object.keys(style ?? {})) if (key.startsWith("--")) observed.add(key);
  };
  const exports = {};
  const { outputText } = ts.transpileModule(readFileSync(fixturePath(name), "utf8"), {
    fileName: name,
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022 },
  });
  runInNewContext(outputText, {
    exports,
    React: { createElement: (_tag, props) => { observeStyle(props.style); return null; } },
  }, { timeout: 1000 });
  return { observed, exports, observeStyle };
}
const live = executeFixture("clean/sinks.tsx");
live.observeStyle(live.exports.annotatedSink);
live.observeStyle(live.exports.castSink(30));
live.exports.imperativeSink({ style: { setProperty: (key) => live.observeStyle({ [key]: "1px" }) } });
assert.deepEqual(live.observed, new Set(realSinks));
assert.deepEqual(definitionsFor("clean/sinks.tsx"), live.observed);
const dead = executeFixture("dead-source/dead.tsx");
assert.deepEqual(dead.observed, new Set(["--fixture-real"]));
assert.deepEqual(definitionsFor("dead-source/dead.tsx"), dead.observed);
assert.deepEqual(definitionsFor("negative/dataKeys.ts", "negative.css"), new Set());
assert.deepEqual(definitionsFor("clean/sinks.css"), new Set(["--fixture-stylesheet", "--fixture-registered"]));
assert.deepEqual(definitionsFor("brace-value/brace.tsx"), new Set(["--fixture-brace-live", "--fixture-brace-second"]));

// Exercise the shipped command, including stdout/stderr and the exit contract.
const run = (scope) => spawnSync(process.execPath,
  [fileURLToPath(new URL("./check-css-custom-properties.mjs", import.meta.url)), scope],
  { encoding: "utf8" });
const clean = run("fixture-clean");
assert.equal(clean.status, 0, `all supported sinks must pass:\n${clean.stdout}${clean.stderr}`);
for (const [scope, missing] of [
  ["fixture-dead-source", inertTokens],
  ["fixture-brace-value", ["--fixture-brace-ghost-before", "--fixture-brace-ghost-after"]],
]) {
  const failed = run(scope);
  assert.equal(failed.status, 1, `${scope} must fail:\n${failed.stdout}${failed.stderr}`);
  const reported = [...failed.stderr.matchAll(/var\((--[\w-]+)\)/g)].map((match) => match[1]);
  assert.deepEqual(new Set(reported), new Set(missing), `${scope}: report only undefined tokens`);
}
const unknown = run("fixture-does-not-exist");
assert.equal(unknown.status, 2);
console.log("check-css-custom-properties classifier contract: ok");
