import assert from "node:assert/strict";
import {performance} from "node:perf_hooks";
import test from "node:test";

import {canonicalAuthoritySha256, createCanonicalAuthorityDigestWindow} from
  "../../loopx/control_plane/coordination/authority_store_codec.ts";

test("window digest keeps the exact canonical v0 hash across JSON shapes and cache eviction", () => {
  const digest = createCanonicalAuthorityDigestWindow();
  const sparse: unknown[] = [];
  sparse.length = 3;
  sparse[1] = "middle";
  const fixtures: unknown[] = [null, "quote\"slash\\\n", -0, 1.25e30, true, sparse,
    JSON.parse('{"": {"marker": "empty"}, "__proto__": {"marker": "proto"}}'),
    JSON.parse('{"10":1,"2":2,"Ω":"🙂","nested":{"b":1,"a":2}}')];
  for (const value of fixtures) assert.equal(digest(value), canonicalAuthoritySha256(value));
  for (let index = 0; index < 8; index++) {
    const value = {payload: String(index).repeat(1024 * 1024), ordinal: index};
    assert.equal(digest(value), canonicalAuthoritySha256(value));
  }
  assert.throws(() => digest({invalid: Number.NaN}));
});

test("one window reuses stable large JSON strings across many distinct proofs", () => {
  const padding = "p".repeat(1024 * 1024);
  const inputs = Array.from({length: 100}, (_, index) => ({padding, ordinal: index}));
  const digest = createCanonicalAuthorityDigestWindow();
  const baseline = inputs.map(value => canonicalAuthoritySha256(value));
  assert.deepEqual(inputs.map(value => digest(value)), baseline);
  const duration = (hash: (value: unknown) => string) => {
    const samples: number[] = [];
    for (let trial = 0; trial < 3; trial++) {
      const start = performance.now();
      for (const value of inputs) hash(value);
      samples.push(performance.now() - start);
    }
    return samples.sort((left, right) => left - right)[1]!;
  };
  const legacyMs = duration(canonicalAuthoritySha256);
  const cachedMs = duration(digest);
  assert.ok(cachedMs * 2 < legacyMs,
    `window digest ${cachedMs.toFixed(1)}ms must beat repeated canonical encoding ${legacyMs.toFixed(1)}ms`);
});
