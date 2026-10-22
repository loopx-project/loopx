import { useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  KeyRound,
  Plug,
  RefreshCw,
  ServerCog,
} from "lucide-react";

import { fetchChatCapabilities } from "../../data/chat";
import {
  deriveDshInteractiveReadiness,
  deriveDshManagedReadiness,
  type DshManagedAdapter,
} from "./dsh-host-readiness";
import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceGoal } from "./personal-workspace-model";
import "./dsh-host-readiness.css";

const NATIVE_PLUGIN_GUIDE =
  "https://github.com/loopx-project/loopx/blob/main/docs/integrations/deepseek-harness-control-plane-adapter.md";
const MANAGED_TURN_GUIDE =
  "https://github.com/loopx-project/loopx/blob/main/docs/integrations/deepseek-harness-connector.md";

type LoadState =
  | {status: "loading"}
  | {status: "loaded"; adapters: DshManagedAdapter[]}
  | {status: "error"};

export function DshHostReadiness({
  goals,
  onOpenProviderSettings,
}: {
  goals: WorkspaceGoal[];
  onOpenProviderSettings?: () => void;
}) {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [readback, setReadback] = useState<LoadState>({status: "loading"});
  const [rechecking, setRechecking] = useState(false);

  async function recheck() {
    if (rechecking) return;
    setRechecking(true);
    try {
      const capabilities = await fetchChatCapabilities();
      setReadback({
        status: "loaded",
        adapters: (capabilities.adapters ?? []).map((adapter) => ({
          agent_id: adapter.agent_id,
          available: adapter.available,
          unavailable_reason: adapter.unavailable_reason,
          execution_profile: adapter.execution_profile,
          execution_profile_details: adapter.execution_profile_details,
          unavailable_remediation: adapter.unavailable_remediation,
        })),
      });
    } catch {
      setReadback({status: "error"});
    } finally {
      setRechecking(false);
    }
  }

  useEffect(() => {
    void recheck();
    // The initial read belongs to mounting this settings section. A later
    // explicit recheck reads a fresh capability projection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const managed = readback.status === "loaded"
    ? deriveDshManagedReadiness(readback.adapters)
    : null;
  const interactive = deriveDshInteractiveReadiness(goals);

  const managedCopy = (() => {
    if (readback.status === "loading") {
      return {tone: "pending", title: zh ? "正在检查" : "Checking", detail: ""};
    }
    if (readback.status === "error") {
      return {
        tone: "warning",
        title: zh ? "无法读取状态" : "Readback unavailable",
        detail: zh ? "检查本机服务后重试。" : "Check the local service and retry.",
      };
    }
    if (!managed) return {tone: "pending", title: "", detail: ""};
    switch (managed.kind) {
      case "ready":
        return {
          tone: "ready",
          title: zh ? "已就绪" : "Ready",
          detail: zh ? "LoopX 可在此机启动后台自动化。" : "LoopX can launch background automation on this machine.",
        };
      case "needs_runtime":
        return {
          tone: "warning",
          title: zh ? "缺少 DSH runtime" : "DSH runtime missing",
          detail: zh
            ? "LoopX 当前解释器无法导入 DSH。安装或切换 runtime 后重新检查。"
            : "The current LoopX interpreter cannot import DSH. Install or switch the runtime, then recheck.",
        };
      case "needs_credential":
        return {
          tone: "warning",
          title: zh ? "缺少模型凭据" : "Model credential missing",
          detail: zh
            ? "后台 Turn 使用本机 operator 凭据；密钥只写入，不会在页面回显。"
            : "Background Turns use the machine operator credential. The key is write-only and never rendered here.",
        };
      case "needs_configuration":
        return {
          tone: "warning",
          title: zh ? "执行配置需要修正" : "Execution profile needs correction",
          detail: managed.repair.kind === "environment_variable"
            ? (zh
              ? `后台 Turn 的 ${managed.repair.variable} 未通过检查。请在启动 LoopX 服务的环境中修正或清除该变量，然后重启并重新检查；下方管家设置只影响会话默认值。`
              : `The background Turn rejected ${managed.repair.variable}. Correct or clear it in the LoopX service environment, then restart and recheck. Steward settings only change session defaults.`)
            : (zh
              ? `后台 Turn 的 ${managed.repair.setting} 配置来自服务启动参数。请在启动服务的位置修正后重启并重新检查；管家设置不能修改它。`
              : `The background Turn's ${managed.repair.setting} comes from service startup configuration. Correct it where the service starts, then restart and recheck; Steward settings cannot change it.`),
        };
      case "not_observed":
        return {
          tone: "neutral",
          title: zh ? "未观察到 managed DSH" : "Managed DSH not observed",
          detail: zh ? "当前服务没有返回 DSH endpoint 行。" : "This service did not return a DSH endpoint row.",
        };
      default:
        return {
          tone: "warning",
          title: zh ? "不可用" : "Unavailable",
          detail: zh ? "服务未提供可识别的修复原因。" : "The service did not provide a recognizable repair reason.",
        };
    }
  })();

  return (
    <section
      aria-label={zh ? "DeepSeek Harness 两种运行方式" : "DeepSeek Harness operating modes"}
      aria-live="polite"
      className="personal-dsh-host-readiness"
      data-testid="dsh-host-readiness"
    >
      <header>
        <div>
          <strong>DeepSeek Harness</strong>
          <p>{zh ? "后台自动化 Turn 与 DSH 内交互会话是两条独立路径。" : "Background Turns and interactive DSH Sessions are separate paths."}</p>
        </div>
        <button
          aria-label={zh ? "重新检查 DSH 状态" : "Recheck DSH readiness"}
          disabled={rechecking}
          onClick={() => void recheck()}
          type="button"
        >
          <RefreshCw aria-hidden className={rechecking ? "personal-spin" : undefined} size={15} />
          {zh ? "重新检查" : "Recheck"}
        </button>
      </header>

      <div className="personal-dsh-host-readiness-row" data-testid="dsh-managed-readiness">
        <span className={`personal-dsh-host-icon is-${managedCopy.tone}`}>
          {managedCopy.tone === "ready" ? <CheckCircle2 aria-hidden size={17} />
            : managedCopy.tone === "warning" ? <AlertTriangle aria-hidden size={17} />
            : <ServerCog aria-hidden size={17} />}
        </span>
        <div>
          <small>{zh ? "后台 Turn" : "Background Turns"}</small>
          <strong>{managedCopy.title}</strong>
          {managedCopy.detail ? <p>{managedCopy.detail}</p> : null}
          {managed?.profile ? <code>{zh ? `执行配置 ${managed.profile}` : `Profile ${managed.profile}`}</code> : null}
        </div>
        <div className="personal-dsh-host-actions">
          {managed?.kind === "needs_credential" && onOpenProviderSettings ? (
            <button onClick={onOpenProviderSettings} type="button">
              <KeyRound aria-hidden size={14} />{zh ? "配置模型凭据" : "Configure credential"}
            </button>
          ) : null}
          {managed?.kind === "needs_runtime" ? (
            <a href={MANAGED_TURN_GUIDE} rel="noreferrer" target="_blank">
              {zh ? "检查安装" : "Setup guide"}<ExternalLink aria-hidden size={13} />
            </a>
          ) : null}
        </div>
      </div>

      <div className="personal-dsh-host-readiness-row" data-testid="dsh-native-binding">
        <span className={`personal-dsh-host-icon ${interactive.kind === "bound" ? "is-ready" : "is-neutral"}`}>
          {interactive.kind === "bound" ? <CheckCircle2 aria-hidden size={17} /> : <Plug aria-hidden size={17} />}
        </span>
        <div>
          <small>{zh ? "DSH 内交互" : "Interactive in DSH"}</small>
          <strong>{interactive.kind === "bound"
            ? (zh ? `已绑定 ${interactive.boundGoalCount} 个 Goal` : `Bound to ${interactive.boundGoalCount} Goal${interactive.boundGoalCount === 1 ? "" : "s"}`)
            : (zh ? "未观察到 native binding" : "No native binding observed")}</strong>
          <p>{interactive.kind === "bound"
            ? (zh ? "这表示已有 Goal 使用 DSH Session；绑定本身不代表 DSH 进程当前正在运行。"
              : "An existing Goal uses a DSH Session. Binding alone does not prove a DSH process is running now.")
            : (zh ? "安装 LoopX plugin 后，在 DSH 中使用 loopx skill 启动并绑定会话。"
              : "Install the LoopX plugin, then start and bind a Session from the loopx skill in DSH.")}</p>
        </div>
        <div className="personal-dsh-host-actions">
          <a href={NATIVE_PLUGIN_GUIDE} rel="noreferrer" target="_blank">
            {interactive.kind === "bound"
              ? (zh ? "查看绑定说明" : "Binding guide")
              : (zh ? "安装与绑定" : "Install and bind")}
            <ExternalLink aria-hidden size={13} />
          </a>
        </div>
      </div>
    </section>
  );
}
