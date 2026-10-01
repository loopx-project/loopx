// Braces inside a style value must not pull neighbouring data keys into the style.
import type { CSSProperties } from "react";

export const neighbourBefore = { "--fixture-brace-ghost-before": "not a style sink" };

export function withBraces(): CSSProperties {
  return { "--fixture-brace-live": "a}b{c", "--fixture-brace-second": "d}" } as CSSProperties;
}

export const neighbourAfter = { "--fixture-brace-ghost-after": "not a style sink" };
