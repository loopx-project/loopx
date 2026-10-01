/*
 * Positive fixture for `scripts/check-css-custom-properties.test.mjs`.
 *
 * Every style-sink form the classifier must recognise, with the token each one
 * defines listed in the case table of that test. If a form stops being matched,
 * the classifier reports the paired `var()` reference in `sinks.css` as
 * undefined — a false positive on correct code — and the Dashboard's own
 * `goal-activity-view.tsx` (`style={{ "--goal-hue": ... } as CSSProperties}`)
 * starts failing the required gate.
 */
import type { CSSProperties } from "react";

// 1. JSX style attribute.
export const jsxSink = <div style={{ "--fixture-jsx": "1px" }} />;

// 2. JSX style attribute with a trailing cast, the form used in the Dashboard.
export function castSink(hue: number) {
  return { "--fixture-cast": `hsl(${hue} 60% 94%)` } as CSSProperties;
}

// 3. Annotated style constant.
export const annotatedSink: CSSProperties = { "--fixture-annotated": "2px" };

// 4. An object that merely has a `style` key is NOT a sink: nothing proves it
//    is ever bound to an element, so it must not satisfy a reference.
export const unboundStyleLikeData = { style: { "--fixture-object-property": "3px" } };

// 5. Imperative write.
export const imperativeSink = (element: HTMLElement) =>
  element.style.setProperty("--fixture-imperative", "4px");

/*
 * Nothing below is a style sink, even though the quoted-key shape is identical.
 * The paired references in `sinks.css` are deliberately absent: these tokens
 * are listed as NOT defined in the test's case table, which is the reviewer's
 * regression.
 */
export const fixtureThemeMetadata = {
  "--fixture-ghost-data-key": "not an inline style",
};

export const fixtureNestedConfig = {
  tokens: { "--fixture-ghost-nested": "also not an inline style" },
};
