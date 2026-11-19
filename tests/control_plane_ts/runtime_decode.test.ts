import assert from "node:assert/strict";
import test from "node:test";

import { effectRuntimeErrorPayload } from "../../loopx/control_plane/effect_runtime_errors.ts";
import {
  assertNever,
  jsonObject,
  optionalNonEmptyString,
  parseJsonSource,
  parsedSourceNumberTokens,
  requireBoolean,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
  requireStringLiteral,
} from "../../loopx/control_plane/runtime_decode.ts";

test("native source numbers retain original spellings across nested object and array reads", () => {
  const parsed = parseJsonSource('{"plan":{"work_contract":{"measurements":[1.0,-0.0,1152921504606846977,0.125,2],"1":2.0}},"ignored":3.0}');
  assert.deepEqual(parsedSourceNumberTokens(parsed.value, parsed.numbers), [
    [["plan", "work_contract", "1"], "2.0"],
    [["plan", "work_contract", "measurements", 0], "1.0"],
    [["plan", "work_contract", "measurements", 1], "-0.0"],
    [["plan", "work_contract", "measurements", 2], "1152921504606846977"],
    [["ignored"], "3.0"],
  ]);
  const contract = requireJsonObject(requireJsonObject(requireJsonObject(parsed.value, "root").plan, "plan").work_contract, "contract");
  assert.deepEqual(parsedSourceNumberTokens(contract, parsed.numbers), [
    [["1"], "2.0"],
    [["measurements", 0], "1.0"], [["measurements", 1], "-0.0"],
    [["measurements", 2], "1152921504606846977"],
  ]);
  assert.deepEqual(parsedSourceNumberTokens(contract), []);
  assert.deepEqual(parsedSourceNumberTokens(null, parsed.numbers), []);
  assert.throws(() => parseJsonSource('{"plan":'), SyntaxError);
});

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
