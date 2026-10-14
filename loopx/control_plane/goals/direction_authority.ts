/** Exact-lifetime material declarations, read offers and acknowledged usage.
 * Canonical state/receipts stay in the existing Goal authority store. Source
 * bytes are never persisted, and a provider read alone is not an Agent receipt.
 */
import {createHash} from "node:crypto";
import {BARE_SHA256_PATTERN, ENVELOPED_SHA256_PATTERN} from "../content_digest.ts";
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore, AuthorityStoreHead} from "../coordination/authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {CoordinationCommandReceipt, commandReceiptResult} from "../coordination/command_receipt.ts";
import {registryAuthoritySourceCheck, type AuthoritySourceCheck} from "../coordination/authority_source.ts";
import {withCanonicalWriter} from "../coordination/local_authority_write.ts";
import {openLocalAuthorityStore} from "../coordination/local_authority_provider.ts";
import {parseQuotaAccountingOwner, withBorrowedQuotaAccountingOwner} from "../quota/source_admission.ts";
import {sourceGoalMutationReadiness} from "./source_session_lifetime.ts";
import {parseWireExactGoalRef, readGoalAcceptanceLifecycle, sameExactGoalRef, type WireExactGoalRef} from "./acceptance_lifecycle.ts";
import {buildGoalDirectionBaseline, GOAL_DIRECTION_LIMITS, validateDirectionIdentity, DirectionBaselineInputError} from "./direction_baseline.ts";
import {INTERNAL_STATE_PRIVATE_TEXT_PATTERNS} from "./vision_checkpoint.ts";
import {readGoalAcceptanceAuthority} from "./acceptance_contract.ts";

const SCHEMA = "goal_direction_result_v0";
const OPERATION_SCHEMA = "goal_direction_operation_v0";
const SCOPE_SCHEMA = "goal_direction_scope_v0";
const USAGE_SCHEMA = "goal_material_usage_record_v0";
const FIELDS = {inspect: [], prepare_configure: ["expected_provider_revision"], configure: ["materials", "expected_provider_revision"], disable: ["expected_provider_revision"],
  prepare_read: ["material_id", "required_revision", "authority_digest", "source_ref_sha256"],
  offer_read: ["material_id", "required_revision", "authority_digest", "source_ref_sha256"],
  acknowledge: ["read_operation_id", "delivery_digest"], receipt: ["receipt_operation_id"]} as const;
type Operation = keyof typeof FIELDS;
const object = (v: unknown, label: string) => canonicalAuthorityObject(v, label);
function need(ok: unknown, message: string): asserts ok {if (!ok) throw new AuthorityStoreProtocolError(message);}
class DirectionBasisChanged extends AuthorityStoreProtocolError {}
function basis(ok: unknown, message: string): asserts ok {if(!ok)throw new DirectionBasisChanged(message);}
function keys(v: JsonObject, required: readonly string[], optional: readonly string[] = []): void {
  need(required.every(k => Object.hasOwn(v, k)) && Object.keys(v).every(k => required.includes(k) || optional.includes(k)), "direction fields missing or unsupported");
}
function token(v: unknown, label: string): string {
  need(typeof v === "string" && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(v), `${label} must be a bounded identifier`);return v;
}
function safe(v: unknown, label: string, limit = 220): string {
  need(typeof v === "string" && v.trim() === v && !!v && [...v].length <= limit && !/[\r\n\t\0]/.test(v)
    && !v.startsWith("/") && !v.includes("://") && !INTERNAL_STATE_PRIVATE_TEXT_PATTERNS.some(p => p.test(v)), `${label} must be bounded public-safe text`);return v;
}
function hash(v: unknown): string {need(typeof v === "string" && BARE_SHA256_PATTERN.test(v), "direction digest must be SHA-256");return v;}
function failure(reason: string): JsonObject & {schema_version: typeof SCHEMA} {
  return {schema_version: SCHEMA, ok: false, status: "failed", changed: false, reason,
    execution_authority_granted: false, acceptance_assessed: false};
}
function policyRegistry(value: unknown): JsonObject {
  need(Array.isArray(value) && value.length > 0 && value.length <= GOAL_DIRECTION_LIMITS.requirements, "direction material declaration capacity exceeded");
  const materials: JsonObject = {}, topics: JsonObject = {}, requirements: JsonObject[] = [];
  const ordered=value.map(item=>object(item,"direction material")).sort((a,b)=>String(a.material_id)<String(b.material_id)?-1:String(a.material_id)>String(b.material_id)?1:0);
  for (const input of ordered) {
    const item = object(input, "direction material");
    keys(item, ["material_id", "purpose", "topics", "required", "boundary", "source_ref_sha256", "revision"]);
    const id = token(item.material_id, "material_id");need(!Object.hasOwn(materials, id), "duplicate direction material");
    need(item.required === true || item.required === false, "required must be boolean");
    need(["public", "public_safe", "local_private"].includes(String(item.boundary)), "direction source boundary is unsupported");
    need(Array.isArray(item.topics) && item.topics.length <= 16, "material topics must be bounded");
    need(typeof item.revision === "string" && ENVELOPED_SHA256_PATTERN.test(item.revision), "local material revision must be content SHA-256");
    materials[id] = {id, source_ref_sha256: hash(item.source_ref_sha256), source_ref_redacted: true, source_kind: "document",
      role: "direction", owner_status: "reviewed", conflict_rule: "owner_wins", freshness: "current", gate_status: "readable",
      boundary: item.boundary, revision: item.revision};
    const names = item.topics.map((t: unknown) => token(t, "topic"));need(new Set(names).size === names.length, "duplicate material topic");
    for (const t of names) {
      if (!Object.hasOwn(topics, t)) Object.defineProperty(topics, t, {value: [], enumerable: true, writable: true});
      (topics[t] as string[]).push(id);
    }
    requirements.push({requirement_id: `direction:${id}`, material_id: id, purpose: safe(item.purpose, "purpose"), required: item.required});
  }
  need(requirements.some(r => r.required), "direction declaration needs at least one required material");
  const registry = {project_materials: materials, topic_authority: topics, goal_direction_requirements: requirements};
  need(Buffer.byteLength(JSON.stringify(registry)) <= GOAL_DIRECTION_LIMITS.output_bytes, "direction declaration byte capacity exceeded");
  return registry;
}
function scope(head: JsonObject, goalRef: WireExactGoalRef): JsonObject | null {
  if (head.goal_direction_scope === undefined) return null;
  const value = object(head.goal_direction_scope, "direction scope");
  keys(value, ["schema_version", "goal_ref", "enabled", "authority_digest"], ["usage_generation"]);
  need(Number.isSafeInteger(value.usage_generation ?? 0) && Number(value.usage_generation ?? 0) >= 0, "invalid material usage generation");
  need(value.schema_version === SCOPE_SCHEMA && typeof value.enabled === "boolean", "invalid direction scope");
  const storedRef = parseWireExactGoalRef(value.goal_ref, "direction scope");need(sameExactGoalRef(storedRef, goalRef), "direction belongs to a different Goal lifetime");
  const registry = object(head.authority_registry, "canonical authority registry");
  keys(registry,["project_materials","topic_authority","goal_direction_requirements"]);
  const materials=object(registry.project_materials,"registered materials"),topics=object(registry.topic_authority,"registered topics");
  need(Array.isArray(registry.goal_direction_requirements),"direction requirements are unavailable");
  const normalized=policyRegistry(registry.goal_direction_requirements.map(value=>{
    const required=object(value,"stored requirement");keys(required,["requirement_id","material_id","purpose","required"]);
    const id=token(required.material_id,"material id"),material=object(materials[id],"stored material");
    need(required.requirement_id===`direction:${id}`,"direction requirement identity changed");
    return {material_id:id,purpose:required.purpose,required:required.required,
      topics:Object.entries(topics).filter(([,ids])=>{need(Array.isArray(ids),"stored topic must be a complete list");return ids.includes(id);}).map(([topic])=>topic),
      boundary:material.boundary,source_ref_sha256:material.source_ref_sha256,revision:material.revision};
  }));
  need(canonicalAuthoritySha256(normalized)===canonicalAuthoritySha256(registry),"stored direction authority is malformed or contains unsupported fields");
  need(value.authority_digest === canonicalAuthoritySha256({goal_ref: goalRef, authority_registry: registry}), "direction authority digest mismatch");
  return value;
}
function usage(head: JsonObject, goalRef: WireExactGoalRef): JsonObject[] {
  const rows = head.material_usage_receipts ?? [];
  need(Array.isArray(rows) && rows.length <= GOAL_DIRECTION_LIMITS.receipts, "material usage receipt capacity exceeded");
  const ids = new Set();
  return rows.map(v => {
    const value = object(v, "scoped material usage");keys(value, ["schema_version", "goal_ref", "receipt", "read_operation_id", "delivery_digest"]);
    need(value.schema_version === USAGE_SCHEMA && sameExactGoalRef(parseWireExactGoalRef(value.goal_ref, "usage scope"), goalRef), "usage receipt belongs to another Goal lifetime");
    token(value.read_operation_id, "read operation");hash(value.delivery_digest);
    const receipt = object(value.receipt, "material usage receipt");
    keys(receipt, ["schema_version", "receipt_id", "goal_id", "agent_id", "material_id", "todo_id", "observed_revision", "outcome", "recorded_at"]);
    need(receipt.schema_version === "material_usage_receipt_v0" && receipt.goal_id === goalRef.goal_id && receipt.todo_id === null
      && (receipt.outcome === "read" || receipt.outcome === "unavailable"), "invalid material observation receipt");
    token(receipt.agent_id, "receipt Agent");token(receipt.material_id, "receipt material");const id=token(receipt.receipt_id,"receipt id");
    need(typeof receipt.recorded_at==="string"&&/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(receipt.recorded_at)
      &&Number.isFinite(Date.parse(receipt.recorded_at)),"usage receipt timestamp invalid");
    need(receipt.outcome==="unavailable"?receipt.observed_revision===null:
      typeof receipt.observed_revision==="string"&&ENVELOPED_SHA256_PATTERN.test(receipt.observed_revision),"usage receipt revision invalid");
    need(!ids.has(id), "duplicate usage receipt identity");ids.add(id);
    return receipt;
  });
}
/** Keep the bounded current read model, retaining every displaced record in the
 * same atomic command receipt/journal. Never evict another Agent/material.
 * Legacy offers use the generation fence; new offers bind only their own pair.
 */
function retainUsage(next: JsonObject, goalRef: WireExactGoalRef, record: JsonObject): JsonObject | null {
  const rows = usage(next, goalRef), incoming = object(record.receipt, "new usage receipt");
  const retained = [...(next.material_usage_receipts as JsonObject[] ?? [])];
  let archived: JsonObject | null = null;
  if (rows.length === GOAL_DIRECTION_LIMITS.receipts) {
    const index = rows.findIndex(row => row.agent_id === incoming.agent_id && row.material_id === incoming.material_id);
    need(index >= 0, "material usage capacity exceeded; no retained observation for this Agent/material can be archived");
    const configured = scope(next, goalRef)!;
    const generation = Number(configured.usage_generation ?? 0) + 1;
    need(Number.isSafeInteger(generation), "material usage generation capacity exceeded");
    [archived] = retained.splice(index, 1);
    next.goal_direction_scope = {...configured, usage_generation: generation};
  }
  retained.push(record);next.material_usage_receipts = retained;
  return archived;
}
function usageBasis(rows: JsonObject[], actor: unknown, material: unknown): string {
  return canonicalAuthoritySha256(rows.filter(row => row.agent_id === actor && row.material_id === material));
}
function readView(loaded: AuthorityStoreHead, goalRef: WireExactGoalRef, actor: string | null, registered: string[], now: string): JsonObject {
  const configured = scope(loaded.head, goalRef), rows = usage(loaded.head, goalRef);
  const registry = configured ? object(loaded.head.authority_registry, "authority registry") : null;
  const projection = configured?.enabled === true && actor ? buildGoalDirectionBaseline({goal_id: goalRef.goal_id, agent_id: actor,
    generated_at: now, authority_registry: registry, receipts: rows, available_boundaries: ["local_private"]}) : null;
  const authority = readGoalAcceptanceAuthority(loaded.head, goalRef.goal_id);
  const acceptance = authority.kind === "exact" && authority.owner_matches && authority.lifecycle.state === "active"
    && sameExactGoalRef(authority.lifecycle.goal_ref, goalRef) && authority.state?.enabled ? authority.state : null;
  return {schema_version: SCHEMA, status: "loaded", ok: true, changed: false, goal_ref: goalRef, provider_revision: loaded.provider_revision,
    enabled: configured?.enabled === true, authority_digest: configured?.authority_digest ?? null,
    authority_registry: registry, registered_agents: registered, projection,
    vision_gap: projection && projection.direction_state !== "current" ? {
      kind: "goal_direction_basis", agent_id: actor, baseline_digest: projection.baseline_digest,
      state: projection.direction_state, reason_codes: projection.reason_codes,
      creates_work: false, rewrites_vision: false, changes_goal_route: false,
    } : null,
    acceptance_basis: {contract_revision: acceptance?.revision ?? null, contract_digest: acceptance?.digest ?? null,
      direction_baseline_digest: projection?.baseline_digest ?? null, acceptance_assessed: false, result: "not_assessed"},
    receipt_count: rows.filter(r => r.agent_id === actor).length, execution_authority_granted: false, acceptance_assessed: false};
}
function usageRecordedAt(rows: JsonObject[], now: string): string {
  // v0 readers order by recorded_at. Preserve canonical append order across
  // equal or rewound host clocks without rewriting any historical timestamp.
  const prior=rows.reduce((latest,row)=>Math.max(latest,Date.parse(String(row.recorded_at))),-Infinity);
  const recorded=new Date(Math.max(Date.parse(now),prior+1)).toISOString();
  need(/^\d{4}-/.test(recorded),"material usage timestamp capacity exceeded");
  return recorded;
}
function requestDigest(request:JsonObject):string {
  return canonicalAuthoritySha256(Object.fromEntries(Object.entries(request).filter(([key])=>!["runtime_root","source","observed_at","dry_run"].includes(key))));
}
function currentMaterial(head: JsonObject, goalRef: WireExactGoalRef, request: JsonObject): JsonObject {
  const configured = scope(head, goalRef);need(configured?.enabled === true, "direction baseline is disabled");
  basis(request.authority_digest === configured.authority_digest, "direction authority changed; inspect the current basis");
  const registry=object(head.authority_registry,"authority registry");const materials=object(registry.project_materials,"materials");
  const item=object(materials[token(request.material_id,"material_id")],"registered material");
  need(item.revision===request.required_revision && item.source_ref_sha256===request.source_ref_sha256, "material read differs from the registered source/revision");
  need(item.owner_status==="reviewed" && item.conflict_rule==="owner_wins" && item.freshness==="current" && item.gate_status==="readable"
    && ["public","public_safe","local_private"].includes(String(item.boundary)), "material authority does not permit this read");
  return item;
}
/** Trusted adapter facts include actual source IO; public callers cannot supply them. */
export async function executeDirectionOperation(store: AuthorityStore, value: unknown, sourceCurrent: AuthoritySourceCheck): Promise<JsonObject> {
  const request=object(value,"direction request");need(Buffer.byteLength(JSON.stringify(request))<=GOAL_DIRECTION_LIMITS.input_bytes,"direction request capacity exceeded");
  const op=request.operation as Operation;need(Object.hasOwn(FIELDS,op),"unsupported direction operation");
  keys(request,["operation","goal_ref","actor_agent_id","source","observed_at","dry_run",...FIELDS[op]],
    ["runtime_root","operation_id",...(op==="configure"?["expected_authority_digest"]:[])]);
  const goalRef=parseWireExactGoalRef(request.goal_ref,"direction Goal");const source=object(request.source,"source facts");
  validateDirectionIdentity(goalRef.goal_id);
  need(typeof request.dry_run==="boolean","direction dry_run must be boolean");
  need(Array.isArray(source.registered_agents)&&source.registered_agents.length<=128,"source Agent registry unavailable");const agents=source.registered_agents.map((v:unknown)=>token(v,"registered Agent"));
  need(new Set(agents).size===agents.length,"source Agent identities are duplicated");
  const actor=request.actor_agent_id===null?null:token(request.actor_agent_id,"Agent");need(actor===null||agents.includes(actor),"direction actor is not registered");
  const now=String(request.observed_at);need(Number.isFinite(Date.parse(now)),"observation timestamp invalid");
  if (op==="receipt") {
    const found=await store.readReceipt(token(request.receipt_operation_id,"receipt operation"));
    if(found.status==="found") {
      need(found.receipts.length===1 && found.receipts[0].schema_version===OPERATION_SCHEMA,"not a direction operation receipt");
      const result=object(found.receipts[0].result,"receipt result");need(sameExactGoalRef(parseWireExactGoalRef(result.goal_ref,"receipt Goal"),goalRef),"receipt belongs to another Goal lifetime");
      need(result.agent_id===null||result.agent_id===actor,"material receipt belongs to another Agent");
    }
    return {schema_version:SCHEMA,...found,ok:found.status==="found"||found.status==="missing",historical_only:true,execution_authority_granted:false,acceptance_assessed:false};
  }
  const readOnly=op==="inspect"||op==="prepare_read"||op==="prepare_configure";
  const identity=readOnly?null:new CoordinationCommandReceipt({result_schema:SCHEMA,
    identity:{schema_version:OPERATION_SCHEMA,goal_id:goalRef.goal_id,operation_id:token(request.operation_id,"operation id"),
      request_sha256:requestDigest(request)},
    failure:(_code,reason)=>failure(reason),decode:commandReceiptResult});
  if(identity){const prior=await identity.read(store);if(prior!==null)return {...prior,
    ok:["applied","recovered","replayed","no_change"].includes(String(prior.status))&&prior.read_outcome!=="unavailable",
    historical_only:true,execution_authority_granted:false,acceptance_assessed:false};}
  need(await sourceCurrent(),"source Goal changed before direction operation");
  const ready=sourceGoalMutationReadiness({...source,requested_goal_ref:goalRef});need(ready.kind==="admit","Goal lifetime does not admit direction operation");
  if(op!=="disable")need(source.legacy_authority_present===false,"existing registry authority needs an explicit owner migration; refusing a second authority");
  const loaded=await store.loadAuthority();need(loaded.status==="loaded","canonical Goal authority is unavailable");
  const lifecycle=readGoalAcceptanceLifecycle(loaded.head,goalRef.goal_id);
  need(!lifecycle || lifecycle.state==="active"&&sameExactGoalRef(lifecycle.goal_ref,goalRef),"canonical Goal lifetime retired or changed");
  if(op==="inspect")return readView(loaded,goalRef,actor,agents,now);
  if(op==="prepare_configure"){
    need(actor===null,"direction declarations require the trusted Goal owner");
    need(agents.length>0,"Register a local Agent before configuring a direction baseline");
    const prior=scope(loaded.head,goalRef);need(prior!==null||loaded.head.authority_registry===undefined,"canonical authority already belongs to another owner");
    const history=await store.readReceipt(token(request.operation_id,"configuration operation"));
    // A known historical operation may be reread under the current live owner
    // boundary. The final configure still verifies its complete request hash.
    const known=history.status==="found"&&history.receipts.length===1&&history.receipts[0].schema_version===OPERATION_SCHEMA
      &&sameExactGoalRef(parseWireExactGoalRef(object(history.receipts[0].result,"configuration receipt").goal_ref,"receipt Goal"),goalRef);
    basis(known||request.expected_provider_revision===loaded.provider_revision,"direction provider basis changed; inspect and preview again");
    return {schema_version:SCHEMA,status:"configuration_admitted",ok:true,goal_ref:goalRef,execution_authority_granted:false,acceptance_assessed:false};
  }
  const next=structuredClone(loaded.head);let result:JsonObject;
  if(op==="configure"||op==="disable"){
    need(actor===null,"direction declarations require the trusted Goal owner");
    basis(request.expected_provider_revision===loaded.provider_revision,"direction provider basis changed; inspect and preview again");
    const prior=scope(loaded.head,goalRef);
    if(op==="configure"){
      need(prior!==null||loaded.head.authority_registry===undefined,"canonical authority already belongs to another owner");
      const registry=policyRegistry(request.materials);
      need(agents.length>0,"Register a local Agent before configuring a direction baseline");
      const readable=buildGoalDirectionBaseline({goal_id:goalRef.goal_id,agent_id:agents[0],generated_at:now,
        authority_registry:registry,receipts:usage(loaded.head,goalRef),available_boundaries:["local_private"]});
      need(readable?.baseline_digest,"direction declaration does not fit the existing reader envelope");
      const digest=canonicalAuthoritySha256({goal_ref:goalRef,authority_registry:registry});
      basis(request.expected_authority_digest===undefined||request.expected_authority_digest===digest,"reviewed material declaration changed before apply");
      next.authority_registry=registry;next.goal_direction_scope={schema_version:SCOPE_SCHEMA,goal_ref:goalRef,enabled:true,authority_digest:digest,
        ...(prior?.usage_generation === undefined ? {} : {usage_generation:prior.usage_generation})};
      result={changed:!prior||prior.enabled!==true||prior.authority_digest!==digest,goal_ref:goalRef,agent_id:null,authority_digest:digest};
    } else {
      need(prior,"direction baseline is not configured");next.goal_direction_scope={...prior,enabled:false};result={changed:prior.enabled===true,goal_ref:goalRef,agent_id:null,authority_digest:prior.authority_digest};
    }
  } else if(op==="prepare_read"||op==="offer_read"){
    need(actor!==null,"material reading requires its registered Agent");currentMaterial(loaded.head,goalRef,request);
    if(op==="prepare_read")return {schema_version:SCHEMA,status:"read_admitted",ok:true,goal_ref:goalRef,agent_id:actor,authority_digest:request.authority_digest,
      required_revision:request.required_revision,source_ref_sha256:request.source_ref_sha256,execution_authority_granted:false,acceptance_assessed:false};
    const observation=object(source.material_observation,"actual material read");
    const count=usage(loaded.head,goalRef).filter(r=>r.agent_id===actor&&r.material_id===request.material_id).length;
    if(observation.available===false){
      keys(observation,["available"]);
      const receipt={schema_version:"material_usage_receipt_v0",receipt_id:`unavailable:${canonicalAuthoritySha256({goal_ref:goalRef,operation_id:request.operation_id}).slice(0,32)}`,
        goal_id:goalRef.goal_id,agent_id:actor,material_id:request.material_id,todo_id:null,observed_revision:null,outcome:"unavailable",
        recorded_at:usageRecordedAt(usage(loaded.head,goalRef),now)};
      const archived=retainUsage(next,goalRef,{
        schema_version:USAGE_SCHEMA,goal_ref:goalRef,receipt,read_operation_id:request.operation_id,
        delivery_digest:canonicalAuthoritySha256({goal_ref:goalRef,agent_id:actor,operation_id:request.operation_id,available:false})});
      result={changed:true,goal_ref:goalRef,agent_id:actor,read_outcome:"unavailable",receipt,
        ...(archived ? {archived_usage_record:archived} : {}),
        receipt_recorded:true,reason:"Material is unavailable at its declared revision; repair its source or have the owner review the updated revision, then retry a new read."};
    } else {
    keys(observation,["revision","source_ref_sha256","byte_count","content_sha256"]);
    need(observation.revision===request.required_revision&&observation.source_ref_sha256===request.source_ref_sha256&&observation.revision===`sha256:${hash(observation.content_sha256)}`,
      "actual read did not match the required material revision");
    need(Number.isSafeInteger(observation.byte_count)&&Number(observation.byte_count)>0&&Number(observation.byte_count)<=256_000,"material byte budget exceeded");
    const offer={goal_ref:goalRef,agent_id:actor,material_id:request.material_id,authority_digest:request.authority_digest,required_revision:request.required_revision,
      source_ref_sha256:request.source_ref_sha256,content_sha256:observation.content_sha256,byte_count:observation.byte_count,read_operation_id:request.operation_id,usage_count:count,
      usage_basis_sha256:usageBasis(usage(loaded.head,goalRef),actor,request.material_id)};
    result={changed:true,goal_ref:goalRef,agent_id:actor,offer,delivery_digest:canonicalAuthoritySha256(offer),receipt_recorded:false};
    }
  } else {
    need(actor!==null,"material acknowledgement requires its registered Agent");
    const readId=token(request.read_operation_id,"read operation");const found=await store.readReceipt(readId);
    need(found.status==="found"&&found.receipts.length===1&&found.receipts[0].schema_version===OPERATION_SCHEMA,"original material read offer is unavailable");
    const read=object(found.receipts[0].result,"read offer result"),offer=object(read.offer,"original read offer");
    need(sameExactGoalRef(parseWireExactGoalRef(offer.goal_ref,"offer Goal"),goalRef)&&offer.agent_id===actor&&offer.read_operation_id===readId,
      "read offer belongs to a different Goal/Agent/operation");
    need(read.delivery_digest===hash(request.delivery_digest)&&read.delivery_digest===canonicalAuthoritySha256(offer),"read delivery acknowledgement differs from original offer");
    currentMaterial(loaded.head,goalRef,{...offer,material_id:offer.material_id});
    const rows=usage(loaded.head,goalRef);
    const receiptId="read_"+createHash("sha256").update(canonicalAuthoritySha256({goal_ref:goalRef,agent_id:actor,read_operation_id:readId})).digest("hex").slice(0,24);
    const existing=rows.find(row=>row.receipt_id===receiptId);
    need(existing || (offer.usage_basis_sha256 === undefined
      ? Number(scope(loaded.head,goalRef)?.usage_generation ?? 0) === 0
        && rows.filter(r=>r.agent_id===actor&&r.material_id===offer.material_id).length===offer.usage_count
      : hash(offer.usage_basis_sha256) === usageBasis(rows,actor,offer.material_id)),
      "a later material observation invalidated this unacknowledged offer; read the source again");
    const receipt=existing??{schema_version:"material_usage_receipt_v0",receipt_id:receiptId,goal_id:goalRef.goal_id,agent_id:actor,material_id:offer.material_id,
      todo_id:null,observed_revision:offer.required_revision,outcome:"read",recorded_at:usageRecordedAt(rows,now)};
    const archived=existing ? null : retainUsage(next,goalRef,{
      schema_version:USAGE_SCHEMA,goal_ref:goalRef,receipt,read_operation_id:readId,delivery_digest:read.delivery_digest});
    result={changed:!existing,goal_ref:goalRef,agent_id:actor,receipt,receipt_recorded:true,understanding_verified:false,
      ...(archived ? {archived_usage_record:archived} : {})};
  }
  need(await sourceCurrent(),"source Goal changed before direction commit");
  // Refuse the added record/configuration before its combined projection input
  // exceeds the builder envelope; never persist a permanently partial baseline.
  need(Buffer.byteLength(JSON.stringify({goal_id:goalRef.goal_id,agent_id:"a".repeat(128),generated_at:now,
    authority_registry:next.authority_registry,receipts:usage(next,goalRef),available_boundaries:["local_private"]}))<=GOAL_DIRECTION_LIMITS.input_bytes,
    "combined direction authority and usage capacity exceeded");
  if(request.dry_run===true)return {schema_version:SCHEMA,status:"planned",ok:true,...result,changed:false,dry_run:true,
    reviewed_request_sha256:requestDigest(op==="configure"?{...request,expected_authority_digest:result.authority_digest}:request),
    ...(Object.hasOwn(result,"receipt_recorded")?{receipt_recorded:false}:{}),execution_authority_granted:false,acceptance_assessed:false};
  const committed=await identity!.commit(store,{expected_provider_revision:loaded.provider_revision,operation_id:String(request.operation_id),next_projection:next,
    events:[{schema_version:OPERATION_SCHEMA,operation:op,operation_id:request.operation_id,goal_ref:goalRef}],
    receipts:[{...identity!.contract.identity,result:{...result,execution_authority_granted:false,acceptance_assessed:false}}]});
  return {...committed,ok:["applied","recovered","replayed","no_change"].includes(String(committed.status))&&committed.read_outcome!=="unavailable",
    execution_authority_granted:false,acceptance_assessed:false};
}
export async function directionLocal(value: unknown): Promise<JsonObject> {
  try {
    const request=object(value,"direction request"),source=object(request.source,"direction source");
    const root=String(request.runtime_root),goalRef=parseWireExactGoalRef(request.goal_ref,"Goal");
    const owner=parseQuotaAccountingOwner({runtimeRoot:root,goalId:goalRef.goal_id,
      goalRefValue:request.operation==="receipt"?source.current_goal_ref:goalRef,sourceAdmissionValue:source.accounting_admission});
    return await withBorrowedQuotaAccountingOwner(owner,()=>withCanonicalWriter(root,goalRef.goal_id,
      ["inspect","prepare_read","prepare_configure","receipt"].includes(String(request.operation))||request.dry_run===true,
      async()=>executeDirectionOperation(await openLocalAuthorityStore(root,goalRef.goal_id),request,registryAuthoritySourceCheck({registry_source:source.registry_source},true))));
  } catch(error) {
    if(error instanceof DirectionBaselineInputError)return {...failure("Goal identity does not fit the direction reader contract"),reason_code:error.reason};
    if(error instanceof DirectionBasisChanged)return {...failure(error.message),reason_code:"direction_basis_changed"};
    if(error instanceof AuthorityStoreProtocolError)return failure(error.message);throw error;
  }
}
