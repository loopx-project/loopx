/*
 * Negative fixture: a closing brace inside a style value must not end the object
 * early, and a brace inside a string must not be counted at all.
 *
 * The trailing-cast form is anchored on `as CSSProperties` and walks backwards
 * to the object's opening brace. Its value deliberately carries `}` and `{`
 * characters, so a walk that counts braces inside strings finds the wrong start:
 *
 *   - with the correct walk, the body is the style object, so
 *     `--fixture-brace-live` is defined and the two ghost keys are not;
 *   - with a string-blind walk, the body stops short or spans too far, and
 *     either a ghost key becomes a definition or the real one is lost.
 *
 * `brace.css` references the real key and both ghosts with no fallback, so the
 * scope exits non-zero unless the walk is exactly right.
 */
import type { CSSProperties } from "react";

export const neighbourBefore = { "--fixture-brace-ghost-before": "not a style sink" };

export function withBraces(): CSSProperties {
  return { "--fixture-brace-live": "a}b{c", "--fixture-brace-second": "d}" } as CSSProperties;
}

export const neighbourAfter = { "--fixture-brace-ghost-after": "not a style sink" };
