import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import postcss from "postcss";

/**
 * Contract for the font and monospace tokens the Dashboard stylesheets reference.
 *
 * `docs/development/design.md` names `--font-sans` and `--font-mono` as the
 * canonical families, so stylesheets reference them instead of repeating a
 * stack. That only works while both tokens are actually defined: a bare
 * `var(--font-mono)` with no fallback makes the browser drop the whole
 * declaration, and the element silently inherits the body face. Code blocks and
 * monospace metadata then render as sans-serif with nothing to notice.
 *
 * `scripts/check-css-custom-properties.mjs` guards the general case (any bare
 * reference to any undefined token). This test pins the specific decision: what
 * the two font tokens are defined as, and that the sites that regressed keep
 * resolving through them rather than through a locally repeated stack.
 */

const dashboardStyles = readFileSync(new URL("../../styles.css", import.meta.url), "utf8");
const workspaceStyles = readFileSync(new URL("./personal-workspace.css", import.meta.url), "utf8");
const loopxModeStyles = readFileSync(new URL("./goal-loopx-mode.css", import.meta.url), "utf8");
const collaborationStyles = readFileSync(new URL("./collaboration-card.css", import.meta.url), "utf8");
const deliveryStyles = readFileSync(new URL("./delivery-review.css", import.meta.url), "utf8");

// Tokens must be defined at the root, with Geist first and the approved CJK
// families available before the final generic fallback.
function assertFontTokens(source) {
  const tokens = new Map();
  postcss.parse(source).walkRules(":root", (rule) => {
    rule.walkDecls(/^--font-(sans|mono)$/, (decl) => tokens.set(decl.prop, decl.value));
  });
  for (const [name, prefix, generic] of [
    ["--font-sans", /^"Geist Variable",\s*"Geist",/, "sans-serif"],
    ["--font-mono", /^"Geist Mono Variable",\s*"Geist Mono",\s*ui-monospace,/, "monospace"],
  ]) {
    const value = tokens.get(name);
    assert.ok(value, `${name} must be defined on :root`);
    assert.match(value, prefix, `${name} leads with the canonical Geist family`);
    assert.match(
      value,
      new RegExp(`"PingFang SC",\\s*"Microsoft YaHei",\\s*"Noto Sans CJK SC",[\\s\\S]*\\b${generic}$`),
      `${name} includes the approved CJK fallbacks before the generic family`,
    );
  }
}
assertFontTokens(dashboardStyles);
assert.match(
  dashboardStyles,
  /font-family: var\(--font-sans\)/,
  "The document body face resolves through the sans token",
);

// Sites that previously regressed must reference the token, not a repeated stack.
assert.ok(
  deliveryStyles.includes(".delivery-acceptance-content code { font-family: var(--font-mono);"),
  "Delivery review code resolves through the mono token",
);
assert.ok(
  collaborationStyles.includes(".personal-collaboration small { display: block; font-family: var(--font-mono);"),
  "Collaboration card metadata resolves through the mono token",
);
assert.ok(
  workspaceStyles.includes(".personal-operator-credential-status { color: var(--pw-muted); font-family: var(--font-mono);"),
  "Operator credential status resolves through the mono token, not a misspelled private one",
);

// Goal code/evidence uses the defined shared token through either font syntax.
function assertGoalMonoSurfaces(source) {
  const stylesheet = postcss.parse(source);
  for (const selector of [".goal-team-work code", ".goal-team-evidence pre", ".goal-team-results pre"]) {
    let font;
    stylesheet.walkRules(selector, (rule) => {
      rule.walkDecls(/^(font|font-family)$/, (decl) => { font = decl.value; });
    });
    assert.ok(font, `${selector} declares its code font`);
    assert.match(font, /var\(--font-mono(?:\s*,|\s*\))/, `${selector} resolves through the shared mono token`);
  }
}
assertGoalMonoSurfaces(loopxModeStyles);

// Where an optional fallback is given, it must be the same family — a bare `monospace`
// keyword would quietly render a different face from every other code surface.
const fallbacks = [...loopxModeStyles.matchAll(/var\(--font-mono,\s*([^)]*)\)/g)].map((match) => match[1]);
for (const fallback of fallbacks) {
  assert.match(
    fallback,
    /"Geist Mono Variable"/,
    `The mono fallback names the Geist Mono family rather than a bare keyword: ${fallback}`,
  );
}

// A missing root token or a code declaration changed to sans must fail even
// when every other font declaration remains valid.
assert.throws(
  () => assertFontTokens(dashboardStyles.replace(/--font-mono\s*:[^;]+;/, "")),
  /--font-mono must be defined on :root/,
);
assert.throws(
  () => assertGoalMonoSurfaces(loopxModeStyles.replace(/var\(--font-mono\)/g, "var(--font-sans)")),
  /resolves through the shared mono token/,
);

// The private token names that had *bare* references and no definition must not
// come back. `--pw-surface` is deliberately not listed: it stays valid as an
// optional token because `.personal-manager-team-result` supplies a fallback
// (`var(--pw-surface, #fff)`), so the general check — which only rejects bare
// references — is the right guard for it.
//
// Match on a token boundary so `--pw-surface-soft`, a different real token,
// cannot be mistaken for a dead name.
const escapeForRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
for (const [name, source] of [
  ["--pw-font-mono", workspaceStyles],
  ["--pw-danger", workspaceStyles],
  ["--pw-border", workspaceStyles],
  ["--pw-canvas", workspaceStyles],
]) {
  const pattern = new RegExp(`${escapeForRegExp(name)}(?![A-Za-z0-9_-])`);
  assert.ok(
    !pattern.test(source),
    `${name} was never defined anywhere; use the existing token instead of reintroducing it`,
  );
}

// The bare `var(--pw-surface)` in the operator credential was the regression;
// the optional use with a fallback is legitimate and must stay.
assert.ok(
  !workspaceStyles.includes("background: var(--pw-surface);"),
  "The operator credential reads the card token rather than the optional surface token",
);

console.log("font-token contract: ok");
