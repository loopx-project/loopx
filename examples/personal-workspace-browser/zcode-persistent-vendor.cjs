// Isolated vendor protocol stub; API, bridge and controller are production code.
const fs = require('node:fs'), path = require('node:path'), readline = require('node:readline');
const storage = process.env.ZCODE_STORAGE_DIR;
fs.mkdirSync(storage, {recursive:true});
const file = path.join(storage,'vendor.json');
let state = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file)) : {session:'persistent-session',target:null,revision:0,model:null,running:false};
const persist = () => fs.writeFileSync(file,JSON.stringify(state));
const snapshot = () => ({protocol:{name:'ZCode Protocol',version:1},session:{sessionId:state.session},settings:{model:{current:state.model,available:[{ref:{providerId:'isolated',modelId:'fixture'},label:'Isolated vendor',reasoning:{levels:[],defaultLevel:null}}]}},projection:{status:state.running?'running':'idle',target:state.target&&{...state.target,sessionId:state.session}},runtime:{stateRevision:state.revision}});
readline.createInterface({input:process.stdin}).on('line',line => {
 const r=JSON.parse(line);if(!r.method)return;const p=r.params||{};let result;
 if(r.method==='runtime/capabilities')result={independentPlanState:true};
 else if(['session/create','session/resume','session/read'].includes(r.method))result=snapshot();
 else if(['session/setModel','session/goal'].includes(r.method)) {
  if(p.expectedRevision!==state.revision)throw Error('Vendor CAS mismatch');
  if(r.method==='session/setModel')state.model=p.model;
  else if(p.action==='set'){state.target={targetId:'persistent-target',objective:p.objective,status:'active'};state.running=true;}
  else if(p.action==='pause'){if(state.target)state.target.status='paused';state.running=false;}
  else if(p.action==='resume'){state.target.status='active';state.running=true;}
  else if(p.action==='clear'){if(state.running)throw Error('Clear before pause');state.target=null;}
  else throw Error('Unknown vendor action');
  state.revision++;persist();result=r.method==='session/goal'?{snapshot:snapshot(),startedTurn:['set','resume'].includes(p.action)}:snapshot();
 } else throw Error('Unexpected vendor method');
 fs.appendFileSync(path.join(storage,'events.jsonl'),JSON.stringify({method:r.method,action:p.action,revision:state.revision,running:state.running})+'\n');
 process.stdout.write(JSON.stringify({id:r.id,result})+'\n');
});
