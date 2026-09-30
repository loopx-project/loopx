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
    // Lower bound on how many definitions the scan must produce. See the
    // anti-vacuity guard in main() for why this is asserted from the tree.
    definedTokenFloor: 30,
  },
};

const STYLE_EXTENSIONS = new Set([".css"]);
const CODE_EXTENSIONS = new Set([".ts", ".tsx"]);

function extensionOf(file) {
  const dot = file.lastIndexOf(".");
  return dot === -1 ? "" : file.slice(dot);
}

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
 * Inline-style custom properties written from TS/TSX, e.g.
 *
 *   style={{ "--goal-hue": identity.hue } as CSSProperties}
 *   const style: CSSProperties = { "--pw-offset": "2px" };
 *
 * Why this is not "any quoted `--x:` key"
 * ---------------------------------------
 * A quoted property is only a *definition* when it lands in a style sink. An
 * ordinary data object — a theme metadata table, an i18n map, a token catalog —
 * can carry the same quoted key without ever setting a CSS property:
 *
 *   export const themeMetadata = { "--review-ghost": "not a style" };
 *
 * Treating that as a definition is how a genuinely undefined
 * `var(--review-ghost)` gets waved through: the reference is subtracted from the
 * undefined set and the gate exits 0. That is the exact silent failure this
 * checker exists to prevent, so the match has to be bounded to a style sink.
 *
 * `setProperty` is matched separately below.
 */
const STYLE_SINK_OPENERS = [
  // JSX attribute: style={{ ... }} / style={{ ... } as CSSProperties}
  /style\s*=\s*\{\s*\{/g,
  // Object property in a style constant: style: { ... }
  /(?:^|[^A-Za-z0-9_$])style\s*:\s*\{/g,
  // A value annotated as CSSProperties: const style: CSSProperties = { ... }
  /:\s*CSSProperties\s*=\s*\{/g,
];

/**
 * Objects justified by a trailing cast rather than a leading annotation:
 *
 *   return { "--goal-hue": hue } as CSSProperties;
 *
 * The cast is the only thing that makes this a style sink, so it has to be the
 * anchor. Matching `return {` instead would accept any function that returns an
 * object containing a quoted `--token` key — which is the false-negative the
 * classifier exists to avoid, just wearing a different shape.
 */
const STYLE_SINK_TRAILING_CAST = /\}\s*as\s+CSSProperties\b/g;

const QUOTED_CUSTOM_PROPERTY = /["'`](--[A-Za-z0-9_-]+)["'`]\s*:/g;

/**
 * Return the inner text of the object literal whose opening brace is the last
 * character of `openIndex`, using brace depth rather than a lazy regex so a
 * nested object (`{ "--x": f({ a: 1 }) }`) does not truncate the match early.
 */
function objectLiteralBody(text, openIndex) {
  let depth = 0;
  for (let i = openIndex; i < text.length; i += 1) {
    const char = text[i];
    if (char === "{") depth += 1;
    else if (char === "}") {
      depth -= 1;
      if (depth === 0) return text.slice(openIndex + 1, i);
    } else if (char === '"' || char === "'" || char === "`") {
      // Skip string contents so a brace inside a string cannot unbalance depth.
      for (let j = i + 1; j < text.length; j += 1) {
        if (text[j] === "\\") { j += 1; continue; }
        if (text[j] === char) { i = j; break; }
      }
    }
  }
  return null;
}

/**
 * Return the inner text of the object literal whose *closing* brace is `closeIndex`,
 * walking backwards with brace depth. Used for the trailing-cast form, where the
 * cast is the reliable anchor and the object start has to be found by matching
 * outwards from the end.
 */
function objectLiteralBodyBefore(text, closeIndex) {
  let depth = 0;
  for (let i = closeIndex; i >= 0; i -= 1) {
    const char = text[i];
    if (char === "}") depth += 1;
    else if (char === "{") {
      depth -= 1;
      if (depth === 0) return text.slice(i + 1, closeIndex);
    }
  }
  return null;
}

function collectInlineStyleDefinitions(text, add, rel) {
  const bodies = [];

  for (const opener of STYLE_SINK_OPENERS) {
    for (const match of text.matchAll(opener)) {
      const openIndex = match.index + match[0].length - 1;
      const body = objectLiteralBody(text, openIndex);
      if (body !== null) bodies.push(body);
    }
  }

  for (const match of text.matchAll(STYLE_SINK_TRAILING_CAST)) {
    const closeIndex = match.index;
    const body = objectLiteralBodyBefore(text, closeIndex);
    if (body !== null) bodies.push(body);
  }

  for (const body of bodies) {
    for (const key of body.matchAll(QUOTED_CUSTOM_PROPERTY)) {
      add(key[1], `inline style in ${rel}`);
    }
  }
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
    for (const match of text.matchAll(/setProperty\(\s*["'`](--[A-Za-z0-9_-]+)["'`]/g)) {
      add(match[1], `setProperty in ${rel}`);
    }
    // A stylesheet declares a property wherever it appears, so the permissive
    // pattern is right there. It is wrong for TS/TSX, where the same shape is
    // usually a plain data key: `{ "--review-ghost": "not a style" }` is an
    // object property, not a definition, and counting it lets a genuinely
    // undefined `var(--review-ghost)` through the gate. Code files therefore
    // contribute only through the style sinks matched below.
    if (CODE_EXTENSIONS.has(extensionOf(file))) {
      collectInlineStyleDefinitions(text, add, rel);
    } else {
      for (const match of text.matchAll(/(?:^|[;{(\s,])(--[A-Za-z0-9_-]+)\s*:/gm)) {
        add(match[1], `declared in ${rel}`);
      }
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
  // a genuine token removal drops the count below it, lower the const in the
  // scope definition in the same change that removes the tokens.
  const definedTokenFloor = scope.definedTokenFloor;
  if (definitions.size < definedTokenFloor) {
    console.error(
      `check-css-custom-properties: only ${definitions.size} defined tokens found under ` +
        `${scope.roots.join(", ")}, below the floor of ${definedTokenFloor}. ` +
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

// Imported by `check-css-custom-properties.test.mjs`, which covers the
// classifier's negative case directly. Running the script still scans.
export { collectDefinitions };

if (import.meta.filename === process.argv[1]) main();
