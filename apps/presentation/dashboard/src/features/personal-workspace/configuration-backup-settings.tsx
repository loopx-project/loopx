import {useEffect, useState} from "react";
import {exportConfigurationBackup, restoreConfigurationCheckpoint, type ConfigurationBackupResult} from "../../data/chat";
import {useWorkspaceI18n} from "./i18n";

export function ConfigurationBackupSettings({goalId}: {goalId: string | null}) {
  const {locale} = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [backup, setBackup] = useState<Record<string, unknown> | null>(null);
  const [result, setResult] = useState<ConfigurationBackupResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {setBackup(null); setResult(null); setError("");}, [goalId]);
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError("");
    try {await action();} catch (failure) {setError(String(failure));}
    finally {setBusy(false);}
  }
  return <details className="personal-detail-card">
    <summary>{zh ? "配置备份与恢复" : "Configuration backup and recovery"}</summary>
    <p>{zh ? "备份包含私有路径、Goal 设置及设备默认值，需自行保管和审查。凭据文件不包含在此组件中。恢复只建立隔离检查点；启用仍在原配置页面办理。" : "Keep and review this private backup: it includes Goal settings, paths and device defaults. Credential files are outside this component. Recovery creates an isolated checkpoint; activate settings through their existing editors."}</p>
    <button className="personal-secondary-action" disabled={busy} type="button" onClick={() => void perform(async () => {
      const exported = await exportConfigurationBackup(goalId ? [goalId] : undefined);
      const url = URL.createObjectURL(new Blob([JSON.stringify(exported.backup, null, 2) + "\n"], {type: "application/json"}));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = "loopx-configuration-backup.json";
      anchor.click(); URL.revokeObjectURL(url);
      setResult(exported);
    })}>{zh ? "下载配置备份" : "Download configuration backup"}</button>
    <label>{zh ? "恢复备份文件" : "Recover backup file"}<input aria-label={zh ? "恢复备份文件" : "Recover backup file"} disabled={busy} type="file" accept=".json,application/json" onChange={(event) => {
      const file = event.currentTarget.files?.[0]; event.currentTarget.value = "";
      setBackup(null); setResult(null);
      if (file) void perform(async () => {
        const candidate = JSON.parse(await file.text()) as Record<string, unknown>;
        const preview = await restoreConfigurationCheckpoint(candidate, false);
        setBackup(candidate); setResult(preview);
      });
    }}/></label>
    {backup && result?.status === "preview" ? <button className="personal-primary-action" disabled={busy} type="button" onClick={() => void perform(async () => {
      setResult(await restoreConfigurationCheckpoint(backup, true));
    })}>{zh ? "恢复为隔离检查点" : "Restore isolated checkpoint"}</button> : null}
    {result ? <p role="status">{result.status === "restored"
      ? (zh ? "检查点已恢复并核对；当前设置及存储 provider 未改变。" : "Checkpoint restored and verified. Live settings and storage provider are unchanged.")
      : (zh ? "配置备份已核对" : "Configuration backup verified")}: {result.goal_count} Goal · {result.machine_configuration_present ? (zh ? "包含设备配置" : "includes device configuration") : (zh ? "设备配置缺省" : "device configuration absent")}
      {result.checkpoint_ref ? <code>{result.checkpoint_ref}</code> : null}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
  </details>;
}
