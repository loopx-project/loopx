const panel = document.querySelector("main");
const status = document.querySelector("#status");
const bootElapsed = document.querySelector("#boot-elapsed");
const bootDetail = document.querySelector("#boot-detail");
const pageStarted = performance.now();
let lastTiming = null;
let timingObservedAt = pageStarted;
let bootState = null;
function renderStartup(result) {
  const state = result?.state;
  bootState = state;
  if (Number.isFinite(result?.startup?.elapsed_ms) && result.startup.elapsed_ms >= 0) {
    lastTiming = result.startup.elapsed_ms;
    timingObservedAt = performance.now();
  }
  const titles = {
    installing_runtime: "正在准备所需组件",
    checking: "正在检查更新",
    downloading: "正在下载更新",
    installing_app: "正在更新 LoopX",
    connecting: "正在打开工作区",
    ready: "本地服务已就绪，正在打开工作区",
    service_error: "本地服务连接失败，正在等待重试",
  };
  if (Object.hasOwn(titles, state?.phase)) {
    status.textContent = titles[state.phase];
    panel.dataset.state = "loading";
    panel.setAttribute("aria-busy", "true");
  }
  updateStartupElapsed();
}
function updateStartupElapsed() {
  if (["error", "runtime_required"].includes(bootState?.phase)) {
    bootElapsed.textContent = "等待恢复";
    bootDetail.textContent = "启动已停止等待。可在下方恢复，不必反复重开 App。";
    return;
  }
  const elapsed = lastTiming === null ? performance.now() - pageStarted : lastTiming + performance.now() - timingObservedAt;
  const seconds = Math.floor(elapsed / 1000);
  bootElapsed.textContent = `${lastTiming === null ? "此页面已等待" : "启动已用时"} ${seconds} 秒`;
  bootDetail.textContent = bootState?.phase === "installing_runtime"
    ? "正在准备最新可用组件，完成后会自动打开工作区。"
    : bootState?.phase === "service_error"
      ? "启动器会自动重试；可展开「恢复与更新」查看诊断。"
      : seconds >= 15
        ? "启动用时较长。当前步骤尚未完成，可展开「恢复与更新」查看诊断。"
        : "正在准备最新可用版本。";
}
setInterval(updateStartupElapsed, 1000);
window.loopxBootFailed = (message) => {
  if (bootState?.phase === "connecting") return;
  panel.dataset.state = "error";
  panel.setAttribute("aria-busy", "false");
  status.textContent = message;
};
window.loopxBootRetrying = () => {
  if (["error", "runtime_required"].includes(bootState?.phase)) return;
  panel.dataset.state = "loading";
  panel.setAttribute("aria-busy", "true");
  status.textContent = "正在重新连接本地控制面";
};
const update = document.querySelector("#update");
const channel = document.querySelector("#channel");
const repair = document.querySelector("#repair");
const rollback = document.querySelector("#rollback");
const updateStatus = document.querySelector("#update-status");
const forgetSelection = document.querySelector("#forget-selection");
let nextAction = "check";
let working = false;
let actionInFlight = false;
let statusGeneration = 0;
let channelInitialized = false;
let runtimeExplicit = false;
let bundledRepairAvailable = true;
const updateWorkingPhases = ["checking","downloading","installing_app","installing_runtime","connecting"];
document.querySelector("#retry").onclick = () => location.reload();
channel.onchange = () => { channelInitialized = true; render({phase:"idle"}); };
const labels = {
  idle: "检查当前通道，不会自动安装。",
  service_error: "运行时已安装，但服务尚未连接。可检查更新、修复或恢复上版；连接仍会自动重试。",
  runtime_required: "无法准备可用组件。可检查 App 更新，或点击修复后重新连接。",
  checking: "正在检查更新…",
  available: "App 与匹配运行时可一起更新。",
  up_to_date: "当前通道暂无更新。",
  downloading: "正在下载并校验签名…",
  installing_app: "正在安装 App，请保持窗口打开。",
  installing_runtime: "正在准备所需组件，请稍候…",
  connecting: "正在连接更新后的服务…",
  restart_required: "请重启 App，继续完成更新。",
  ready: "更新完成，正在打开工作区。",
  error: "更新未完成。请重试检查，或修复当前版本。Goal 数据不会被删除。",
};
const errors = {
  desktop_status_unavailable: "无法读取 App 诊断状态。请重启 App；若仍失败，请重新安装完整 App。",
  runtime_setup_required: "App 与本机运行时不匹配，或找不到安装身份。请修复当前版本，成功后重启。",
  runtime_bundle_missing: "App 缺少配套运行时文件。请重新下载完整 App。",
  runtime_bundle_invalid: "App 配套运行时校验失败。请重新下载完整 App。",
  runtime_installer_unavailable: "无法启动安装程序。请检查系统是否提供 bash（Windows 为 PowerShell）。",
  runtime_install_failed: "运行时安装失败。请展开诊断信息，提供错误码以便排查。",
  runtime_install_timeout: "运行时安装超过十分钟，已停止。请检查网络和安装依赖后重试。",
  runtime_identity_mismatch: "安装已结束，但 App 仍选中了不同运行时。请检查是否设置了 LOOPX_BIN。",
  runtime_identity_unavailable: "所选运行时尚不能验证。请用它的安装方式更新 CLI，或清除记住的选择后重新检测。App 保留当前安装。",
  runtime_selection_invalid: "记住的运行时选择无法读取。清除选择后，App 会重新检测本机 CLI。",
  runtime_selection_unavailable: "所选运行时无法读取。请恢复该安装，或清除选择后重新检测。",
  runtime_selection_explicit: "当前 App 使用单独选择的运行时，无法用自带组件修复。请通过原安装方式维护它，或先清除选择。开发启动参数 LOOPX_BIN 仍优先。",
  runtime_staging_failed: "无法创建安装临时目录。请检查磁盘空间及写入权限。",
  update_state_unavailable: "无法读写更新状态。请检查 App 数据目录的权限和磁盘空间。",
  update_state_invalid: "更新状态无法读取。请保留诊断信息并反馈问题。",
  app_update_incomplete: "App 更新尚未完成，无法安装配套运行时。请重新安装目标 App。",
  update_feed_unavailable: "此通道的更新源尚未就绪或暂时不可用。可稍后重新检查。",
  update_feed_invalid: "更新源格式异常。请稍后重新检查。",
  update_platform_unavailable: "此通道尚无适用于本机的更新包。",
  update_check_timeout: "检查更新超时。请稍后重试。",
  update_network_failed: "无法连接更新服务器。请检查网络后重试。",
  update_download_or_signature_failed: "更新包下载或签名校验失败，尚未安装。请重新检查更新。",
  app_install_failed: "App 安装未能完成，本次更新未生效；已确认当前版本完好且运行时可用，可直接重启继续使用，或重新检查更新后再试。",
  app_install_incomplete: "App 安装中断，且无法确认当前版本是否完整，请勿直接重启。请在恢复与更新面板还原上一版本（或重新安装）后再试。",
  runtime_pairing_required: "旧版本未能自动选择可用组件。请检查 App 更新，或通过恢复入口重新连接。",
  backup_failed: "无法备份当前版本，更新已停止。请检查磁盘空间后重试。",
};
function codeText(code, phase) {
  if (runtimeExplicit && ["runtime_identity_unavailable", "runtime_selection_explicit"].includes(code)) {
    return "启动参数 LOOPX_BIN 固定了当前运行时。请通过该安装方式修复它，或移除、修正 LOOPX_BIN 后重新打开 App；清除记住的选择不会改变此参数。";
  }
  if (typeof code === "string" && /^runtime_install_exit_(\d+|signal)$/.test(code)) {
    // Exit 2 from install-local.sh is its "no usable Python 3.11+" gate; the
    // same exit can technically be a usage error, so the wording stays
    // probabilistic and points at the repair action.
    if (code === "runtime_install_exit_2") {
      return "安装程序退出（2）：本机多半缺少可用的 Python 3.11+。安装 Python 后点击「修复当前版本」。";
    }
    return `安装程序退出（${code.slice("runtime_install_exit_".length)}）。请复制诊断信息反馈；修复没有完成。`;
  }
  return Object.hasOwn(errors, code) ? errors[code] : labels[phase] || "";
}
function render(state) {
  if (!state?.phase) return state;
  if (state.phase === "available" && state.details?.channel !== channel.value) state = {phase:"idle"};
  working = updateWorkingPhases.includes(state.phase);
  const controlsDisabled = working || actionInFlight;
  update.disabled = controlsDisabled;
  repair.disabled = controlsDisabled || state.phase === "restart_required" || runtimeExplicit || !bundledRepairAvailable;
  if (state.details?.bundled_repair_available === false) repair.disabled = true;
  forgetSelection.disabled = controlsDisabled || state.phase === "restart_required";
  rollback.disabled = controlsDisabled || state.phase === "restart_required";
  channel.disabled = controlsDisabled || state.phase === "restart_required";
  nextAction = state.phase === "available" ? "apply" : state.phase === "restart_required" ? "restart" : "check";
  update.textContent = nextAction === "apply" ? "更新并准备重启 / Install update" : nextAction === "restart" ? "重启完成更新 / Restart" : "检查更新 / Check for updates";
  updateStatus.textContent = codeText(state.details?.code, state.phase);
  return state;
}
const diagnostics = document.querySelector("#diagnostics");
function safeCode(code) {
  return typeof code === "string" && (Object.hasOwn(errors, code) || /^runtime_install_exit_(\d+|signal)$/.test(code) || ["service_start_failed", "update_failed"].includes(code)) ? code : "unknown";
}
// v2 adds the non-PII environment block surfaced by desktop_update_status.
// Fields the backend has not sent yet stay null so old payloads still render.
function safeEnvironment(result) {
  const environment = result.environment;
  if (!environment || typeof environment !== "object") return null;
  const text = (value) => typeof value === "string" && value ? value : null;
  const flag = (value) => typeof value === "boolean" ? value : null;
  return {
    os_version: text(environment.os_version),
    arch: text(environment.arch),
    runtime_executable_found: flag(environment.runtime_executable_found),
    python3_found: flag(environment.python3_found),
    python3_version: text(environment.python3_version),
  };
}
function renderDiagnostics(result) {
  const failure = result.last_failure ?? result.state;
  const text = JSON.stringify({
    schema_version: "desktop_recovery_diagnostics_v2",
    failure_phase: ["error", "runtime_required", "runtime_pairing_required", "service_error"].includes(failure?.phase) ? failure.phase : null,
    app_version: /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(result.app_version) ? result.app_version : "unknown",
    error_code: safeCode(failure?.details?.code),
    installed_identity_available: typeof failure?.details?.installed_identity_available === "boolean" ? failure.details.installed_identity_available : null,
    revision_matches: typeof failure?.details?.revision_matches === "boolean" ? failure.details.revision_matches : null,
    environment: safeEnvironment(result),
  }, null, 2);
  if (diagnostics.value !== text) diagnostics.value = text;
}
document.querySelector("#copy-diagnostics").onclick = async () => {
  try {
    await navigator.clipboard.writeText(diagnostics.value);
    document.querySelector("#copy-status").textContent = "已复制 / Copied";
  } catch {
    diagnostics.focus(); diagnostics.select();
    document.querySelector("#copy-status").textContent = "请按 ⌘C / Ctrl+C 复制已选中的诊断。";
  }
};
async function run(action) {
  if (working || actionInFlight) return;
  actionInFlight = true;
  statusGeneration++;
  // Match the phase the backend publishes for each action (rollback restores
  // the previous app; restart keeps the required-restart state) instead of
  // previewing a download that is not happening.
  render({phase: action === "check" ? "checking" : action === "repair" || action === "align_runtime" ? "installing_runtime" : action === "forget_runtime_selection" ? "connecting" : action === "rollback" ? "installing_app" : action === "restart" ? "restart_required" : "downloading"});
  try {
    const result = await window.__TAURI__.core.invoke("desktop_update", {action,channel:channel.value});
    statusGeneration++;
    actionInFlight = false;
    return render(result);
  } catch (error) {
    statusGeneration++;
    actionInFlight = false;
    return render({phase:"error", details:{code: safeCode(error)}});
  }
}
update.onclick = () => run(nextAction);
repair.onclick = () => run("repair");
rollback.onclick = () => run("rollback");
forgetSelection.onclick = () => void run("forget_runtime_selection");
// The main status line keeps its loading shape while the supervisor retries.
// A terminal snapshot is decisive. The front-end error projection the page pulls it from
// desktop_update_status itself, so it does not depend on the native eval()
// calls racing this script's definition.
const TERMINAL_PHASES = ["error", "runtime_required"];
// A terminal native result is already decisive; never keep its loading clock
// or progress treatment while waiting for more identical polls.
const ERROR_ESCALATION_ROUNDS = 1;
let terminalRounds = 0;
let escalated = false;
async function refresh() {
  if (!window.__TAURI__) { renderDiagnostics({state:{phase:"error",details:{code:"desktop_status_unavailable"}}}); return; }
  const generation = ++statusGeneration;
  try {
    const result = await window.__TAURI__.core.invoke("desktop_update_status");
    if (generation !== statusGeneration) return;
    if (actionInFlight && result.state?.phase && !updateWorkingPhases.includes(result.state.phase)) return;
    if (!channelInitialized) {
      channel.value = result.state?.details?.channel ?? (result.app_version?.includes("-main.") ? "main" : "stable");
      channelInitialized = true;
    }
    runtimeExplicit = result.runtime_selection?.explicit === true;
    bundledRepairAvailable = result.runtime_selection?.bundled_repair_available !== false;
    rollback.hidden = !result.rollback_available;
    forgetSelection.hidden = result.runtime_selection?.explicit === true || (result.runtime_selection?.remembered !== true && !["runtime_selection_invalid", "runtime_selection_unavailable"].includes(result.state?.details?.code));
    renderDiagnostics(result);
    render(result.state);
    renderStartup(result);
    escalateFromSnapshot(result.state);
  } catch {
    if (generation === statusGeneration && !actionInFlight) {
      renderDiagnostics({state:{phase:"error",details:{code:"desktop_status_unavailable"}}});
    }
  }
}
function escalateFromSnapshot(state) {
  const terminal = TERMINAL_PHASES.includes(state?.phase);
  terminalRounds = terminal ? terminalRounds + 1 : 0;
  if (terminal && terminalRounds >= ERROR_ESCALATION_ROUNDS) escalated = true;
  if (!escalated) return;
  if (terminal) {
    panel.dataset.state = "error";
    panel.setAttribute("aria-busy", "false");
    status.textContent = `${codeText(state?.details?.code, state?.phase)} 详见下方「恢复与更新」面板，可复制诊断信息反馈。`;
    bootDetail.textContent = "启动已停止等待。可在下方恢复，不必反复重开 App。";
    document.querySelector(".recovery").open = true;
  } else {
    escalated = false;
    panel.dataset.state = "loading";
    panel.setAttribute("aria-busy", "true");
    status.textContent = "正在重新连接本地控制面";
  }
}
void refresh();
setInterval(refresh,1000);
