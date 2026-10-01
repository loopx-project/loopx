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
import { createRequire } from "node:module";
import ts from "typescript";

const REPO_ROOT = resolve(import.meta.dirname, "..");
const requireDashboard = createRequire(join(REPO_ROOT, "apps/presentation/dashboard/package.json"));
const postcss = requireDashboard("postcss");
const parseValue = requireDashboard("postcss-value-parser");

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
  // Fixture scopes so the contract test can run the **shipped command** over
  // committed inputs. A gate whose only signal is an exit code has to be tested
  // on the exit code, not only on an exported function.
  "fixture-clean": {
    // A stylesheet reference satisfied by a real JSX/cast style sink.
    roots: ["scripts/fixtures/css-custom-properties/clean"],
    allowlistedFiles: new Set(),
    definedTokenFloor: 1,
  },
  "fixture-dead-source": {
    // References the only commented-out, stringified or unbound text mentions.
    roots: ["scripts/fixtures/css-custom-properties/dead-source"],
    allowlistedFiles: new Set(),
    definedTokenFloor: 0,
  },
  "fixture-brace-value": {
    // A closing brace inside a style value must not end the object early.
    roots: ["scripts/fixtures/css-custom-properties/brace-value"],
    allowlistedFiles: new Set(),
    definedTokenFloor: 0,
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

function stylesheet(text, file) {
  return postcss.parse(text, { from: relative(REPO_ROOT, file) });
}

/**
 * This is a syntactic inventory, not a reachability or cascade analysis.
 * Only direct properties of JSX style objects, CSSProperties-typed objects,
 * and literal element.style.setProperty calls count. The existing TypeScript
 * parser owns lexical boundaries: comments, strings and regex literals cannot
 * turn their contents into declarations or calls. Template expressions are code.
 */
function collectInlineStyleDefinitions(text, add, rel) {
  const source = ts.createSourceFile(rel, text, ts.ScriptTarget.Latest, true);
  if (source.parseDiagnostics.length) {
    const diagnostic = source.parseDiagnostics[0];
    const { line } = source.getLineAndCharacterOfPosition(diagnostic.start ?? 0);
    throw new Error(`${rel}:${line + 1}: ${ts.flattenDiagnosticMessageText(diagnostic.messageText, " ")}`);
  }
  const customProperty = (name) => /^--[A-Za-z0-9_-]+$/.test(name);
  const literal = (node) => node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
    ? node.text : undefined;
  const member = (node) => ts.isPropertyAccessExpression(node) ? node.name.text
    : ts.isElementAccessExpression(node) ? literal(node.argumentExpression) : undefined;
  const cssProperties = (type) => type && ts.isTypeReferenceNode(type) && (
    ts.isIdentifier(type.typeName) ? type.typeName.text === "CSSProperties"
      : type.typeName.getText(source) === "React.CSSProperties"
  );
  const unwrap = (node) => {
    while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node)
      || ts.isTypeAssertionExpression(node) || ts.isSatisfiesExpression(node))) node = node.expression;
    return node;
  };
  const collectObject = (expression) => {
    const object = unwrap(expression);
    if (!object || !ts.isObjectLiteralExpression(object)) return;
    for (const property of object.properties) {
      if (!ts.isPropertyAssignment(property)) continue;
      const name = literal(ts.isComputedPropertyName(property.name) ? property.name.expression : property.name);
      if (name && customProperty(name)) add(name, `inline style in ${rel}`);
    }
  };
  const visit = (node) => {
    if (ts.isJsxAttribute(node) && node.name.getText(source) === "style"
      && node.initializer && ts.isJsxExpression(node.initializer)) {
      collectObject(node.initializer.expression);
    } else if (ts.isVariableDeclaration(node) && cssProperties(node.type)) {
      collectObject(node.initializer);
    } else if ((ts.isAsExpression(node) || ts.isTypeAssertionExpression(node)
      || ts.isSatisfiesExpression(node)) && cssProperties(node.type)) {
      collectObject(node.expression);
    } else if (ts.isCallExpression(node) && member(node.expression) === "setProperty"
      && member(node.expression.expression) === "style" && node.arguments.length >= 2) {
      const name = literal(node.arguments[0]);
      if (name && customProperty(name)) add(name, `setProperty in ${rel}`);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
}

/**
 * Definitions, per file.
 *
 * Definition forms that occur in this repository:
 *   1. inside a rule block        `.selector { --x: value; }`
 *   2. registered custom property  `@property --x { ... }`
 *   3. inline style object         `style={{ "--x": value }}`
 *   4. el.style.setProperty("--x", ...) imperative
 *
 * Definitions are collected globally rather than per-cascade-scope. A token
 * defined on `.personal-workspace-shell` and referenced inside it is correct,
 * and proving that statically needs a cascade engine. This inventory does not
 * prove that a definition is reachable or inherited at each reference.
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
    if (CODE_EXTENSIONS.has(extensionOf(file))) {
      collectInlineStyleDefinitions(raw, add, rel);
    } else {
      const ast = stylesheet(raw, file);
      ast.walkAtRules("property", (rule) => {
        if (/^--[A-Za-z0-9_-]+$/.test(rule.params.trim())) add(rule.params.trim(), `@property in ${rel}`);
      });
      ast.walkDecls((declaration) => {
        if (declaration.prop.startsWith("--")) add(declaration.prop, `declared in ${rel}`);
      });
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
    stylesheet(readFileSync(file, "utf8"), file).walkDecls((declaration) => {
      const value = declaration.raws.value?.raw ?? declaration.value;
      parseValue(value).walk((node) => {
        if (node.type !== "function" || node.value !== "var") return;
        if (node.nodes.some((part) => part.type === "div" && part.value === ",")) return;
        const name = node.nodes.filter((part) => part.type !== "space" && part.type !== "comment");
        if (name.length !== 1 || name[0].type !== "word" || !name[0].value.startsWith("--")) return;
        references.push({ token: name[0].value, file: rel,
          line: declaration.source.start.line + value.slice(0, node.sourceIndex).split("\n").length - 1 });
      });
    });
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
