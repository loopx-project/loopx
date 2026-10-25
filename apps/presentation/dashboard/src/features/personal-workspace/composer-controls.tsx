import { Bot, Plus } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import type { ChatSessionSummary } from "../../data/chat";
import type { WorkspaceAgentOption } from "./personal-workspace-model";
import { ChatModelMenu } from "./chat-model-menu";
import { useWorkspaceI18n } from "./i18n";
import { WorkspaceSelect } from "./workspace-select";

/** Existing send controls keep their Session/configuration owners. */
export function ComposerRuntimeControls({ agents, agentId, goalId, original, model, effort, readback, onAgent, onModel, onEffort, onDefaults }: {
  agents: WorkspaceAgentOption[]; agentId: string; goalId?: string; original: boolean;
  model?: string; effort?: string; readback?: ChatSessionSummary;
  onAgent: (id: string) => void; onModel?: (id: string) => void;
  onEffort?: (effort: string) => void; onDefaults?: () => void;
}) {
  const { locale, t } = useWorkspaceI18n();
  const statusOnly = agentId === "status-only";
  return <div className="personal-composer-runtime">
    {original ? <span className="personal-composer-executor"><Bot size={15} aria-hidden />{agentId}</span>
      : <WorkspaceSelect ariaLabel={t("header.selectChatRuntime")} className="personal-composer-executor"
        icon={<Bot size={15} />} onChange={onAgent} value={statusOnly ? "" : agentId}
        options={[
          ...(statusOnly ? [{value: "", label: locale === "zh-CN" ? "选择执行器" : "Choose executor", disabled: true}] : []),
          ...agents.filter(agent => agent.agentId !== "status-only").map(agent => ({value: agent.agentId,
            label: `${agent.label}${agent.available ? "" : ` · ${t("header.agentUnavailable")}`}`, disabled: !agent.available})),
        ]} />}
    {!statusOnly ? <ChatModelMenu agentId={agentId} goalId={goalId} ordinary={!original}
      model={model} effort={effort} readback={readback} onModel={onModel} onEffort={onEffort}
      onOpenDefaults={onDefaults} /> : null}
  </div>;
}

/** One optional action entry; opening and dismissing it never sends a message. */
export function ComposerActions({ children }: { children: ReactNode }) {
  const { locale } = useWorkspaceI18n();
  const root = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const close = (event: PointerEvent | KeyboardEvent) => {
      if (!root.current?.open) return;
      if (event instanceof KeyboardEvent) {
        if (event.key !== "Escape" || event.defaultPrevented) return;
        event.preventDefault();
        root.current?.querySelector("summary")?.focus();
      } else if (root.current?.contains(event.target as Node)) return;
      if (root.current) root.current.open = false;
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", close); };
  }, []);
  return <details className="personal-composer-tools personal-composer-actions" ref={root}>
    <summary aria-label={locale === "zh-CN" ? "添加附件与快捷操作" : "Attachments and actions"}><Plus size={18} aria-hidden /></summary>
    <div className="personal-composer-actions-panel" onClick={event => {
      if ((event.target as Element).closest("button:not(:disabled)") && root.current) root.current.open = false;
    }}>{children}</div>
  </details>;
}
