import assert from "node:assert/strict";
import test from "node:test";
import {createHash} from "node:crypto";
import {mkdtemp,rm} from "node:fs/promises";
import {join} from "node:path";
import {tmpdir} from "node:os";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {Pool} from "pg";
import {PostgreSqlAuthorityStore,installPostgreSqlAuthorityStoreSchema} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {executeDirectionOperation} from "../../loopx/control_plane/goals/direction_authority.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const goal_ref={goal_id:"direction",goal_instance_id:`ginst_${"a".repeat(32)}`};
const source={profile_id:"source_session_v1",current_goal_ref:goal_ref,goal_status:"active",gate:null,
  registered_agents:["agent-a","agent-b"],legacy_authority_present:false};
const sha=(v:string)=>createHash("sha256").update(v).digest("hex");
const material={material_id:"contract",purpose:"Preserve the agreed outcome",topics:["architecture"],required:true,
  boundary:"local_private",source_ref_sha256:sha("source.md"),revision:`sha256:${sha("original material")}`};
function request(operation:string,fields:JsonObject={}):JsonObject{return {operation,goal_ref,source,actor_agent_id:"agent-a",
  observed_at:"2026-10-13T01:00:00.001Z",dry_run:false,...fields};}
for(const provider of ["file","sqlite","postgresql"]){
 test(`${provider}: acknowledged delivery replays only its own exact source and preserves work`,{skip:provider==="postgresql"&&!process.env.LOOPX_TEST_POSTGRES_URL},async t=>{
  const folder=await mkdtemp(join(tmpdir(),"direction-owner-"));t.after(()=>rm(folder,{recursive:true,force:true}));
  let store:AuthorityStore;
  if(provider==="postgresql"){
    const pool=new Pool({connectionString:process.env.LOOPX_TEST_POSTGRES_URL});t.after(()=>pool.end());
    const database={connect:async()=>{const client=await pool.connect();return {query:(sql:string,values?:readonly unknown[])=>client.query(sql,values?[...values]:undefined),release:()=>client.release()};}};
    await installPostgreSqlAuthorityStoreSchema(database,"postgresql:"+"b".repeat(32));
    store=new PostgreSqlAuthorityStore(database,{tenant_id:folder,goal_id:goal_ref.goal_id});
  }else store=provider==="file"?new FileAuthorityStore(folder,goal_ref.goal_id):new SqliteAuthorityStore(folder,goal_ref.goal_id);
  const work={goal_id:goal_ref.goal_id,todos:[{todo_id:"existing",text:"Retain independent work"}],leases:[]};
  const initial=await store.commitAuthority({expected_provider_revision:null,operation_id:"seed",events:[],receipts:[],next_projection:work});
  assert.equal(initial.status,"applied");if(initial.status!=="applied")throw Error("seed");
  const invoke=(r:JsonObject)=>executeDirectionOperation(store,r,async()=>true);
  const configure=request("configure",{actor_agent_id:null,operation_id:"configure",expected_provider_revision:initial.provider_revision,materials:[material]});
  for(const patch of [{actor_agent_id:"agent-a"},{dry_run:"false"},{source:{...source,legacy_authority_present:true}},
    {materials:[{...material,required:false}]},{materials:[{...material,boundary:"unknown"}]},{materials:[{...material,purpose:"https://private.example"}]},
    {materials:[{...material,topics:["t".repeat(121)]}]},{source:{...source,registered_agents:[]}}]){
    await assert.rejects(invoke({...configure,...patch}));assert.equal((await store.readReceipt("configure")).status,"missing");
  }
  const configured=await invoke(configure);assert.equal(configured.ok,true);
  const revision=await store.loadAuthority();if(revision.status!=="loaded")throw Error("missing");
  const stable=await invoke({...configure,operation_id:"identical-declaration",expected_provider_revision:revision.provider_revision});
  assert.equal(stable.status,"no_change");assert.equal(stable.authority_digest,configured.authority_digest);
  const current=await store.loadAuthority();if(current.status!=="loaded")throw Error("missing");
  assert.deepEqual(current.head.todos,work.todos);assert.deepEqual(current.head.leases,[]);
  const read=request("offer_read",{operation_id:"read",material_id:"contract",required_revision:material.revision,
    authority_digest:configured.authority_digest,source_ref_sha256:material.source_ref_sha256,source:{...source,material_observation:{
      revision:material.revision,content_sha256:sha("original material"),source_ref_sha256:material.source_ref_sha256,byte_count:17}}});
  const write=store.commitAuthority.bind(store);let writes=0;
  store.commitAuthority=async input=>{writes++;await write(input);throw Error("reply lost after durable write");};
  const offered=await invoke(read);assert.equal(offered.status,"recovered");assert.equal(writes,1);
  assert.equal((await invoke(request("inspect"))).receipt_count,0);
  const replay=await invoke(read);assert.equal(replay.status,"replayed");assert.equal(writes,1);
  assert.deepEqual(replay.original_receipt,offered.original_receipt);
  store.commitAuthority=write;
  const ack=request("acknowledge",{operation_id:"ack",read_operation_id:"read",delivery_digest:offered.delivery_digest});
  for(const patch of [{actor_agent_id:"agent-b"},{delivery_digest:"0".repeat(64)},
    {goal_ref:{...goal_ref,goal_instance_id:`ginst_${"b".repeat(32)}`}},{receipts:[{outcome:"verified"}]}]){
    await assert.rejects(invoke({...ack,...patch}));assert.equal((await store.readReceipt("ack")).status,"missing");
  }
  const confirmed=await invoke(ack);assert.equal(confirmed.ok,true);assert.equal(confirmed.understanding_verified,false);
  assert.equal((await invoke(request("inspect"))).receipt_count,1);
  assert.equal(((await invoke(request("inspect"))).projection as JsonObject).direction_state,"current");
  assert.equal(((await invoke(request("inspect",{actor_agent_id:"agent-b"}))).projection as JsonObject).direction_state,"blocked");
  const historical=await invoke(request("receipt",{receipt_operation_id:"ack"}));assert.equal(historical.historical_only,true);
  await assert.rejects(invoke(request("receipt",{actor_agent_id:"agent-b",receipt_operation_id:"ack"})));
  const after=await store.loadAuthority();if(after.status!=="loaded")throw Error("missing");
  assert.deepEqual(after.head.todos,work.todos);assert.deepEqual(after.head.leases,[]);
  assert.equal(JSON.stringify(after.head).includes("original material"),false);
  assert.equal(JSON.stringify(after.head).includes("source.md"),false);
  // A legal full projection remains a bounded read model, not a reason to
  // discard a newer failure. Seed synthetic history only in this isolated store.
  const original=(after.head.material_usage_receipts as JsonObject[])[0]!;
  const records=Array.from({length:4096},(_,i)=>({...original,read_operation_id:`history-${i}`,
    receipt:{...(original.receipt as JsonObject),receipt_id:`history-${i}`,agent_id:i===4095?"agent-b":"agent-a",
      recorded_at:new Date(Date.UTC(2026,0,1)+i).toISOString()}}));
  const full=await store.commitAuthority({expected_provider_revision:after.provider_revision,operation_id:"full-history",
    events:[],receipts:[],next_projection:{...after.head,material_usage_receipts:records}});
  assert.equal(full.status,"applied");
  const old=await invoke({...read,operation_id:"old-offer"});
  const peer=await invoke({...read,operation_id:"peer-offer",actor_agent_id:"agent-b"});
  // Persist an old-format offer, as produced by the pre-fix reader. It must
  // remain valid before rollover, but cannot use the unchanged count after it.
  const legacyOffer={...(old.offer as JsonObject)};delete legacyOffer.usage_basis_sha256;
  const {canonicalAuthoritySha256}=await import("../../loopx/control_plane/coordination/authority_store_codec.ts");
  legacyOffer.read_operation_id="legacy-offer";
  const beforeLegacy=await store.loadAuthority();if(beforeLegacy.status!=="loaded")throw Error("missing");
  const legacyDigest=canonicalAuthoritySha256(legacyOffer);
  await store.commitAuthority({expected_provider_revision:beforeLegacy.provider_revision,operation_id:"legacy-offer",events:[],
    receipts:[{schema_version:"goal_direction_operation_v0",operation_id:"legacy-offer",result:{goal_ref,agent_id:"agent-a",offer:legacyOffer,delivery_digest:legacyDigest}}],
    next_projection:beforeLegacy.head});
  const missing={...read,operation_id:"full-missing",source:{...source,material_observation:{available:false}}};
  store.commitAuthority=async input=>{await write(input);throw Error("lost full-capacity reply");};
  const failed=await invoke(missing);assert.equal(failed.status,"recovered");assert.equal(failed.read_outcome,"unavailable");
  store.commitAuthority=write;
  assert.deepEqual(failed.archived_usage_record,records[0]);
  assert.equal(((await invoke(request("inspect"))).projection as JsonObject).direction_state,"blocked");
  assert.equal((await invoke(missing)).status,"replayed");
  const archive=await invoke(request("receipt",{receipt_operation_id:"full-missing"}));
  assert.deepEqual(((archive.receipts as JsonObject[])[0]!.result as JsonObject).archived_usage_record,records[0]);
  for(const [id,digest] of [["old-offer",old.delivery_digest],["legacy-offer",legacyDigest]]) {
    await assert.rejects(invoke(request("acknowledge",{operation_id:`late-${id}`,read_operation_id:id,delivery_digest:digest})),/later material observation/);
  }
  // A new-format peer offer binds only that peer/material, so unrelated
  // rollover must not require another read.
  const peerAck=await invoke(request("acknowledge",{actor_agent_id:"agent-b",operation_id:"peer-ack",read_operation_id:"peer-offer",delivery_digest:peer.delivery_digest}));
  assert.equal(peerAck.ok,true);
  const fresh=await invoke({...read,operation_id:"fresh-offer"});
  const restored=await invoke(request("acknowledge",{operation_id:"fresh-ack",read_operation_id:"fresh-offer",delivery_digest:fresh.delivery_digest}));
  assert.equal(restored.ok,true);
  assert.equal(((await invoke(request("inspect"))).projection as JsonObject).direction_state,"current");
  const final=await store.loadAuthority();if(final.status!=="loaded")throw Error("missing");
  assert.equal((final.head.material_usage_receipts as JsonObject[]).length,4096);
  assert.deepEqual(final.head.todos,work.todos);assert.deepEqual(final.head.leases,[]);
  const originalHistory=await store.readReceipt("ack");
  assert.equal(originalHistory.status,"found");
  if(originalHistory.status==="found")assert.deepEqual(originalHistory.receipts,historical.receipts);
  // The same Agent cannot evict a different material to make space either.
  const secondMaterial={...material,material_id:"other",required:false};
  const expanded=await invoke(request("configure",{actor_agent_id:null,operation_id:"add-other-material",
    expected_provider_revision:final.provider_revision,materials:[material,secondMaterial]}));
  const beforeOther=await store.loadAuthority();
  await assert.rejects(invoke({...missing,operation_id:"other-material-unavailable",material_id:"other",
    authority_digest:expanded.authority_digest}),/no retained observation for this Agent\/material/);
  assert.deepEqual(await store.loadAuthority(),beforeOther);
  assert.equal((await store.readReceipt("other-material-unavailable")).status,"missing");

 });
}

test("invalid canonical fields and capacity cannot create a partial or content-bearing projection",async t=>{
 const folder=await mkdtemp(join(tmpdir(),"direction-corruption-"));t.after(()=>rm(folder,{recursive:true,force:true}));
 const store=new SqliteAuthorityStore(folder,goal_ref.goal_id);
 const seed=await store.commitAuthority({expected_provider_revision:null,operation_id:"seed",events:[],receipts:[],next_projection:{goal_id:goal_ref.goal_id,todos:[],leases:[]}});
 if(seed.status!=="applied")throw Error("seed");
 const invoke=(r:JsonObject)=>executeDirectionOperation(store,r,async()=>true);
 const config=request("configure",{actor_agent_id:null,operation_id:"configure",expected_provider_revision:seed.provider_revision,materials:[material]});
 await assert.rejects(invoke({...config,materials:Array.from({length:65},(_,i)=>({...material,material_id:`material-${i}`}))}),/capacity/);
 assert.equal((await store.readReceipt("configure")).status,"missing");
 await invoke(config);
 const baseline=await store.loadAuthority();if(baseline.status!=="loaded")throw Error("missing");
 const registry=baseline.head.authority_registry as JsonObject;
 // Even a self-consistent storage hash cannot turn an unsupported public field
 // into a raw-body projection. This mutates only a disposable canonical store.
 const altered={...registry,content:"must never be projected"};
 const {canonicalAuthoritySha256}=await import("../../loopx/control_plane/coordination/authority_store_codec.ts");
 await store.commitAuthority({expected_provider_revision:baseline.provider_revision,operation_id:"synthetic-invalid",events:[],receipts:[],next_projection:{...baseline.head,
   authority_registry:altered,goal_direction_scope:{...(baseline.head.goal_direction_scope as JsonObject),authority_digest:canonicalAuthoritySha256({goal_ref,authority_registry:altered})}}});
 await assert.rejects(invoke(request("inspect")),/fields missing or unsupported/);
 const bad=await store.loadAuthority();if(bad.status!=="loaded")throw Error("missing");
 await store.commitAuthority({expected_provider_revision:bad.provider_revision,operation_id:"restore",events:[],receipts:[],next_projection:baseline.head});
 const restored=await invoke(request("inspect"));assert.equal(restored.ok,true);
 assert.equal((restored.projection as JsonObject).direction_state,"blocked");
});

test("writer rejects a valid source Goal id that the M1 identity envelope cannot represent",async t=>{
 const folder=await mkdtemp(join(tmpdir(),"direction-identity-envelope-"));t.after(()=>rm(folder,{recursive:true,force:true}));
 const longRef={...goal_ref,goal_id:"g".repeat(181)};
 const store=new FileAuthorityStore(folder,longRef.goal_id);
 const seed=await store.commitAuthority({expected_provider_revision:null,operation_id:"seed",events:[],receipts:[],next_projection:{goal_id:longRef.goal_id,todos:[],leases:[]}});
 if(seed.status!=="applied")throw Error("seed");
 const before=await store.loadAuthority();
 await assert.rejects(executeDirectionOperation(store,request("configure",{goal_ref:longRef,source:{...source,current_goal_ref:longRef},
   actor_agent_id:null,operation_id:"configure",expected_provider_revision:seed.provider_revision,materials:[material]}),async()=>true),/direction_input_invalid/);
 assert.deepEqual(await store.loadAuthority(),before);
 assert.equal((await store.readReceipt("configure")).status,"missing");
});
