import assert from "node:assert/strict";
import {test} from "node:test";
import {apiKeys, apiOutcome, apiStreamOutcome} from "../src/loopx_codex_provider_routing/openai_policy.ts";

test("known quota rejection rotates a key; unknown effects and ordinary request failures do not", () => {
  const input={now:1000,status:429,code:"insufficient_quota",retry_after:null,model:"selected-model"};
  assert.equal(apiOutcome(input).state,"quota_exhausted");
  assert.equal(apiOutcome(input).retry_next_key,true);
  assert.deepEqual(apiOutcome({...input,code:"credit_balance_exhausted"}),{
    state:"quota_exhausted",retry_next_key:true,blocked_until:1300,blocked_model:null});
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
test("expired rejections yield to alternatives instead of starving a spaced recovery request", () => {
  const rejected={id:"a",state:"quota_exhausted",observed_at:1000,blocked_until:1300};
  const keys=[rejected,{id:"b",state:"accepted",observed_at:900},{id:"c"}];
  assert.deepEqual(apiKeys({now:1400,model:"selected-model",keys}),["b","c","a"]);
  assert.deepEqual(apiKeys({now:1200,model:"selected-model",keys:[rejected]}),[]);
  assert.deepEqual(apiKeys({now:1400,model:"selected-model",keys:[rejected]}),["a"],"Expiry permits a future attempt, not a capacity claim");
  const failures=[rejected,{id:"b",state:"rate_limited",observed_at:800,blocked_until:1200},
    {id:"c",state:"authentication_failed",observed_at:800,blocked_until:1200}];
  assert.deepEqual(apiKeys({now:1400,model:"selected-model",keys:failures}),["b","c","a"],"Oldest failures remain eligible with stable ties");
  const permission={...rejected,state:"permission_denied",blocked_model:"other-model"};
  assert.deepEqual(apiKeys({now:1400,model:"selected-model",keys:[permission,{id:"b"}]}),["a","b"]);
  assert.deepEqual(apiKeys({now:1400,model:"other-model",keys:[permission,{id:"b"}]}),["b","a"]);
  for (const state of ["accepted","failed",undefined]) {
    assert.deepEqual(apiKeys({now:1400,model:"selected-model",keys:[{...rejected,state},{id:"b"}]}),["a","b"]);
  }
  assert.deepEqual(apiKeys({now:1400,model:"selected-model",keys:[{...rejected,observed_at:undefined},{id:"b"}]}),["a","b"]);
});
test("only structured streamed quota failures update future key availability", () => {
  for (const code of ["insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded", "organization_usage_limit_exceeded", "usage_limit_exceeded",
    "rate_limit_exceeded"]) {
    for (const event of [{type:"error",code},
      {type:"response.failed",response:{status:"failed",error:{code}}}]) {
      assert.deepEqual(apiStreamOutcome(event),{status:429,code});
      assert.ok(apiOutcome({now:1000,...apiStreamOutcome(event),retry_after:null,model:"selected-model"}).blocked_until>1000);
    }
  }
  for (const event of [null, [], "insufficient_quota", {type:"error",message:"Quota exceeded"},
    {type:"error",code:"server_error"}, {type:"error",code:{insufficient_quota:true}},
    {type:"response.output_text.delta",delta:JSON.stringify({type:"error",code:"insufficient_quota"})},
    {type:"response.completed",response:{status:"failed",error:{code:"insufficient_quota"}}},
    {type:"response.failed",response:{status:"completed",error:{code:"insufficient_quota"}}},
    {type:"response.failed",response:{status:"failed",error:"insufficient_quota"}}]) {
    assert.equal(apiStreamOutcome(event),null);
  }
});
