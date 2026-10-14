import {useEffect,useRef,useState} from "react";
import {fetchGoalDirection,updateGoalDirection,recoverGoalDirection,savedDirectionSchema,
  type DirectionMaterial,type DirectionRequest,type DirectionView} from "../../data/goal-direction";
import {useWorkspaceI18n} from "./i18n";

const blank=():DirectionMaterial=>({material_id:"",source_path:"",purpose:"",topics:[],required:true,boundary:"local_private"});
/** The operator can declare sources and inspect any registered Agent's state.
 * This interaction never acknowledges consumption on an Agent's behalf. */
export function GoalDirectionSettings({goalId}:{goalId:string}){
  const {locale}=useWorkspaceI18n(),zh=locale==="zh-CN";
  const w=(cn:string,en:string)=>zh?cn:en;
  const [open,setOpen]=useState(false),[view,setView]=useState<DirectionView|null>(null),[agent,setAgent]=useState("");
  const [draft,setDraft]=useState<DirectionMaterial[]>([blank()]),[request,setRequest]=useState<DirectionRequest|null>(null);
  const [attempted,setAttempted]=useState(false),[invalid,setInvalid]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null);
  const generation=useRef(0),flight=useRef<number|null>(null),selected=useRef(agent);selected.current=agent;
  const key=`loopx-direction:${goalId}`;
  async function load(){
    if(flight.current===generation.current)return;
    const observation=generation.current;flight.current=observation;setBusy(true);setError(null);
    try{
      // Recover original-operation identity independently of whether today's
      // Goal/source can be read (for example after a lifetime was retired).
      setInvalid(true);
      const raw=sessionStorage.getItem(key);
      if(raw){const saved=savedDirectionSchema.parse(JSON.parse(raw));if(saved.request.goal_id!==goalId)throw Error("Saved Goal mismatch");
        setRequest(saved.request);setAttempted(saved.attempted);if(saved.request.parameters.materials)setDraft(saved.request.parameters.materials);}
      else{setRequest(null);setAttempted(false);}
      setInvalid(false);
      const current=await fetchGoalDirection(goalId,selected.current||undefined);
      if(observation!==generation.current)return;
      setView(current);
      if(!selected.current&&current.registered_agents.length)setAgent(current.registered_agents[0]);
    }catch(cause){if(observation===generation.current){setView(null);setError(cause instanceof Error?cause.message:w("方向资料暂不可用。","Direction materials are unavailable."));}}
    finally{if(flight.current===observation)flight.current=null;if(observation===generation.current)setBusy(false);}
  }
  useEffect(()=>{generation.current++;setView(null);setRequest(null);setAttempted(false);setInvalid(false);setError(null);setAgent("");setDraft([blank()]);selected.current="";
    if(open)void load();return()=>{generation.current++;};
    // Goal scope owns async responses; language changes do not issue reads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[goalId,open]);
  useEffect(()=>{if(!open||!agent)return;const observation=++generation.current;setBusy(false);void load();return()=>{if(observation===generation.current)generation.current++;};
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[agent]);
  function edit(index:number,patch:Partial<DirectionMaterial>){if(attempted||invalid)return;setDraft(rows=>rows.map((row,i)=>i===index?{...row,...patch}:row));setRequest(null);sessionStorage.removeItem(key);}
  async function submit(operation:"configure"|"disable",apply:boolean){
    if(!view&&!apply||invalid||flight.current===generation.current||apply&&!request)return;
    const observation=generation.current;flight.current=observation;setBusy(true);setError(null);
    const next=apply?request!:{goal_id:goalId,goal_instance_id:view!.goal_ref.goal_instance_id,operation,operation_id:`direction:${crypto.randomUUID()}`,
      parameters:{expected_provider_revision:view!.provider_revision,...(operation==="configure"?{materials:draft}:{})}} satisfies DirectionRequest;
    try{
      if(apply){sessionStorage.setItem(key,JSON.stringify({request:next,attempted:true}));setAttempted(true);}
      const result=await updateGoalDirection(next,apply);if(observation!==generation.current)return;
      if(!result.ok){
        if(result.reason_code==="direction_basis_changed"){
          sessionStorage.removeItem(key);setRequest(null);setAttempted(false);
          const current=await fetchGoalDirection(goalId,selected.current||undefined);if(observation===generation.current)setView(current);
          setError(result.reason??w("资料声明已变化，请重新检查。","Material declaration changed; review it again."));
          return;
        }
        throw Error(result.reason??w("未应用本次声明，请检查当前基线。","Declaration was not applied; inspect the current basis."));
      }
      if(!apply){
        if(!result.reviewed_request_sha256)throw Error(w("来源未提供可恢复的完整预览，请更新来源后重试。","The source did not provide a recoverable preview; update it and retry."));
        const saved={...next,preview_digest:result.authority_digest??undefined,reviewed_request_sha256:result.reviewed_request_sha256};sessionStorage.setItem(key,JSON.stringify({request:saved,attempted:false}));setRequest(saved);
      }
      else{sessionStorage.removeItem(key);setRequest(null);setAttempted(false);const current=await fetchGoalDirection(goalId,selected.current||undefined);if(observation===generation.current)setView(current);}
    }catch(cause){if(observation===generation.current){if(apply)setView(null);setError(cause instanceof Error?cause.message:w("结果未知，保留原操作进行恢复。","Outcome unknown; retain the original operation for recovery."));}}
    finally{if(flight.current===observation)flight.current=null;if(observation===generation.current)setBusy(false);}
  }
  async function recover(){
    if(!request||flight.current===generation.current)return;
    const observation=generation.current;flight.current=observation;setBusy(true);setError(null);
    try{const result=await recoverGoalDirection(request);if(observation!==generation.current)return;
      const receipt=result.receipts?.[0];if(result.status!=="found"||receipt?.operation_id!==request.operation_id||receipt.result.goal_ref.goal_instance_id!==request.goal_instance_id
        ||receipt.result.authority_digest!==request.preview_digest||receipt.request_sha256!==request.reviewed_request_sha256)throw Error(w("尚未确认原回执；不能据此断言未提交，请重试原操作。","Original receipt is unconfirmed; absence does not prove no commit. Retry the original operation."));
      sessionStorage.removeItem(key);setRequest(null);setAttempted(false);const current=await fetchGoalDirection(goalId,selected.current||undefined);if(observation===generation.current)setView(current);
    }catch(cause){if(observation===generation.current)setError(cause instanceof Error?cause.message:"Readback failed");}
    finally{if(flight.current===observation)flight.current=null;if(observation===generation.current)setBusy(false);}
  }
  const state=view?.projection?.direction_state;
  const stateLabel=state==="current"?w("已确认当前资料版本","Current revisions acknowledged"):state==="re_evaluation_required"?w("资料版本已变化，需要重新评估方向","Material revision changed; re-evaluate direction"):w("仍有资料未读取或不可用","Material unread or unavailable");
  return <details className="personal-cadence-settings" open={open} onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary>{w("方向依据与资料版本","Direction materials and revisions")}</summary>
    {open?<div className="personal-cadence-form">
      <p>{w("声明哪些资料约束当前方向。此处只读取和展示 Agent 的版本确认；不会替它确认已读，也不会修改任务、路线或目标验收。","Declare which materials constrain direction. This view observes each Agent's revision acknowledgements; it cannot acknowledge for them or change work, route or Goal acceptance.")}</p>
      {view?<><label className="personal-cadence-automation">{w("查看 Agent","View Agent")}<select aria-label={w("查看 Agent","View Agent")} value={agent} disabled={busy} onChange={e=>setAgent(e.target.value)}>
        <option value="">{w("选择 Agent","Choose Agent")}</option>{view.registered_agents.map(id=><option key={id}>{id}</option>)}</select></label>
        <p role="status">{view.enabled?stateLabel:w("方向资料未启用","Direction materials are disabled")}</p>
        <ul>{view.projection?.items.map(row=><li key={row.material_id}><strong>{row.material_id}</strong> · {row.state==="current"?w("版本已确认","Revision acknowledged"):row.state==="stale"?w("版本待更新","Revision needs update"):row.state==="inaccessible"?w("来源不可用","Source unavailable"):w("待读取","Read required")}
          <details><summary>{w("版本与回执","Revision and receipt")}</summary><p>{row.required_revision}</p><p>{row.receipt_ref??w("暂无回执","No receipt")}</p></details></li>)}</ul>
        <p>{w("资料版本一致不代表 Goal 验收通过。","Matching material revisions do not establish Goal acceptance.")}</p>
        {view.acceptance_basis.contract_revision!==null?<p>{w("当前验收合同版本：","Current acceptance contract revision: ")}{view.acceptance_basis.contract_revision}</p>:null}
        {view.authority_registry?<details><summary>{w("已声明资料","Declared materials")}</summary><ul>{view.authority_registry.goal_direction_requirements.map(row=><li key={row.requirement_id}>{row.material_id} · {row.purpose}</li>)}</ul></details>:null}
      </>:null}
      {view?<details><summary>{w("设置或更新资料","Configure or update materials")}</summary><div className="personal-cadence-form">
        <p>{w("路径仅用于当前项目的本次读取，不写入共享回执。更新时完整列出希望保留的资料。","Paths are used only for this read in the current project and are excluded from shared receipts. List the complete material set to retain when updating.")}</p>
        {draft.map((row,i)=><fieldset className="personal-capability-fields" key={i} disabled={busy||Boolean(request)||invalid}>
          <legend>{w(`资料 ${i+1}`,`Material ${i+1}`)}</legend>
          <label><span>{w("资料标识","Material id")}</span><input aria-label={w(`资料标识 ${i+1}`,`Material id ${i+1}`)} value={row.material_id} onChange={e=>edit(i,{material_id:e.target.value})}/></label>
          <label><span>{w("本地 Markdown 路径","Local Markdown path")}</span><input aria-label={w(`资料路径 ${i+1}`,`Material path ${i+1}`)} value={row.source_path} onChange={e=>edit(i,{source_path:e.target.value})}/></label>
          <label><span>{w("约束用途","Purpose")}</span><input aria-label={w(`资料用途 ${i+1}`,`Material purpose ${i+1}`)} value={row.purpose} onChange={e=>edit(i,{purpose:e.target.value})}/></label>
          <label><span>{w("主题，逗号分隔","Topics, comma separated")}</span><input value={row.topics.join(",")} onChange={e=>edit(i,{topics:e.target.value.split(",").map(v=>v.trim()).filter(Boolean)})}/></label>
          <label className="is-boolean"><span>{w("必需资料","Required material")}</span><input type="checkbox" checked={row.required} onChange={e=>edit(i,{required:e.target.checked})}/></label>
          <button type="button" disabled={draft.length===1} onClick={()=>setDraft(rows=>rows.filter((_,n)=>n!==i))}>{w("移除此项","Remove item")}</button>
        </fieldset>)}
        <button type="button" disabled={busy||Boolean(request)||invalid||draft.length>=64} onClick={()=>setDraft(rows=>[...rows,blank()])}>{w("添加资料","Add material")}</button>
        {!request?<button type="button" disabled={busy||invalid} onClick={()=>void submit("configure",false)}>{w("检查资料声明","Review material declaration")}</button>:null}
      </div></details>:null}
      {request?<div className="personal-cadence-actions">
        <p>{request.operation==="disable"?w("将停止方向投影，原声明和回执保留。","Stop direction projection while retaining declarations and receipts."):w("将启用本次完整资料声明及读取边界；不代表 Agent 已读取。","Enable this complete declaration and its read boundary; this does not mark an Agent as having read it.")}</p>
        <button type="button" disabled={busy||invalid||Boolean(view&&view.goal_ref.goal_instance_id!==request.goal_instance_id)} onClick={()=>void submit(request.operation,true)}>{attempted?w("重试原操作","Retry original operation"):w("确认应用声明","Apply reviewed declaration")}</button>
        {!attempted?<button type="button" disabled={busy} onClick={()=>{sessionStorage.removeItem(key);setRequest(null);}}>{w("返回修改","Edit draft")}</button>:<button type="button" disabled={busy} onClick={()=>void recover()}>{w("查阅原操作回执","Read original receipt")}</button>}
      </div>:view?.enabled?<button type="button" disabled={busy||invalid} onClick={()=>void submit("disable",false)}>{w("停用方向投影","Disable direction projection")}</button>:null}
      <button type="button" disabled={busy} onClick={()=>void load()}>{w("读取当前方向状态","Read current direction state")}</button>
      {error?<p role="alert" className="personal-machine-error">{error}</p>:null}
      {busy?<p role="status">{w("正在读取或提交…","Reading or submitting…")}</p>:null}
    </div>:null}
  </details>;
}
