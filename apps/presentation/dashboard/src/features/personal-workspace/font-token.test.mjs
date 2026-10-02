import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

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

// The two font tokens exist, and the body face is expressed through the sans token.
assert.match(
  dashboardStyles,
  /--font-sans:\s*\n?\s*"Geist Variable", "Geist", Inter,/,
  "The sans token is defined from the Geist Variable family",
);
assert.match(
  dashboardStyles,
  /--font-mono: "Geist Mono Variable", "Geist Mono", ui-monospace,/,
  "The mono token is defined from the Geist Mono family with platform fallbacks",
);
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

// Where a fallback is given, it must be the same family — a bare `monospace`
// keyword would quietly render a different face from every other code surface.
const fallbacks = [...loopxModeStyles.matchAll(/var\(--font-mono,\s*([^)]*)\)/g)].map((match) => match[1]);
assert.ok(fallbacks.length > 0, "Goal LoopX mode keeps an explicit mono fallback");
for (const fallback of fallbacks) {
  assert.match(
    fallback,
    /"Geist Mono Variable"/,
    `The mono fallback names the Geist Mono family rather than a bare keyword: ${fallback}`,
  );
}

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
