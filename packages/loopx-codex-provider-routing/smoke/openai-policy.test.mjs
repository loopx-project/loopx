import assert from "node:assert/strict";
import {test} from "node:test";
import {apiKeys, apiOutcome} from "../src/loopx_codex_provider_routing/openai_policy.ts";

test("known quota rejection rotates a key; unknown effects and ordinary request failures do not", () => {
  const input={now:1000,status:429,code:"insufficient_quota",retry_after:null,model:"selected-model"};
  assert.equal(apiOutcome(input).state,"quota_exhausted");
  assert.equal(apiOutcome(input).retry_next_key,true);
  for(const status of [400,408,500,502,503]) assert.equal(apiOutcome({...input,status}).retry_next_key,false);
  assert.equal(apiOutcome({...input,status:200}).state,"accepted","HTTP success is not completed inference");
  assert.equal(apiOutcome({...input,code:"rate_limit_exceeded",retry_after:60}).blocked_until,1060);
  assert.equal(apiOutcome({...input,code:"rate_limit_exceeded",retry_after:7200}).blocked_until,8200,"Never shorten an upstream minimum wait");
});
test("cooldowns and model-scoped permission failures retain key order without making a balance claim", () => {
  const keys=[{id:"a",blocked_until:1100},{id:"b",blocked_until:1100,blocked_model:"other-model"},{id:"c"}];
  assert.deepEqual(apiKeys({now:1000,model:"selected-model",keys}),["b","c"]);
  assert.deepEqual(apiKeys({now:1100,model:"selected-model",keys}),["a","b","c"]);
});
