import assert from "node:assert/strict";
import test from "node:test";

import { effectRuntimeErrorPayload } from "../../loopx/control_plane/effect_runtime_errors.ts";
import {
  assertNever,
  jsonObject,
  optionalNonEmptyString,
  requireBoolean,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
  requireStringLiteral,
} from "../../loopx/control_plane/runtime_decode.ts";

test("runtime decoder accepts valid primitive boundary values", () => {
  const source = { nested: true };
  const strings = ["a", "b"];
  assert.equal(jsonObject(source), source);
  assert.equal(requireJsonObject(source, "value"), source);
  assert.equal(requireBoolean(false, "value"), false);
  assert.equal(requireInteger(3, "value"), 3);
  assert.equal(requireNonEmptyString(" ready ", "value"), " ready ");
  assert.equal(optionalNonEmptyString(null, "value"), null);
  assert.equal(optionalNonEmptyString(undefined, "value"), null);
  assert.equal(optionalNonEmptyString("", "value"), null);
  const decodedStrings = requireStringArray(strings, "value");
  assert.deepEqual(decodedStrings, strings);
  assert.notEqual(decodedStrings, strings);
});

test("runtime decoder rejects malformed primitive boundary values", () => {
  assert.equal(jsonObject([]), null);
  assert.throws(() => requireJsonObject(null, "value"), /value must be an object/);
  assert.throws(() => requireBoolean(1, "value"), /value must be a boolean/);
  assert.throws(() => requireInteger(1.5, "value"), /value must be an integer/);
  assert.throws(
    () => requireNonEmptyString("  ", "value"),
    /value must be a non-empty string/,
  );
  assert.throws(
    () => optionalNonEmptyString("  ", "value"),
    /value must be a non-empty string/,
  );
  assert.throws(
    () => requireStringArray(["ok", 1], "value"),
    /value must be an array of strings/,
  );
});

test("internal exhaustiveness assertions do not become request rejection", () => {
  let caught: unknown;
  try {
    assertNever("unexpected" as never, "internal assertion failed");
  } catch (error) {
    caught = error;
  }
  assert.deepEqual(effectRuntimeErrorPayload(caught), {
    kind: "internal_failure",
    code: "unexpected_handler_error",
    message: "Effect runtime handler failed unexpectedly",
  });
});

test("literal decoder narrows only values present in the runtime allowlist", () => {
  const operations = ["read", "write"] as const;
  const operation = requireStringLiteral(
    "write",
    operations,
    "operation",
  );
  const exact: "read" | "write" = operation;
  assert.equal(exact, "write");
  assert.throws(
    () => requireStringLiteral("delete", operations, "operation"),
    /operation is unsupported/,
  );
});
