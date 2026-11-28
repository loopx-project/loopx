import assert from "node:assert/strict";
import test from "node:test";
import {compactPythonTextLength, compactPythonWhitespace, hasPythonNonWhitespaceText, stripPythonWhitespace} from "../../loopx/control_plane/coordination/python_whitespace.ts";
import {compactPythonWhitespace as agentWhitespace} from "../../loopx/control_plane/coordination/todo_agents.ts";
import {projectTodoHandoffContext} from "../../loopx/control_plane/todos/handoff_projection.ts";
import {compactTodoReadText, pythonJson} from "../../loopx/control_plane/todos/read_codec.ts";

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

test("boundary fast paths preserve non-whitespace controls, surrogates, and credential exclusion", () => {
  for (const cp of [0x21, 0x7f, 0x84, 0xfeff, 0x200b]) {
    const edge = String.fromCharCode(cp);
    const wrapped = `${edge}hello world${edge}`;
    assert.equal(stripPythonWhitespace(wrapped), wrapped);
    assert.equal(compactPythonWhitespace(wrapped), wrapped);
  }
  assert.equal(stripPythonWhitespace(""), "");
  assert.equal(compactPythonWhitespace(""), "");
  assert.equal(compactPythonWhitespace("alpha  beta"), "alpha beta");
  assert.equal(compactPythonWhitespace("🌱  alpha\u0085beta 🌱"), "🌱 alpha beta 🌱");

  assert.equal(compactTodoReadText("a\u00a0b   c", 64), "a b c");
  assert.equal(compactTodoReadText("🌱🌱🌱🌱", 3), "🌱🌱…");
  assert.equal(pythonJson({ok: true, msg: "plain ascii"}), '{"msg":"plain ascii","ok":true}');
  assert.equal(pythonJson({msg: "中"}), '{"msg":"\\u4e2d"}');

  const projected = projectTodoHandoffContext([
    {
      source: "todo",
      metadata: {todo_id: "todo_abc123", claimed_by: "antigravity"},
      handoff: {
        truthy: true,
        fields: {
          note: "Bearer secret-token-123",
          reason: "AP\u0130_KEY=secret-val",
          summary: "Plain clean handoff summary with\u0131",
          suggested_next_action: "Advance   S1  hotpath",
        },
      },
      texts: {},
    },
    {
      source: "todo",
      metadata: {todo_id: "todo_def456", claimed_by: "antigravity"},
      handoff: {
        truthy: true,
        fields: {
          note: "Bearer secret-token-123",
          reason: "AP\u0130_KEY=secret-val",
        },
      },
      texts: {},
    },
  ]);
  const note = projected[0]?.note as Record<string, unknown>;
  assert.equal(note.summary, "Plain clean handoff summary with\u0131");
  assert.equal(note.suggested_next_action, "Advance S1 hotpath");
  assert.equal(projected[1]?.continuation_hint, null);
  const redactedNote = projected[1]?.note as Record<string, unknown>;
  assert.equal(redactedNote.summary, undefined);
});
