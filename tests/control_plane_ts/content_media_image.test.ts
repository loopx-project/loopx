import assert from "node:assert/strict";
import test from "node:test";
import {validateImagePlan as validate, transitionImage as transition, IMAGE_MODEL} from "../../loopx/control_plane/capabilities/content_media_image.ts";

const at = "2026-10-19T12:30:00Z";
const brief = () => ({schema_version:"content_ops_image_plan_v0",title:"Evidence returns",model:IMAGE_MODEL,prompt:"Three stations and one returned artifact. No text.",width:1280,height:720,references:[] as any[],watermark:true,
  quote:{cny_per_image:.3,checked_at:at,source_url:"https://ark.volcengine.com/docs/model-pricing",max_estimated_cny:.4}});
const draft = () => ({...validate({plan:brief()}),revision:1,status:"prepared"});
function event(state: any,action: string,extra = {}) {return transition({state,action,observed_at:at,expected_revision:1,...extra}).state;}
const result = () => ({plan_digest:draft().plan_digest,model:IMAGE_MODEL,full_decode:"passed",width:1280,height:720,usage:{generated_images:1,output_tokens:3600},
  files:{"original.png":{sha256:"a".repeat(64),bytes:1200},"generated.png":{sha256:"b".repeat(64),bytes:1200}}});

test("one-image quotation preserves exact request bounds, not invoice or permission",()=>{
  const checked=validate({plan:brief()});assert.equal(checked.estimate.images,1);assert.equal(checked.estimate.cny,.3);assert.equal(checked.publish_authorized,false);
  const same={...brief(),title:"Changed meaning"};assert.notEqual(checked.plan_digest,validate({plan:same}).plan_digest);
  for(const change of [{width:512},{height:720.1},{references:[{path:"../private.png"}]},{quote:{...brief().quote,max_estimated_cny:.1}},{model:"other"},{extra:true}]) assert.throws(()=>validate({plan:{...brief(),...change}}));
  const ref={path:"reference.png",sha256:"c".repeat(64),source_ref:"synthetic:handoff",visibility:"public_safe"};
  assert.equal(validate({plan:{...brief(),references:[ref]}}).plan.references.length,1);
  for(const change of [{visibility:"private"},{source_ref:"https://private.example/raw"},{path:"/local/private.png"},{sha256:"changed"}]) assert.throws(()=>validate({plan:{...brief(),references:[{...ref,...change}]}}));
});
test("unknown synchronous submission is not a cloud job or a new paid attempt",()=>{
  const sending=event(draft(),"submit"), unknown=event(sending,"unknown");
  assert.equal(unknown.status,"submission_unknown");assert.throws(()=>event(unknown,"submit"));assert.throws(()=>event(unknown,"check"));
  assert.throws(()=>event(draft(),"submit",{observed_at:"2026-10-21T12:00:00Z"}));
  assert.throws(()=>event({...draft(),plan_digest:"changed"},"submit"));
  assert.throws(()=>event(draft(),"submit",{expected_revision:2}));
});
test("saved response can repair caller loss but never replace original bytes",()=>{
  const unknown=event(event(draft(),"submit"),"unknown"), ready=event(unknown,"collect",{result:result()});
  assert.equal(ready.status,"ready");assert.equal(ready.result.invoice,"unknown");assert.equal(ready.result.creative_review,"not_tested");
  for(const mutation of [{usage:{generated_images:2}},{model:"other"},{width:1920},{full_decode:"unknown"},{plan_digest:"changed"}]) assert.throws(()=>event(unknown,"collect",{result:{...result(),...mutation}}));
  const replacement=result();replacement.files["original.png"].sha256="c".repeat(64);
  assert.throws(()=>event(ready,"collect",{result:replacement}));
  assert.throws(()=>event(draft(),"collect",{result:result()}));
});
