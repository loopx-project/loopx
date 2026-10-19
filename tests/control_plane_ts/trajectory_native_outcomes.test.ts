import assert from "node:assert/strict";
import test from "node:test";
import {prepareTrajectoryAssessment, type TrajectoryInput} from "../../loopx/control_plane/capabilities/trajectory_assessment.ts";

const input: TrajectoryInput = {schema_version:"trajectory_assessment_input_v1",
  scope:{goal_id:"g",case_id:"c",run_id:"r",revision:"rev",window:"complete"}, objective:"Minimize native error",
  selection:{rule:"all",population_episodes:1,exhaustive:true},
  sources:[{id:"s",artifact:"native.json",artifact_sha256:"a".repeat(64),first_line:1,text:"first\nold delayed result\nnew retained result"}],
  episodes:[{id:"e",question:"Does the alternative help?",source_ids:["s"],eligible_metrics:["benefit_test"]}]};
const config = {model:"fixture",effort:"max",service_tier:"fast"};
function capture() {return {schema_version:"trajectory_native_outcomes_v1",scope:{...input.scope},
  start:"2026-01-01T00:00:00Z",end:"2026-01-01T00:10:00Z",complete:true,
  metric:{name:"error",unit:"points",higher_is_better:false},retained_result_id:"result-3",
  samples:[2,5,1].map((value,i)=>({submission_index:i+1,result_id:`result-${i+1}`,candidate_ref:`candidate-${i+1}`,
    submitted_at:`2026-01-01T00:0${i}:00Z`,observed_at:`2026-01-01T00:0${i===1?5:3}:00Z`,valid:true,value,
    citation:{source_id:"s",first_line:i+1,last_line:i+1}}))};}
function project(value: unknown) {return prepareTrajectoryAssessment({input,config,native_outcomes:value}).native_outcomes!;}

test("native facts preserve first quality, actual retention and submission order independently of delayed feedback",()=>{
  const native=project(capture());
  assert.equal(native.initial?.value,2); assert.equal(native.best_valid?.value,1);
  assert.equal(native.retained?.result_id,"result-3"); assert.equal(native.final_valid?.result_id,"result-3");
  assert.equal(native.sample_count,3); assert.equal(native.valid_count,3);
  assert.equal(Object.hasOwn(native,"retained_improvement"),false,"An absent comparison preserves the old response shape");
  assert.equal(prepareTrajectoryAssessment({input,config,native_outcomes:capture()}).analysis_key,
    prepareTrajectoryAssessment({input,config}).analysis_key,"native readback does not rerun unchanged analysis");
  assert.equal(Object.hasOwn(prepareTrajectoryAssessment({input,config}),"native_outcomes"),false);
});
test("a declared baseline measures adopted improvement in native units, including lower-is-better and losses",()=>{
  const raw={...capture(), comparison:{baseline_result_id:"result-1",retention_citation:{source_id:"s",first_line:3,last_line:3}}};
  let native=project(raw);
  assert.equal(native.retained_improvement?.value,1);
  assert.equal(native.retained_improvement?.baseline?.value,2);
  raw.metric.higher_is_better=true;
  assert.equal(project(raw).retained_improvement?.value,-1,"An adopted regression is negative, not clamped away");
  raw.complete=false;raw.samples=raw.samples.slice(1);raw.comparison.baseline_result_id="result-2";
  native=project(raw);
  assert.equal(native.initial,null,"An explicit comparison does not invent a complete prefix");
  assert.equal(native.retained_improvement?.value,-4);
});
test("rejecting a candidate and retaining the baseline realizes zero, not an avoided-loss benefit",()=>{
  const raw={...capture(), comparison:{baseline_result_id:"result-1",retention_citation:{source_id:"s",first_line:1,last_line:1}}};
  raw.metric.higher_is_better=true;raw.retained_result_id="result-1";
  assert.equal(project(raw).retained_improvement?.value,0);
  assert.equal(project(raw).retained_improvement?.final_candidate_delta,-1);
  assert.equal(project(raw).best_valid?.value,5,"Best observed candidate does not override actual retention");
});
test("unknown baselines and unobserved retention cannot produce realized improvement",()=>{
  const raw:any={...capture(), comparison:{baseline_result_id:null,retention_citation:{source_id:"s",first_line:3,last_line:3}}};
  assert.equal(project(raw).retained_improvement?.value,null,"No fallback to submission one");
  raw.comparison.baseline_result_id="result-1";raw.comparison.retention_citation=null;
  assert.equal(project(raw).retained_improvement?.value,null);
  raw.comparison.retention_citation={source_id:"s",first_line:3,last_line:3};raw.retained_result_id=null;
  assert.equal(project(raw).retained_improvement?.value,null);
  assert.equal(project(raw).retained_improvement?.final_candidate_delta,1,"Observed candidate change is separate from retention");
});
test("an archived result may have unknown submission time without inventing a clock or losing its measurement",()=>{
  const raw:any={...capture(), comparison:{baseline_result_id:"result-1",retention_citation:{source_id:"s",first_line:3,last_line:3}}};
  raw.samples[0].submitted_at=null;
  assert.equal(project(raw).retained_improvement?.value,1);
  assert.equal(project(raw).retained_improvement?.baseline?.submitted_at,null);
  raw.samples[0].observed_at="2025-12-31T23:59:59Z";
  assert.throws(()=>project(raw),/outside its capture/);
});
test("comparisons reject foreign, invalid or later baselines, unbound retention evidence and overflow",()=>{
  for (const mutate of [
    (x:any)=>x.comparison.baseline_result_id="foreign",
    (x:any)=>{x.samples[0].valid=false;x.samples[0].value=null;},
    (x:any)=>{x.comparison.baseline_result_id="result-3";x.retained_result_id="result-1";},
    (x:any)=>x.comparison.retention_citation.source_id="foreign",
    (x:any)=>x.comparison.retention_citation.last_line=4,
    (x:any)=>delete x.comparison.baseline_result_id,
    (x:any)=>delete x.comparison.retention_citation,
    (x:any)=>{x.samples[0].value=-Number.MAX_VALUE;x.samples[2].value=Number.MAX_VALUE;},
  ]) {
    const raw:any={...capture(), comparison:{baseline_result_id:"result-1",retention_citation:{source_id:"s",first_line:3,last_line:3}}};
    mutate(raw);assert.throws(()=>project(raw));
  }
});
test("higher-is-better is explicit; best measurement does not invent retained selection",()=>{
  const raw=capture();raw.metric.higher_is_better=true;raw.retained_result_id="result-1";
  raw.metric.name="Native throughput (%)"; raw.metric.unit="ops/s";
  const native=project(raw);assert.equal(native.best_valid?.value,5);assert.equal(native.retained?.value,2);
  assert.equal(native.metric.unit,"ops/s");assert.equal(native.metric.name,"Native throughput (%)");
});
test("invalid first measurements and incomplete prefixes remain unknown, never zero",()=>{
  const raw=capture() as unknown as Record<string,any>;
  raw.samples[0].valid=false;raw.samples[0].value=null;
  assert.equal(project(raw).initial,null);assert.equal(project(raw).valid_count,2);
  raw.complete=false;raw.samples=raw.samples.slice(1);
  assert.equal(project(raw).initial,null);assert.equal(project(raw).final_valid?.value,1);
  raw.samples[0].valid=false;raw.samples[0].value=0;assert.throws(()=>project(raw),/validity/);
});
test("wrong source, duplicate results, old scope and invented retention cannot borrow a pack",()=>{
  for(const mutate of [
    (x:any)=>x.scope.run_id="foreign", (x:any)=>x.scope.revision="old",
    (x:any)=>x.samples[1].result_id=x.samples[0].result_id,
    (x:any)=>x.samples[1].submission_index=1,
    (x:any)=>x.samples[0].citation.last_line=4,
    (x:any)=>x.samples[0].citation.source_id="foreign",
    (x:any)=>x.samples[0].observed_at="2026-01-01T00:11:00Z",
    (x:any)=>x.retained_result_id="invented",
    (x:any)=>x.samples[0].value=Infinity,
  ]) {const x=capture();mutate(x);assert.throws(()=>project(x));}
});
test("UTC dates must name real calendar instants, preserving legal fractional seconds",()=>{
  for (const date of ["2026-02-30T00:00:00Z", "2025-02-29T00:00:00Z",
    "2026-04-31T00:00:00Z", "2026-01-01T24:00:00Z"]) {
    for (const field of ["start", "end", "submitted_at", "observed_at"]) {
      const raw = capture() as any;
      if (field === "start" || field === "end") raw[field] = date;
      else raw.samples[0][field] = date;
      assert.throws(()=>project(raw), /UTC timestamp/, `${field}: ${date}`);
    }
  }
  for (const fraction of ["", ".1", ".12", ".123"]) {
    const raw = capture();
    raw.start = `2024-02-29T00:00:00${fraction}Z`;
    raw.end = "2024-03-01T00:00:00Z";
    for (const sample of raw.samples) {
      sample.submitted_at = sample.observed_at = raw.start;
    }
    assert.equal(project(raw).initial?.submitted_at, raw.start);
  }
});
