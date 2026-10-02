import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import test from "node:test";
import {authorityUnicodeCompare, canonicalAuthorityBytes, canonicalAuthoritySha256, copyAuthorityJson}
  from "../../loopx/control_plane/coordination/authority_store_codec.ts";

// Frozen pre-optimization comparator: persisted revisions depend on code points,
// including unpaired surrogates, rather than the default UTF-16 sort order.
function referenceCompare(a: string, b: string): number {
  const x = Array.from(a, c => c.codePointAt(0)!);
  const y = Array.from(b, c => c.codePointAt(0)!);
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    if (x[i] !== y[i]) return x[i]! - y[i]!;
  }
  return x.length - y.length;
}

test("canonical key ordering preserves the persisted Unicode comparator", () => {
  const alphabet = ["", "a", "\0", "é", "中", "\ud800", "\udfff", "\ue000", "😀", "𐀀", "\u{10ffff}"];
  const keys = [...alphabet, ...alphabet.flatMap(a => alphabet.map(b => a + b))];
  for (const a of keys) for (const b of keys) {
    assert.equal(Math.sign(authorityUnicodeCompare(a, b)), Math.sign(referenceCompare(a, b)));
  }
  assert.deepEqual([...keys].sort(authorityUnicodeCompare), [...keys].sort(referenceCompare));
});

test("copying JSON defines own data even when a key names an inherited setter", () => {
  const key = "authorityCopySentinel";
  const previous = Object.getOwnPropertyDescriptor(Object.prototype, key);
  let setterCalls = 0;
  Object.defineProperty(Object.prototype, key, {
    configurable: true, set: () => { setterCalls++; },
  });
  try {
    const input = JSON.parse('{"authorityCopySentinel":{"nested":1},"constructor":{"marker":2}}');
    const copy = copyAuthorityJson(input) as Record<string, unknown>;
    assert.equal(setterCalls, 0);
    assert.deepEqual(copy, input);
    assert.equal(Object.getPrototypeOf(copy), Object.prototype);
    assert.equal(Object.hasOwn(copy, key), true);
    (copy[key] as {nested: number}).nested = 99;
    assert.deepEqual(input[key], {nested: 1});
  } finally {
    if (previous) Object.defineProperty(Object.prototype, key, previous);
    else Reflect.deleteProperty(Object.prototype, key);
  }
});

test("canonical bytes and digest retain JSON enumeration, scalar and special-key semantics", () => {
  const input = JSON.parse('{"😀":4,"\\ue000":3,"__proto__":{"z":2,"a":1},"2":2,"10":10,"":0}');
  input.scalars = [-0, 1e30, "\ud800", true, null];
  input.sparse = Array(2);
  const expected = '{"2":2,"10":10,"":0,"__proto__":{"a":1,"z":2},"scalars":[0,1e+30,"\\ud800",true,null],"sparse":[null,null],"\ue000":3,"😀":4}';
  assert.equal(canonicalAuthorityBytes(input).toString(), expected);
  assert.equal(canonicalAuthoritySha256(input), createHash("sha256").update(expected).digest("hex"));
  assert.equal(Object.getPrototypeOf(input), Object.prototype);
  const cyclic: Record<string, unknown> = {}; cyclic.self = cyclic;
  for (const invalid of [cyclic, {n: NaN}, {n: Infinity}, {x: undefined}, {x: new Date()}, [undefined]]) {
    assert.throws(() => canonicalAuthorityBytes(invalid));
  }
});
