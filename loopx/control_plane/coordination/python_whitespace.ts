// Python str.split() and isspace() recognize exactly 29 Unicode whitespace
// code points. ECMAScript \s omits U+001C..U+001F and U+0085 while including
// U+FEFF, which Python treats as content. Keep this pure boundary usable by
// both authority rules and browser inputs without importing server IO.
const PYTHON_WHITESPACE_CLASS =
  "[\\t\\n\\v\\f\\r \\u001c-\\u001f\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
const PYTHON_LEADING_TRAILING_WHITESPACE = new RegExp(
  `^${PYTHON_WHITESPACE_CLASS}+|${PYTHON_WHITESPACE_CLASS}+$`,
  "gu",
);
const PYTHON_WHITESPACE_RUN = new RegExp(`${PYTHON_WHITESPACE_CLASS}+`, "gu");

/** Strip exactly the characters removed by Python str.strip(). */
export function stripPythonWhitespace(value: string): string {
  return value.replace(PYTHON_LEADING_TRAILING_WHITESPACE, "");
}

/** Match Python's ``" ".join(str(value).strip().split())`` text compaction. */
export function compactPythonWhitespace(value: string): string {
  return stripPythonWhitespace(value).replace(PYTHON_WHITESPACE_RUN, " ");
}

/** Match Python's bool(str(value).strip()) presence contract. */
export function hasPythonNonWhitespaceText(value: string): boolean {
  return stripPythonWhitespace(value).length > 0;
}

/** Python len() counts Unicode code points after the write normalizer runs. */
export function compactPythonTextLength(value: string): number {
  return Array.from(compactPythonWhitespace(value)).length;
}
