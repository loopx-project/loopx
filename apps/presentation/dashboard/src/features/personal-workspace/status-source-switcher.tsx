import { Copy, Plus, RotateCw, Server, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { StatusSource } from "../../data/status-source-catalog";
import {
  configuredSshTunnelDraft,
  fetchConfiguredSshHosts,
  type ConfiguredSshHost,
} from "../../data/ssh-host-catalog";
import { useWorkspaceI18n } from "./i18n";
import { WorkspaceSelect } from "./workspace-select";

// "degraded": the status source answers but the Chat execution service does
// not, so state stays readable while Agent runs cannot be listed or started.
export type StatusSourceConnectionState = "connected" | "degraded" | "error" | "loading";

export type StatusSourceControl = {
  activeSource: StatusSource;
  connectionState: StatusSourceConnectionState;
  errorMessage?: string | null;
  onAdd: (input: { ensureTunnel?: boolean; hostAlias?: string; label: string; statusUrl: string }) => { error?: string };
  onConfiguredHostsLoaded?: (hostAliases: string[]) => void;
  onRemove: (sourceId: string) => void;
  onSelect: (sourceId: string) => void;
  sources: StatusSource[];
};

export function StatusSourceSwitcher({
  activeSource,
  connectionState,
  errorMessage,
  onAdd,
  onConfiguredHostsLoaded,
  onRemove,
  onSelect,
  sources,
}: StatusSourceControl) {
  const { t } = useWorkspaceI18n();
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addMode, setAddMode] = useState<"configured" | "manual">("configured");
  const [configuredHosts, setConfiguredHosts] = useState<ConfiguredSshHost[]>([]);
  const [configuredHostsError, setConfiguredHostsError] = useState<string | null>(null);
  const [configuredHostsLoading, setConfiguredHostsLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [hostAlias, setHostAlias] = useState("");
  const [label, setLabel] = useState("");
  const [localPort, setLocalPort] = useState("8876");
  const [statusUrl, setStatusUrl] = useState("");
  const quickAddPrefix = "configured:";
  const sourceOptions = [
    ...sources.map((source) => ({ label: source.label, value: source.id })),
    ...configuredHosts
      .filter((host) => !sources.some((source) => source.label === host.alias))
      .map((host) => ({
        group: t("source.configuredGroup", { count: configuredHosts.length }),
        label: host.alias,
        value: `${quickAddPrefix}${host.alias}`,
      })),
  ];
  const configuredDraft = useMemo(() => {
    if (!configuredHosts.some((host) => host.alias === hostAlias)) {
      return { error: t("source.selectHost") } as const;
    }
    return configuredSshTunnelDraft(hostAlias, localPort);
  }, [configuredHosts, hostAlias, localPort, t]);

  useEffect(() => {
    void loadConfiguredHosts();
  }, []);

  async function loadConfiguredHosts() {
    setConfiguredHostsLoading(true);
    setConfiguredHostsError(null);
    try {
      const catalog = await fetchConfiguredSshHosts();
      setConfiguredHosts(catalog.hosts);
      onConfiguredHostsLoaded?.(catalog.hosts.map((host) => host.alias));
      setHostAlias((current) => current || catalog.hosts[0]?.alias || "");
      if (!catalog.hosts.length) setConfiguredHostsError(t("source.hostEmpty"));
    } catch (caught) {
      setConfiguredHostsError(caught instanceof Error ? caught.message : t("source.hostLoadError"));
    } finally {
      setConfiguredHostsLoading(false);
    }
  }

  function openForm() {
    setAdding(true);
    setAddMode("configured");
    setError(null);
    void loadConfiguredHosts();
  }

  function closeForm() {
    setAdding(false);
    setAddMode("configured");
    setConfiguredHostsError(null);
    setCopied(false);
    setHostAlias("");
    setError(null);
    setLabel("");
    setLocalPort("8876");
    setStatusUrl("");
  }

  function submitManual() {
    const result = onAdd({ label, statusUrl });
    if (result.error) {
      setError(result.error);
      return;
    }
    closeForm();
  }

  function submitConfigured() {
    if ("error" in configuredDraft) {
      setError(configuredDraft.error ?? t("source.invalid"));
      return;
    }
    const result = onAdd({ ensureTunnel: true, hostAlias: configuredDraft.hostAlias, label: configuredDraft.label, statusUrl: configuredDraft.statusUrl });
    if (result.error) {
      setError(result.error);
      return;
    }
    closeForm();
  }

  function quickAddConfiguredHost(alias: string) {
    const usedPorts = new Set<string>();
    for (const source of sources) {
      try {
        usedPorts.add(new URL(source.statusUrl).port);
      } catch {
        // ignore malformed persisted sources
      }
    }
    let freePort = "8877";
    for (let port = 8877; port < 8877 + 200; port += 1) {
      if (!usedPorts.has(String(port))) {
        freePort = String(port);
        break;
      }
    }
    const draft = configuredSshTunnelDraft(alias, freePort);
    if ("error" in draft) {
      setError(draft.error ?? t("source.invalid"));
      return;
    }
    const result = onAdd({ ensureTunnel: true, hostAlias: draft.hostAlias, label: draft.label, statusUrl: draft.statusUrl });
    if (result.error) setError(result.error);
    else setError(null);
    setLocalPort(freePort);
  }

  async function copyTunnelCommand() {
    if ("error" in configuredDraft) {
      setError(configuredDraft.error ?? t("source.invalid"));
      return;
    }
    try {
      await navigator.clipboard.writeText(configuredDraft.command);
      setCopied(true);
      setError(null);
    } catch {
      setError(t("source.copyError"));
    }
  }

  return (
    <section aria-label={t("source.controlPlane")} className="personal-status-source">
      <header>
        <span>Control plane</span>
        <button aria-label={t("source.addSsh")} onClick={openForm} title={t("source.add")} type="button"><Plus size={14} /></button>
      </header>
      <WorkspaceSelect
        ariaLabel={t("source.select")}
        className="personal-status-source-select"
        icon={<Server size={15} />}
        onChange={(value) => {
          if (value.startsWith(quickAddPrefix)) {
            quickAddConfiguredHost(value.slice(quickAddPrefix.length));
            return;
          }
          onSelect(value);
        }}
        options={sourceOptions}
        value={activeSource.id}
      />
      <div className="personal-status-source-meta">
        <span className={`is-${connectionState}`}><i />{connectionState === "loading" ? t("source.connecting") : connectionState === "error" ? t("source.notAvailable") : connectionState === "degraded" ? t("source.executionUnavailable") : t("source.connected")}</span>
        <small>{activeSource.readOnly ? t("source.readOnly") : t("source.localInteractive")}</small>
        {activeSource.kind === "ssh_tunnel" ? (
          <button aria-label={t("source.remove", { source: activeSource.label })} onClick={() => onRemove(activeSource.id)} title={t("source.removeCurrent")} type="button"><Trash2 size={12} /></button>
        ) : null}
      </div>
      {errorMessage ? <p className="personal-status-source-error" role="alert">{errorMessage}</p> : null}
      {adding ? (
        <div className="personal-status-source-form">
          <header><strong>{t("source.addSsh")}</strong><button aria-label={t("source.closeForm")} onClick={closeForm} type="button"><X size={13} /></button></header>
          <div aria-label={t("source.addMethod")} className="personal-status-source-modes" role="tablist">
            <button aria-selected={addMode === "configured"} onClick={() => { setAddMode("configured"); setError(null); }} role="tab" type="button">{t("source.configured")}</button>
            <button aria-selected={addMode === "manual"} onClick={() => { setAddMode("manual"); setError(null); }} role="tab" type="button">{t("source.manual")}</button>
          </div>
          {addMode === "configured" ? (
            <>
              <label>
                <span>{t("source.configuredCount", { count: configuredHosts.length })}</span>
                <span className="personal-status-source-field-row">
                  <input aria-label={t("source.host")} disabled={configuredHostsLoading || !configuredHosts.length} list="loopx-configured-ssh-hosts" onChange={(event) => { setHostAlias(event.target.value); setCopied(false); }} placeholder={configuredHostsLoading ? t("source.loadingHosts") : t("source.hostPlaceholder")} value={hostAlias} />
                  <datalist id="loopx-configured-ssh-hosts">{configuredHosts.map((host) => <option key={host.alias} value={host.alias} />)}</datalist>
                  <button aria-label={t("source.refreshHosts")} disabled={configuredHostsLoading} onClick={() => void loadConfiguredHosts()} title={t("source.refreshHosts")} type="button"><RotateCw size={13} /></button>
                </span>
              </label>
              <label><span>{t("source.localPort")}</span><input aria-label={t("source.localPort")} inputMode="numeric" onChange={(event) => { setLocalPort(event.target.value); setCopied(false); }} value={localPort} /></label>
              <div className="personal-status-source-command">
                <code>{"error" in configuredDraft ? t("source.tunnelCommandPending") : configuredDraft.command}</code>
                <button aria-label={t("source.copyCommand")} disabled={"error" in configuredDraft} onClick={() => void copyTunnelCommand()} type="button"><Copy size={12} />{copied ? t("source.copied") : t("source.copy")}</button>
              </div>
              {configuredHostsError ? <p className="is-error">{configuredHostsError}</p> : null}
              <p>{t("source.description")}</p>
              <button className="personal-status-source-add" disabled={"error" in configuredDraft} onClick={submitConfigured} type="button">{t("source.addConfigured")}</button>
            </>
          ) : (
            <>
              <label><span>{t("source.name")}</span><input autoFocus maxLength={48} onChange={(event) => setLabel(event.target.value)} placeholder={t("source.namePlaceholder")} value={label} /></label>
              <label><span>{t("source.statusUrl")}</span><input onChange={(event) => setStatusUrl(event.target.value)} placeholder="http://127.0.0.1:8876/status.json" value={statusUrl} /></label>
              <p><code>ssh -N -L 8876:127.0.0.1:8766 &lt;host&gt;</code></p>
              <p>{t("source.manualDescription")}</p>
              <button className="personal-status-source-add" onClick={submitManual} type="button">{t("source.addConfigured")}</button>
            </>
          )}
          {error ? <p className="is-error" role="alert">{error}</p> : null}
        </div>
      ) : null}
    </section>
  );
}
