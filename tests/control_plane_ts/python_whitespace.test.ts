import assert from "node:assert/strict";
import test from "node:test";
import {compactPythonTextLength, compactPythonWhitespace, hasPythonNonWhitespaceText, stripPythonWhitespace} from "../../loopx/control_plane/coordination/python_whitespace.ts";
import {compactPythonWhitespace as agentWhitespace} from "../../loopx/control_plane/coordination/todo_agents.ts";

test("browser and authority text compaction retain Python's whitespace contract", () => {
  // The documented Python whitespace set, including the control separators
  // absent from ECMAScript's \s. BOM and zero-width space remain user content.
  const whitespace = "\t\n\v\f\r \u001c\u001d\u001e\u001f\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000";
  for (const char of whitespace) {
    assert.equal(compactPythonWhitespace(`${char}a${char}${char}b${char}`), "a b");
    assert.equal(stripPythonWhitespace(`${char}a${char}b${char}`), `a${char}b`);
    assert.equal(hasPythonNonWhitespaceText(char), false);
  }
  for (const char of ["\ufeff", "\u200b"]) {
    assert.equal(compactPythonWhitespace(` ${char} `), char);
    assert.equal(hasPythonNonWhitespaceText(char), true);
  }
  assert.equal(agentWhitespace, compactPythonWhitespace, "Existing authority callers retain the same pure owner");
});

test("input limits count normalized code points without editing the raw draft", () => {
  for (const limit of [400, 1000, 8000]) {
    for (const count of [limit - 1, limit, limit + 1]) {
      const raw = `\u0085${"🌱".repeat(count)}\u001c`;
      assert.equal(compactPythonTextLength(raw), count);
      assert.equal(raw.startsWith("\u0085"), true);
      assert.equal(raw.endsWith("\u001c"), true);
    }
  }
  assert.equal(compactPythonTextLength(`a${"\u0085".repeat(500)}b`), 3);
  assert.equal(compactPythonTextLength(`a${"\ufeff".repeat(500)}b`), 502);
  assert.equal(compactPythonTextLength("e\u0301"), 2, "The owner counts code points, not graphemes");
  assert.equal(compactPythonTextLength("👨‍👩‍👧‍👦"), 7);
});
