import { ChevronUp, Download, RefreshCw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useWorkspaceI18n } from "./i18n";

type Phase = "idle" | "runtime_required" | "runtime_pairing_required" | "connecting" | "service_error" | "checking" | "available" | "up_to_date" | "downloading" | "installing_app" | "installing_runtime" | "restart_required" | "ready" | "error";
type UpdateState = { phase: Phase; details?: { version?: string; channel?: string; received?: number; total?: number; code?: string } };
type RuntimeSelection = { explicit?: boolean; remembered?: boolean; bundled_repair_available?: boolean };
type UpdateStatus = { state: UpdateState | null; app_version: string; rollback_available?: boolean; runtime_selection?: RuntimeSelection };
type DesktopWindow = Window & { __TAURI__?: { core: { invoke: <T>(command: string, args?: Record<string, string>) => Promise<T> } } };
const workingPhases: Phase[] = ["checking", "connecting", "downloading", "installing_app", "installing_runtime"];

const updateErrors: Record<string, [string, string]> = {
  runtime_selection_explicit: ["当前运行时由其他安装方式或启动参数管理。请通过原安装方式维护它；App 不会覆盖该安装。", "This runtime is managed by another installer or launch override. Maintain it through that owner; the App will not overwrite it."],
  desktop_status_unavailable: ["无法读取 App 更新状态。请重启 App 后再试；若仍失败，请重新安装最新 App。", "Cannot read the App update status. Restart the App; if this persists, reinstall the latest App."],
  update_feed_unavailable: ["此通道的更新源尚未就绪或暂时不可用。可稍后重新检查，当前版本仍可继续使用。", "This channel's update feed is not ready or temporarily unavailable. Check again later; you can keep using this version."],
  update_feed_invalid: ["更新源格式异常。请稍后重新检查。", "The update feed is invalid. Check again later."],
  update_platform_unavailable: ["此通道尚无适用于本机的更新包。", "This channel has no update package for this platform."],
  update_check_timeout: ["检查更新超时。请稍后重试。", "The update check timed out. Try again later."],
  update_network_failed: ["无法连接更新服务器。请检查网络后重试。", "Cannot reach the update server. Check your connection and retry."],
  update_download_or_signature_failed: ["更新包下载或签名校验失败，尚未安装。请重新检查更新。", "Download or signature verification failed; the update was not installed. Check for updates again."],
};
function failedUpdate(error: unknown, fallback = "update_failed"): UpdateState {
  return { phase: "error", details: { code: typeof error === "string" && Object.hasOwn(updateErrors, error) ? error : fallback } };
}

export function DesktopUpdate() {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLElement>(null);
  const [channel, setChannel] = useState("stable");
  const [state, setState] = useState<UpdateState>({ phase: "idle" });
  const [version, setVersion] = useState("");
  const [rollbackAvailable, setRollbackAvailable] = useState(false);
  const [runtimeSelection, setRuntimeSelection] = useState<RuntimeSelection>();
  const busy = useRef(false);
  const statusGeneration = useRef(0);
  const invoke = (window as DesktopWindow).__TAURI__?.core.invoke;
  const working = workingPhases.includes(state.phase);
  const repairAvailable = runtimeSelection?.explicit !== true && runtimeSelection?.bundled_repair_available !== false;

  function readStatus(value: UpdateStatus, includeState = true) {
    setVersion(value.app_version);
    setRollbackAvailable(value.rollback_available === true);
    setRuntimeSelection(value.runtime_selection);
    if (includeState && value.state?.phase) setState(value.state);
  }

  function commitStatus(value: UpdateStatus, generation: number) {
    if (generation !== statusGeneration.current) return false;
    const stateIsCurrent = !busy.current || !value.state?.phase || workingPhases.includes(value.state.phase);
    readStatus(value, stateIsCurrent);
    return true;
  }

  useEffect(() => {
    const element = panel.current;
    if (!element) return;
    const sync = () => setOpen(element.matches(":popover-open"));
    element.addEventListener("toggle", sync);
    return () => element.removeEventListener("toggle", sync);
  }, []);

  useEffect(() => {
    if (!invoke) return;
    let alive = true;
    const generation = ++statusGeneration.current;
    void invoke<UpdateStatus>("desktop_update_status").then((value) => {
      if (!alive || !commitStatus(value, generation)) return;
      const selected = value.state?.details?.channel ?? (value.app_version.includes("-main.") ? "main" : "stable");
      setChannel(selected);
      if (!value.state?.phase) {
        const actionGeneration = ++statusGeneration.current;
        void invoke<UpdateState>("desktop_update", { action: "check", channel: selected }).then((next) => {
          if (alive && actionGeneration === statusGeneration.current) setState(next);
        }).catch((error: unknown) => {
          if (alive && actionGeneration === statusGeneration.current) setState(failedUpdate(error));
        });
      }
    }).catch(() => {
      if (alive && generation === statusGeneration.current) setState(failedUpdate(null, "desktop_status_unavailable"));
    });
    return () => { alive = false; statusGeneration.current++; };
  }, [invoke]);

  useEffect(() => {
    if (!invoke || !working) return;
    const timer = window.setInterval(() => {
      const generation = ++statusGeneration.current;
      void invoke<UpdateStatus>("desktop_update_status").then((value) => {
        commitStatus(value, generation);
      }).catch(() => {});
    }, 1000);
    return () => window.clearInterval(timer);
  }, [invoke, working]);

  async function run(action: "check" | "apply" | "repair" | "restart" | "rollback" | "forget_runtime_selection") {
    if (!invoke || busy.current) return;
    busy.current = true;
    statusGeneration.current++;
    setState({ phase: action === "check" ? "checking" : action === "repair" ? "installing_runtime" : action === "forget_runtime_selection" ? "connecting" : "downloading" });
    try {
      if (!version) {
        const generation = ++statusGeneration.current;
        commitStatus(await invoke<UpdateStatus>("desktop_update_status"), generation);
      }
      const next = await invoke<UpdateState>("desktop_update", { action, channel });
      statusGeneration.current++;
      setState(next);
    }
    catch (error: unknown) {
      statusGeneration.current++;
      setState(failedUpdate(error, version ? "update_failed" : "desktop_status_unavailable"));
    }
    finally { busy.current = false; }
  }
  const message: Record<Phase, string> = {
    service_error: zh ? "运行时已安装，但服务尚未连接。可重试更新、修复或恢复上版。" : "Runtime installed, but services are unavailable. Retry updates, repair, or restore the previous version.",
    runtime_pairing_required: zh ? "旧版启动器未能自动选择可用组件。请检查 App 更新，或通过恢复入口重新连接。" : "The older launcher could not select a usable runtime. Check for an App update or reconnect through recovery.",
    idle: zh ? "App 启动时会检查并安装当前通道的可用更新；也可在此手动检查。" : "On launch, the App checks and installs available updates on its current channel. You can also check here.",
    runtime_required: zh ? "请完成匹配组件安装，或检查 App 更新。" : "Install matching components or check for an App update.",
    connecting: zh ? "正在连接更新后的服务…" : "Connecting to updated services…",
    checking: zh ? "正在检查更新…" : "Checking for updates…",
    available: zh ? "新版本已就绪，一次更新 App 与匹配的运行时。" : "Update the App and its matching runtime together.",
    up_to_date: zh ? "当前通道暂无更新。" : "No newer update on this channel.",
    downloading: zh ? "正在下载并校验签名…" : "Downloading and verifying signature…",
    installing_app: zh ? "正在安装 App，请保持窗口打开。" : "Installing the App. Keep this window open.",
    installing_runtime: zh ? "正在安装匹配的运行时，请稍候…" : "Installing the matching runtime…",
    restart_required: zh ? "重启后将自动完成运行时安装与服务连接。" : "Restart to finish runtime installation and reconnect services.",
    ready: zh ? "更新完成，服务已就绪。" : "Update completed; services are ready.",
    error: updateErrors[state.details?.code ?? ""]?.[zh ? 0 : 1] ?? (zh ? "更新未完成。请重试；启动失败可尝试修复当前版本。" : "Update incomplete. Retry; repair this version if startup fails."),
  };
  const label = working ? (zh ? "正在更新…" : "Updating…") : state.phase === "available" ? (zh ? "有可用更新" : "Update available") : state.phase === "restart_required" ? (zh ? "重启完成更新" : "Restart to finish") : state.phase === "error" ? (zh ? "更新需重试" : "Retry update") : (zh ? "更新 LoopX" : "Update LoopX");
  return <div className="personal-desktop-update">
    <button className="personal-update-trigger" type="button" aria-expanded={open} aria-controls="desktop-update-panel" onClick={(event) => {
      panel.current?.style.setProperty("bottom", `${window.innerHeight - event.currentTarget.getBoundingClientRect().top + 8}px`);
      panel.current?.togglePopover();
    }}>
      <Download size={16} aria-hidden="true" />
      <span>{label}</span><ChevronUp size={14} aria-hidden="true" />
    </button>
    <section ref={panel} popover="auto" id="desktop-update-panel" className="personal-update-panel" aria-label={zh ? "LoopX 更新" : "LoopX updates"}>
      <header><strong>{zh ? "LoopX 更新" : "LoopX updates"}</strong><button type="button" aria-label={zh ? "关闭更新面板" : "Close updates"} onClick={() => panel.current?.hidePopover()}><X size={16} aria-hidden="true" /></button></header>
      <small>{version} · {channel === "main" ? (zh ? "main 预览版" : "main preview") : (zh ? "稳定版" : "Stable")}</small>
      {state.details?.version ? <p>{zh ? "目标版本：" : "Target: "}{state.details.version}</p> : null}
      <p role="status" aria-live="polite">{working ? <RefreshCw className="is-spinning" size={14} aria-hidden="true" /> : null}{message[state.phase]}</p>
      {state.phase === "downloading" && state.details?.total ? <progress aria-label={zh ? "下载进度" : "Download progress"} max={state.details.total} value={state.details.received ?? 0} /> : null}
      {invoke ? <div className="personal-update-actions">
        {state.phase === "restart_required" ? <button type="button" onClick={() => void run("restart")}>{zh ? "重启完成更新" : "Restart to finish"}</button> : <>
          <button type="button" disabled={working} onClick={() => void run("check")}>{zh ? "检查更新" : "Check for updates"}</button>
          {state.phase === "available" ? <button type="button" onClick={() => void run("apply")}>{zh ? "更新并准备重启" : "Install update"}</button> : null}
        </>}
      </div> : <p>{zh ? "请在 LoopX App 中更新；浏览器自身无需安装包。" : "Update from the LoopX App; the browser needs no installer."}</p>}
      <details><summary>{zh ? "高级选项" : "Advanced options"}</summary>
      <label>{zh ? "更新通道" : "Update channel"}
        <select disabled={working || state.phase === "restart_required"} value={channel} onChange={(event) => { setChannel(event.target.value); setState({ phase: "idle" }); }}>
          <option value="stable">{zh ? "稳定版（推荐）" : "Stable (recommended)"}</option>
          <option value="main">{zh ? "main 预览版" : "main preview"}</option>
        </select>
      </label>
      <p>{zh ? "App 与匹配的 CLI 一起更新，服务可能短暂断开。不删除 Goal 数据。" : "Updates the App and matching CLI. Services may briefly disconnect. Goal data is not deleted."}</p>
      {invoke ? <>
        <p>{repairAvailable ? (zh ? "启动失败时，可重装当前 App 随附的运行时。" : "If startup fails, reinstall this App's bundled runtime.") : updateErrors.runtime_selection_explicit[zh ? 0 : 1]}</p>
        <button disabled={working || state.phase === "restart_required" || !repairAvailable} type="button" onClick={() => void run("repair")}>{zh ? "修复当前版本" : "Repair this version"}</button>
        {runtimeSelection?.remembered === true && runtimeSelection.explicit !== true ? <>
          <p>{zh ? "清除后会重新检测已安装的运行时，不删除该安装。" : "Forgetting the choice rediscovers installed runtimes without deleting them."}</p>
          <button disabled={working || state.phase === "restart_required"} type="button" onClick={() => void run("forget_runtime_selection")}>{zh ? "清除记住的运行时选择" : "Forget runtime choice"}</button>
        </> : null}
      </> : null}
      {invoke && rollbackAvailable ? <details><summary>{zh ? "恢复上个版本" : "Restore previous version"}</summary><p>{zh ? "将恢复已保留的 App 和它的运行时，需要重启。" : "Restore the retained App and its runtime, then restart."}</p><button disabled={working} type="button" onClick={() => void run("rollback")}>{zh ? "确认恢复上版" : "Restore previous version"}</button></details> : null}
      </details>
    </section>
  </div>;
}
