#!/usr/bin/env node
/**
 * Reject `var(--token)` references that no stylesheet — and no inline style —
 * ever defines.
 *
 * Why this exists
 * ---------------
 * An undefined custom property in `var(--x)` with no fallback makes the whole
 * declaration *invalid at computed-value time*. The browser drops that one
 * declaration and the element silently inherits whatever the cascade would
 * otherwise give it: the wrong font stack, a missing border, a missing
 * background. Nothing logs, nothing fails, and a component can render slightly
 * wrong in production for months.
 *
 * That is exactly what happened here. Six tokens were referenced but never
 * defined anywhere in the repository:
 *
 *   var(--font-mono)       delivery-review.css, collaboration-card.css
 *   var(--pw-font-mono)    personal-workspace.css (typo for --font-mono)
 *   var(--pw-border)       personal-workspace.css
 *   var(--pw-canvas)       personal-workspace.css
 *   var(--pw-surface)      personal-workspace.css
 *   var(--pw-danger)       personal-workspace.css
 *
 * `var(--x, fallback)` is deliberately allowed: a fallback is an explicit
 * statement that the token is optional, so the declaration still computes.
 * Only bare `var(--x)` is checked.
 *
 * Scopes
 * ------
 *   --scope dashboard   apps/presentation/dashboard/src   (default)
 *
 * Adding a new scope means adding a real surface, not a directory that happens
 * to contain CSS. A scope that over-reports gets muted, which is worse than
 * not having the check.
 *
 * Exit codes: 0 = clean, 1 = undefined token references found.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";

const REPO_ROOT = resolve(import.meta.dirname, "..");

const SCOPES = {
  dashboard: {
    roots: ["apps/presentation/dashboard/src"],
    // Files that legitimately reference tokens owned by another surface.
    // Keep this list empty unless review establishes why a token is genuinely
    // out of scope for this surface.
    allowlistedFiles: new Set(),
  },
};

const STYLE_EXTENSIONS = new Set([".css"]);
const CODE_EXTENSIONS = new Set([".ts", ".tsx"]);

function walk(dir, extensions, found = []) {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === "dist") continue;
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      walk(full, extensions, found);
      continue;
    }
    const dot = entry.lastIndexOf(".");
    if (dot === -1) continue;
    if (extensions.has(entry.slice(dot))) found.push(full);
  }
  return found;
}

/**
 * Strip /* ... *\/ comments so a token mentioned in prose is not read as a
 * reference, while preserving character offsets for line numbers.
 */
function stripComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, (match) => match.replace(/[^\n]/g, " "));
}

function lineAt(text, index) {
  let line = 1;
  for (let i = 0; i < index && i < text.length; i += 1) {
    if (text[i] === "\n") line += 1;
  }
  return line;
}

/**
 * Definitions, per file.
 *
 * Definition forms that occur in this repository:
 *   1. inside a rule block        `.selector { --x: value; }`
 *   2. registered custom property  `@property --x { ... }`
 *   3. inline style object         `style={{ "--x": value }}`
 *   4. setProperty("--x", ...)     imperative
 *
 * Definitions are collected globally rather than per-cascade-scope. A token
 * defined on `.personal-workspace-shell` and referenced inside it is correct,
 * and proving that statically needs a cascade engine. Global collection trades
 * a little precision for zero false positives, which is the right trade for a
 * gate that must never cry wolf.
 */
function collectDefinitions(files) {
  const definitions = new Set();
  const reasons = new Map();
  const add = (name, reason) => {
    definitions.add(name);
    if (!reasons.has(name)) reasons.set(name, reason);
  };

  for (const file of files) {
    const rel = relative(REPO_ROOT, file).split(sep).join("/");
    const raw = readFileSync(file, "utf8");
    const text = stripComments(raw);

    for (const match of text.matchAll(/@property\s+(--[A-Za-z0-9_-]+)/g)) {
      add(match[1], `@property in ${rel}`);
    }
    for (const match of text.matchAll(/(?:^|[;{(\s,])(--[A-Za-z0-9_-]+)\s*:/gm)) {
      add(match[1], `declared in ${rel}`);
    }
    for (const match of text.matchAll(/setProperty\(\s*["'`](--[A-Za-z0-9_-]+)["'`]/g)) {
      add(match[1], `setProperty in ${rel}`);
    }
    for (const match of text.matchAll(/["'`](--[A-Za-z0-9_-]+)["'`]\s*:/g)) {
      add(match[1], `inline style in ${rel}`);
    }
  }

  return { definitions, reasons };
}

/**
 * Bare references: `var(--x)` with no fallback. `var(--x, ...)` is skipped even
 * when the fallback is a nested var() or another token.
 */
function collectBareReferences(files) {
  const references = [];
  for (const file of files) {
    const rel = relative(REPO_ROOT, file).split(sep).join("/");
    const text = stripComments(readFileSync(file, "utf8"));
    for (const match of text.matchAll(/var\(\s*(--[A-Za-z0-9_-]+)\s*\)/g)) {
      references.push({ token: match[1], file: rel, line: lineAt(text, match.index) });
    }
  }
  return references;
}

function main() {
  const scopeName = process.argv[2] ?? "dashboard";
  const scope = SCOPES[scopeName];
  if (!scope) {
    console.error(`Unknown scope: ${scopeName}`);
    console.error(`Known scopes: ${Object.keys(SCOPES).join(", ")}`);
    process.exitCode = 2;
    return;
  }

  const styleFiles = [];
  const codeFiles = [];
  for (const root of scope.roots) {
    const absolute = join(REPO_ROOT, root);
    styleFiles.push(...walk(absolute, STYLE_EXTENSIONS));
    codeFiles.push(...walk(absolute, CODE_EXTENSIONS));
  }

  // Anti-vacuity: if the roots or extensions stop matching, fail loudly rather
  // than reporting a clean tree that was never parsed.
  if (styleFiles.length === 0) {
    console.error(`check-css-custom-properties: no stylesheets found under ${scope.roots.join(", ")}`);
    process.exitCode = 1;
    return;
  }

  const { definitions, reasons } = collectDefinitions([...styleFiles, ...codeFiles]);
  const references = collectBareReferences(styleFiles).filter(
    (reference) => !scope.allowlistedFiles.has(reference.file),
  );

  const undefinedReferences = references.filter((reference) => !definitions.has(reference.token));

  // Anti-vacuity guard, independent of the definitions scan.
  //
  // A sentinel set drawn from the same scan cannot detect a scan that stopped
  // matching: if the definition regex breaks, the sentinels simply are not
  // found either, and a naive check would report every reference as undefined
  // (noisy) — or, if written as "fail only when a sentinel IS found", pass
  // silently (dangerous). So this asserts a floor on how many tokens the scan
  // must produce, derived from the tree rather than from the scan.
  //
  // The floor is a lower bound, not an equality: adding tokens is normal. When
  // a genuine token removal drops the count below it, lower the constant in the
  // same change that removes the tokens.
  const DEFINED_TOKEN_FLOOR = 30;
  if (definitions.size < DEFINED_TOKEN_FLOOR) {
    console.error(
      `check-css-custom-properties: only ${definitions.size} defined tokens found under ` +
        `${scope.roots.join(", ")}, below the floor of ${DEFINED_TOKEN_FLOOR}. ` +
        `The definition scan is probably broken, not the tree.`,
    );
    process.exitCode = 1;
    return;
  }

  if (undefinedReferences.length > 0) {
    console.error(
      `check-css-custom-properties: ${undefinedReferences.length} bare var() reference(s) ` +
        `to tokens that are never defined. Each one silently drops its declaration.\n`,
    );
    for (const reference of undefinedReferences) {
      console.error(`  ${reference.file}:${reference.line}  var(${reference.token})`);
    }
    console.error(
      `\nFix by defining the token or giving the reference a fallback. ` +
        `Do not delete the declaration to silence this check.`,
    );
    process.exitCode = 1;
    return;
  }

  const scoped = references.filter((reference) => reference.token.startsWith("--pw-"));
  console.log(
    `check-css-custom-properties[${scopeName}]: ${styleFiles.length} stylesheets, ` +
      `${definitions.size} defined tokens, ${references.length} bare references ` +
      `(${scoped.length} --pw-*). No undefined references.`,
  );
}

main();
