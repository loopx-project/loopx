/*
 * Negative fixture: the reviewer's P1 case.
 *
 * `negative.css` references `--fixture-ghost` with no fallback. The only place
 * that token appears is the ordinary data key below — never a style sink. The
 * check must therefore report it and exit non-zero.
 *
 * This is what the bug looks like in real code: a theme metadata table, an i18n
 * catalogue, or a token listing can carry a quoted `"--token":` key without ever
 * setting a CSS property. Reading such a key as a definition subtracts the
 * reference from the undefined set and the gate exits 0, so a genuine CSS typo
 * hides behind unrelated data.
 *
 * `sinks.tsx` in the sibling directory holds the same key shape inside a real
 * style sink. The pair is the whole contract: identical syntax, opposite meaning,
 * decided by whether the key lands in a style sink.
 */
export const fixtureThemeMetadata = {
  "--fixture-ghost": "not an inline style",
};
