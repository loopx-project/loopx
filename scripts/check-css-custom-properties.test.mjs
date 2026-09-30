#!/usr/bin/env node
/**
 * Contract for the definition classifier in `check-css-custom-properties.mjs`.
 *
 * The scan is a required CI gate whose only signal is a non-zero exit, so the
 * way it *fails to fail* matters more than the way it passes. A definition set
 * that is too generous is the dangerous direction: any token it wrongly counts
 * as defined lets a genuinely undefined `var(--token)` through, and the browser
 * then drops that declaration silently. Nothing else catches the regression.
 *
 * The cases below are driven by two committed fixtures, not by strings built in
 * this file, so the contract is reviewable as a diff and the same inputs can be
 * run through the script by hand:
 *
 *   scripts/fixtures/css-custom-properties/sinks.tsx   real style sinks
 *   scripts/fixtures/css-custom-properties/positive.css  their bare references
 *   scripts/fixtures/css-custom-properties/negative/      the look-alike data key
 *   scripts/fixtures/css-custom-properties/negative.css   its bare reference
 *
 * The boundary under test, one row per line:
 *
 *   defined      `style={{ "--x": v }}`            a JSX style sink
 *   defined      `{ … } as CSSProperties`          a cast style sink
 *   defined      `const s: CSSProperties = {...}`  an annotated style constant
 *   defined      `style: { "--x": v }`             a style object property
 *   defined      `el.style.setProperty(...)`       an imperative write
 *   defined      `selector { --x: v }`             a stylesheet declaration
 *   NOT defined  `{ "--x": "data" }`               an ordinary data key
 *
 * The last row is the one that regressed: a theme metadata table, an i18n map or
 * a token catalogue can carry the same quoted key without setting any CSS
 * property, and reading it as a definition waves the real reference through.
 */

import assert from "node:assert/strict";

import { collectDefinitions } from "./check-css-custom-properties.mjs";

const FIXTURES = new URL("./fixtures/css-custom-properties/", import.meta.url);
const fixturePath = (name) => new URL(name, FIXTURES).pathname;

const POSITIVE_FILES = [fixturePath("sinks.tsx"), fixturePath("positive.css")];
const NEGATIVE_FILES = [
  fixturePath("negative/dataKeys.ts"),
  fixturePath("negative.css"),
];

const definitionsFor = (paths) => collectDefinitions(paths).definitions;

// --- Positive direction: every real style sink must be recognised ------------
//
// Each token below is written through one sink form, and `positive.css` carries a
// bare `var()` for it. Missing any one of these turns correct code into a gate
// failure — which is how a required check gets muted.
{
  const definitions = definitionsFor(POSITIVE_FILES);
  const expected = {
    "--fixture-jsx": "a JSX style attribute",
    "--fixture-cast": "a trailing `as CSSProperties` cast",
    "--fixture-annotated": "a CSSProperties-annotated constant",
    "--fixture-object-property": "a `style:` object property",
    "--fixture-imperative": "a setProperty call",
  };
  for (const [token, form] of Object.entries(expected)) {
    assert.ok(definitions.has(token), `${form} defines ${token}`);
  }
}

// --- Negative direction: a data key is not a definition ---------------------
//
// The reviewer's P1 case. `negative/dataKeys.ts` carries `--fixture-ghost` as an
// ordinary object key and `negative.css` references it bare; the reference must
// stay undefined so the scope exits non-zero.
{
  const definitions = definitionsFor(NEGATIVE_FILES);
  assert.ok(
    !definitions.has("--fixture-ghost"),
    "an ordinary data key does not define a custom property, so a bare var() reference to it stays undefined",
  );
}

// --- The two directions in one file ----------------------------------------
//
// `sinks.tsx` carries the ghost keys beside the real sinks. Only the sinks may
// count; if the classifier scanned every quoted key, the ghosts would appear too.
{
  const definitions = definitionsFor(POSITIVE_FILES);
  assert.ok(
    !definitions.has("--fixture-ghost-data-key"),
    "a data key beside real style sinks stays out of the definitions",
  );
  assert.ok(
    !definitions.has("--fixture-ghost-nested"),
    "a nested config key beside real style sinks stays out of the definitions",
  );
}

// --- Stylesheet declarations still count ------------------------------------
//
// The plain CSS form is the one the gate exists for, and it must survive the
// narrowing that fixed the regression. `negative.css` declares nothing, so it
// also proves a references-only stylesheet yields an empty definition set
// instead of an error or a phantom token.
{
  const declared = collectDefinitions([fixturePath("positive.css")]).definitions;
  assert.ok(
    !declared.has("--fixture-ghost"),
    "a stylesheet that only references a token does not define it",
  );

  const negativeDeclarations = definitionsFor([fixturePath("negative.css")]);
  assert.equal(
    negativeDeclarations.size,
    0,
    "a references-only stylesheet produces no definitions",
  );
}

// --- Anti-vacuity: the stylesheet direction must actually match -------------
//
// Without this, a classifier that matched nothing at all would still satisfy
// every negative assertion above while silently disabling the gate. The positive
// fixture carries both stylesheet declarations and sink-defined tokens, so the
// scan has to produce definitions from more than one source.
{
  const fromSinksAndSheet = definitionsFor(POSITIVE_FILES);
  const fromSheetOnly = collectDefinitions([fixturePath("positive.css")]).definitions;
  assert.ok(
    fromSinksAndSheet.size > fromSheetOnly.size,
    "inline sinks add definitions beyond the stylesheet declarations alone",
  );
}

console.log("check-css-custom-properties classifier contract: ok");
