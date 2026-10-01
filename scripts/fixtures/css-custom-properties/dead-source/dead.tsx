/*
 * Negative fixture: text that is not executable must not define a token.
 *
 * `dead-source.css` references all four tokens below with no fallback. None of
 * them is set on an element:
 *
 *   - `--fixture-commented` appears inside a line comment;
 *   - `--fixture-block-commented` inside a block comment;
 *   - `--fixture-stringified` inside a string literal;
 *   - `--fixture-unbound` in an ordinary `style`-shaped data object that never
 *     reaches a DOM node.
 *
 * A classifier that reads raw source or that skips only block comments lets
 * these satisfy the references, and the required gate exits 0 for declarations
 * the browser will drop. The whole scope must exit non-zero.
 */
export const live = 1;

// const sink = <div style={{ "--fixture-commented": "1px" }} />;

/* const other = <div style={{ "--fixture-block-commented": "1px" }} />; */

export const docs = 'a note about style: { "--fixture-stringified": "1px" }';

export const unboundConfig = { style: { "--fixture-unbound": "never applied" } };
