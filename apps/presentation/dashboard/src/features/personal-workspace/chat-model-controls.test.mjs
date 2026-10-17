import assert from "node:assert/strict";
import { test } from "node:test";
import { chatModelControls, modelSelectionFromReadback } from "./chat-model-controls.ts";

const discovery = { model: { selected: "catalog-default", options: [
  { value: "first", label: "First", reasoning_efforts: ["low", "high"] },
  { value: "second", label: "Second", reasoning_efforts: ["low"] },
] } };

test("catalog defaults cannot become an observed model or unknown choice", () => {
  const state = chatModelControls(discovery, "", undefined);
  assert.equal(state.observedModel, null);
  assert.deepEqual(state.modelOptions.map((option) => option.value), ["first", "second"]);
  assert.deepEqual(state.reasoningOptions, []);
});

test("reasoning choices follow the selected model, without inheriting old effort", () => {
  const state = chatModelControls(discovery, "second", undefined,
    { agent_model_id: "first", agent_reasoning_effort: "high" });
  assert.deepEqual(state.reasoningOptions, [{ value: "low", label: "low" }]);
  assert.equal(state.effort, "");
  assert.equal(state.observedModel, "first");
  assert.equal(state.observedEffort, "high");
});

test("ACP-advertised thought level remains separate from observed Session", () => {
  const state = chatModelControls({ model: null, reasoning_effort: {
    selected: "low", options: [{ value: "low", label: "Quick" }, { value: "high", label: "Deep" }],
  } }, "acp-model", "high", { agent_model_id: "acp-model", agent_reasoning_effort: "low" });
  assert.equal(state.effort, "high");
  assert.equal(state.observedEffort, "low");
  assert.equal(state.reasoningOptions[1].label, "Deep");
});

test("unsupported or unavailable reasoning is not replaced with invented options", () => {
  assert.deepEqual(chatModelControls(null, "first", "high").reasoningOptions, []);
  assert.deepEqual(chatModelControls({ model: { options: [{ value: "first" }] } }, "first", undefined).reasoningOptions, []);
});

test("old model readback cannot restore its effort after a different model was picked", () => {
  const update = modelSelectionFromReadback({ agent_model_id: "first", agent_reasoning_effort: "high" }, { model: "second" });
  assert.equal(update.model, undefined);
  assert.equal(update.effort, undefined);
});

test("a pending effort stays selected until the same model confirms it", () => {
  assert.deepEqual(modelSelectionFromReadback({ agent_model_id: "first", agent_reasoning_effort: "low" },
    { model: "first", effort: "high" }), { model: "first", effort: undefined });
  assert.deepEqual(modelSelectionFromReadback({ agent_model_id: "first", agent_reasoning_effort: "high" },
    { model: "first", effort: "high" }), { model: "first", effort: "high" });
});

test("unselected defaults use only the actual Session response, including unknown", () => {
  assert.deepEqual(modelSelectionFromReadback({}, {}), { model: "", effort: "" });
  assert.deepEqual(modelSelectionFromReadback({ agent_model_id: "first", agent_reasoning_effort: "low" }, {}),
    { model: "first", effort: "low" });
});
