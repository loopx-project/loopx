import type {PrivateConversation} from "../../data/chat";

/** Identity preparation is deliberately distinct from enabling scheduled work. */
export function StewardManagedMode({state, zh, busy, available, onPrepare}: {
  state: PrivateConversation["managed_steward"]; zh: boolean; busy: boolean;
  available: boolean; onPrepare: () => void;
}) {
  if (state === undefined) return null;
  return <details>
    <summary>{zh ? "原生周期监控" : "Native periodic monitoring"}</summary>
    {!state.available ? <p role="status">{zh ? "管家工作身份暂不可用；请检查原会话和连接后重试。" : "The steward identity is unavailable. Check the original conversation and connection, then retry."}</p>
      : state.prepared ? <p role="status">{zh ? "管家身份已准备。定时任务尚未启用。" : "The steward identity is prepared. Scheduled tasks are not enabled."}</p>
      : <>
        <p>{zh ? "为当前管家准备一个可见的 LoopX 工作身份，保留原对话和权限。此步骤不会启动定时任务或模型。" : "Prepare one visible LoopX identity for this steward, retaining its original conversation and permissions. This step starts neither scheduled tasks nor a model."}</p>
        <button type="button" disabled={busy || !available} onClick={onPrepare}>
          {zh ? "准备管家身份" : "Prepare steward identity"}
        </button>
      </>}
  </details>;
}
