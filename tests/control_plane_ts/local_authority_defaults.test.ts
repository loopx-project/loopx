import assert from "node:assert/strict";
import {mkdtemp,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {manageNewGoalStorage as manage} from "../../loopx/control_plane/coordination/local_authority_defaults.ts";
import {openLocalAuthorityStoreHandle as selected} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
const configuration=(provider: string)=>({schema_version:"loopx_goal_storage_defaults_v0",new_goal_provider:provider});
for(const provider of ["file","sqlite"]){
  test(`new Goal target ${provider} is durable, idempotent and not a promotion`,async t=>{
    const root=await mkdtemp(join(tmpdir(),"goal-storage-"));t.after(()=>rm(root,{recursive:true,force:true}));
    const target=await manage({action:"resolve",configuration:configuration(provider)});
    const request={action:"initialize",runtime_root:root,goal_id:"example",target};
    const first=await manage(request);assert.equal(first.changed,provider === "sqlite");assert.equal(first.promotion_performed,false);
    const handle=await selected(root,"example");assert.equal(handle.provider,provider);
    assert.equal((await handle.store.loadAuthority()).status,"missing");
    assert.equal((await manage(request)).changed,false);
    if (provider === "file") return;
    const other=await manage({action:"resolve",configuration:configuration(provider==="file"?"sqlite":"file")});
    await assert.rejects(manage({...request,target:other}),/reviewed migration/);
    assert.equal((await selected(root,"example")).provider,provider);
  });
}
test("storage target rejects unknown providers and activation fields",async()=>{
  await assert.rejects(manage({action:"resolve",configuration:configuration("postgresql")}),/file or sqlite/);
  await assert.rejects(manage({action:"resolve",configuration:{...configuration("sqlite"),promote:true}}),/Invalid/);
});
