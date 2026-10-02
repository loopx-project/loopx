import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  CONNECTED_CREDENTIAL_VALUE_SHAPE,
  CREDENTIAL_WORD_PATTERNS,
  INTERNAL_STATE_PRIVATE_TEXT_PATTERNS,
  INTERNAL_STATE_SHAPE_PATTERNS,
  OPAQUE_VALUE_MIN_LENGTH,
  PRIVATE_TEXT_PATTERNS,
  QUOTED_CREDENTIAL_VALUE_SHAPE,
  VISION_REFRESH_REQUEST_SCHEMA,
  buildVisionCheckpoint,
} from "../../loopx/control_plane/goals/vision_checkpoint.ts";

// The Python owners (loopx/public_safe_text.py) and this TypeScript owner must
// agree on the same corpus. The Python parity test drives this file through
// refresh-state; this test pins the TypeScript owner on its own so the
// contract cannot drift when only the control-plane suite runs.
const CORPUS_PATH = fileURLToPath(
  new URL("../fixtures/public_safe_text_corpus.json", import.meta.url),
);

interface CorpusSample {
  id: string;
  template: string;
  shape?: string;
  note?: string;
}

interface Corpus {
  schema_version: string;
  contract_signals: Record<string, string>;
  tokens: Record<string, string[]>;
  public_safe: CorpusSample[];
  private_looking: CorpusSample[];
  internal_state_prose: CorpusSample[];
}

const corpus = JSON.parse(readFileSync(CORPUS_PATH, "utf8")) as Corpus;
assert.equal(corpus.schema_version, "public_safe_text_corpus_v2");

function render(template: string): string {
  return template.replace(/\{([A-Z][A-Z_]*)\}/g, (_match, name: string) => {
    const direct = corpus.tokens[name];
    if (direct !== undefined) return direct.join("");
    const lowerSuffix = "_LOWER";
    if (name.endsWith(lowerSuffix)) {
      const base = corpus.tokens[name.slice(0, -lowerSuffix.length)];
      if (base !== undefined) return base.join("").toLowerCase();
    }
    throw new Error(`corpus placeholder ${name} has no token definition`);
  });
}

function checkpoint(text: string): void {
  buildVisionCheckpoint({
    schema_version: VISION_REFRESH_REQUEST_SCHEMA,
    phase: "finalize",
    agent_id: "kiro-cli",
    agent_vision: null,
    existing_agent_vision: null,
    vision_unchanged_reason: text,
    delivery_outcome: "outcome_progress",
    active_state_next_action_would_update: false,
    delivery_boundary: null,
    todo_id: null,
    completion_todo_id: null,
    autonomous_replan_recorded: false,
  });
}

test("vision checkpoint accepts every public-safe corpus sample", () => {
  for (const sample of corpus.public_safe) {
    assert.doesNotThrow(() => checkpoint(render(sample.template)), sample.id);
  }
});

test("vision checkpoint rejects every private-looking corpus sample", () => {
  for (const sample of corpus.private_looking) {
    assert.throws(
      () => checkpoint(render(sample.template)),
      /private-looking value/,
      sample.id,
    );
  }
});

// Refs #5136, direction 2: this owner validates LoopX's own state, so a bare
// credential word is a fact about a credential rather than one. The Python tier
// test drives the same bucket through the three Python owners.
test("vision checkpoint accepts the internal-state prose corpus", () => {
  for (const sample of corpus.internal_state_prose) {
    assert.doesNotThrow(() => checkpoint(render(sample.template)), sample.id);
  }
});

test("the internal-state tier drops the words and adds the ported shapes", () => {
  // Without this, dropping a word arm and dropping a value arm would look the
  // same from the corpus alone. The composition is pinned, not the count.
  const wordSources = CREDENTIAL_WORD_PATTERNS.map((pattern) => pattern.source);
  assert.deepEqual(
    wordSources,
    [/\bBearer\b/i, /\bpassword\b/i, /\bsecret\b/i].map((pattern) => pattern.source),
  );
  assert.equal(INTERNAL_STATE_SHAPE_PATTERNS.length, 2);
  const expected = [
    ...PRIVATE_TEXT_PATTERNS.filter(
      (pattern) => !CREDENTIAL_WORD_PATTERNS.includes(pattern),
    ),
    ...INTERNAL_STATE_SHAPE_PATTERNS,
  ].map((pattern) => pattern.source);
  assert.deepEqual(
    INTERNAL_STATE_PRIVATE_TEXT_PATTERNS.map((pattern) => pattern.source),
    expected,
  );
});

test("every corpus row declares a contract signal and every signal has a row", () => {
  // The fixture is the contract both runtimes are pinned to, so its structure is
  // checked here as well as in Python: a signal nobody samples, or a row whose
  // verdict its own signal does not name, fails in both suites.
  const signals = new Set(Object.keys(corpus.contract_signals));
  const declared = new Set<string>();
  for (const group of ["public_safe", "private_looking", "internal_state_prose"] as const) {
    for (const sample of corpus[group]) {
      assert.ok(typeof sample.shape === "string", `${sample.id} declares no signal`);
      assert.ok(signals.has(sample.shape), `${sample.id} names unknown signal ${sample.shape}`);
      declared.add(sample.shape);
    }
  }
  assert.deepEqual([...declared].sort(), [...signals].sort());
});

test("the letter-run ceiling is the named constant the pattern carries", () => {
  // The connector arms are literals so the digest guard can fold them, which means
  // the ceiling has to be checked against the source rather than used to build it.
  const ceiling = new RegExp(`\\{${OPAQUE_VALUE_MIN_LENGTH},\\}`);
  assert.ok(ceiling.test(CONNECTED_CREDENTIAL_VALUE_SHAPE.source));
  const below = `Bearer ${"a".repeat(OPAQUE_VALUE_MIN_LENGTH - 1)}`;
  const at = `Bearer ${"a".repeat(OPAQUE_VALUE_MIN_LENGTH)}`;
  assert.ok(!CONNECTED_CREDENTIAL_VALUE_SHAPE.test(below), below);
  assert.ok(CONNECTED_CREDENTIAL_VALUE_SHAPE.test(at), at);
  // Shape signals need no length at all, which is what retired the bearer floor.
  assert.ok(CONNECTED_CREDENTIAL_VALUE_SHAPE.test("Bearer 1"));
  assert.ok(CONNECTED_CREDENTIAL_VALUE_SHAPE.test("Bearer +"));
  assert.ok(CONNECTED_CREDENTIAL_VALUE_SHAPE.test("Bearer abc123"));
  assert.ok(QUOTED_CREDENTIAL_VALUE_SHAPE.test('Bearer is "a"'));
  assert.ok(QUOTED_CREDENTIAL_VALUE_SHAPE.test(`Bearer is "${"a".repeat(8)}"`));
});

test("the narrower tier still rejects every value and assignment shape", () => {
  // Each value below is one of the demoted labels carrying something, decided by
  // which contract signal it expresses. Assembling them keeps the fixture's
  // discipline of carrying no literal credential text.
  const bearer = "Bear" + "er";
  const password = "pass" + "word";
  const secret = "sec" + "ret";
  const token = "tok" + "en";
  const rejected = [
    `${bearer} abc123def456`,
    `${bearer} 1`,
    `${bearer} abc123de`,
    `${bearer}, aB3d9QkLm`,
    `${bearer} is abc123`,
    `${bearer} ${"a".repeat(OPAQUE_VALUE_MIN_LENGTH)}`,
    `${bearer} ${"a".repeat(OPAQUE_VALUE_MIN_LENGTH + 24)}`,
    `${password}=hunter2`,
    `{"${password}": 1}`,
    `{"${secret}": "a"}`,
    `{'${password}': 'a'}`,
    `{"${secret}": }`,
    `${password} is hunter2`,
    `${password} set to hunter2`,
    `${password} is "a"`,
    `${password} set to "a"`,
    `${bearer} set to ${"a".repeat(OPAQUE_VALUE_MIN_LENGTH)}`,
    `${password} is "${"a".repeat(8)}"`,
    `${secret}: env`,
    `${secret} - Qz8m2Xp7`,
    `${token}: abc123`,
    `${token} abc123def`,
    "/Us" + "ers/operator/state.json",
  ];
  for (const value of rejected) {
    assert.throws(() => checkpoint(value), /private-looking value/, value.slice(0, 24));
  }
  const accepted = [
    `the ${bearer} token expired`,
    `the ${password} is stored in the vault`,
    `the "${password}" field stays unset`,
    `read the ${secret} from the environment`,
    `${bearer} authentication is required here`,
    `the ${password} is configured per environment`,
    `the ${password} set to rotate after expiry`,
    `${secret} is ${"a".repeat(OPAQUE_VALUE_MIN_LENGTH - 1)}`,
  ];
  for (const value of accepted) {
    assert.doesNotThrow(() => checkpoint(value), value.slice(0, 24));
  }
});
