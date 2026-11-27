import {useEffect, useRef, useState} from "react";
import {compareConfigurationSnapshots, type ConfigurationBackupComparison} from "../../data/chat";
import {useWorkspaceI18n} from "./i18n";
import "./configuration-backup-comparison.css";

type Snapshot = {name: string; data: Record<string, unknown>};
export function ConfigurationBackupComparisonSettings({disabled, onChange, onReview, onBusyChange}: {
  disabled: boolean; onBusyChange: (busy: boolean) => void; onChange: () => void; onReview: (backup: Record<string, unknown>) => void;
}) {
  const zh = useWorkspaceI18n().locale === "zh-CN";
  const [snapshots, setSnapshots] = useState<[Snapshot | null, Snapshot | null]>([null, null]);
  const [comparison, setComparison] = useState<ConfigurationBackupComparison | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const labels = zh ? ["比较前文件", "比较后文件"] : ["Before snapshot", "After snapshot"];
  async function select(file: File | undefined, index: number) {
    setComparison(null); setError(""); onChange();
    setSnapshots(previous => index === 0 ? [null, previous[1]] : [previous[0], null]);
    if (!file) return;
    setBusy(true); onBusyChange(true);
    try {
      if (file.size > 64 * 1024 * 1024) throw new Error(zh ? "文件超过 64 MiB 比较限制。" : "File exceeds the 64 MiB comparison limit.");
      const data: unknown = JSON.parse(await file.text());
      if (!data || Array.isArray(data) || typeof data !== "object") throw new Error(zh ? "请选择配置快照 JSON 文件。" : "Select a configuration snapshot JSON object.");
      if (!alive.current) return;
      const selected = {name: file.name, data: data as Record<string, unknown>};
      setSnapshots(previous => index === 0 ? [selected, previous[1]] : [previous[0], selected]);
    } catch (failure) {if (alive.current) setError(String(failure));} finally {if (alive.current) {setBusy(false); onBusyChange(false);}}
  }
  async function compare() {
    if (!snapshots[0] || !snapshots[1]) return;
    setBusy(true); onBusyChange(true); setComparison(null); setError(""); onChange();
    try {const compared = await compareConfigurationSnapshots(snapshots[0].data, snapshots[1].data); if (alive.current) setComparison(compared);}
    catch (failure) {if (alive.current) setError(String(failure));} finally {if (alive.current) {setBusy(false); onBusyChange(false);}}
  }
  return <section className="personal-backup-comparison" aria-label={zh ? "比较配置快照" : "Compare configuration snapshots"}>
    <h3>{zh ? "比较配置快照" : "Compare configuration snapshots"}</h3>
    <p>{zh ? "先比较两个保存的文件，再选择要核对恢复的快照。比较不会修改当前设置；Goal 仅在某一文件出现可能是备份范围不同，不代表已新建或删除。" : "Compare two saved files before choosing a snapshot to review for recovery. Comparison changes no live settings. Goals present in only one file may reflect different backup cohorts, not creation or deletion."}</p>
    <p>{zh ? "两文件请求合计最多 64 MiB；完整差异最多 56 MiB，超限时不返回部分结果。" : "Combined request limit: 64 MiB. Complete comparison limit: 56 MiB; oversized comparisons return no partial result."}</p>
    <div className="personal-backup-files">{labels.map((label, index) => <label key={index}>{label}
      <input type="file" accept=".json,application/json" aria-label={label} disabled={busy || disabled} onChange={event => {
        void select(event.currentTarget.files?.[0], index);
      }}/><span>{snapshots[index]?.name ?? (zh ? "尚未选择" : "No file selected")}</span>
    </label>)}</div>
    <button type="button" className="personal-secondary-action" disabled={busy || disabled || !snapshots[0] || !snapshots[1]} onClick={() => void compare()}>{busy ? (zh ? "正在读取…" : "Reading…") : (zh ? "比较两个文件" : "Compare files")}</button>
    {error ? <p role="alert">{error}</p> : null}
    {comparison ? <>
      <p role="status">{zh ? `已比较：${comparison.change_count} 项差异。当前设置未改变。` : `Compared: ${comparison.change_count} differences. Live settings unchanged.`}</p>
      <div className="personal-backup-files">{snapshots.map((snapshot, index) => {
        const before = index === 0;
        const ids = before ? comparison.before_goal_ids : comparison.after_goal_ids;
        const only = before ? comparison.only_in_before : comparison.only_in_after;
        return <div key={index}>
          <h4>{labels[index]}: {snapshot?.name}</h4>
          <p>{ids.length} Goal · {(before ? comparison.before_machine_configuration_present : comparison.after_machine_configuration_present) ? (zh ? "包含设备配置" : "Includes device configuration") : (zh ? "设备配置缺省" : "Device configuration absent")}</p>
          <details><summary>{zh ? "散列与备份范围" : "Hash and cohort"}</summary>
            <p>SHA-256: <code>{before ? comparison.before_sha256 : comparison.after_sha256}</code></p>
            <p>Goal: {ids.join(", ") || (zh ? "无" : "None")}</p>
            <p>{zh ? "仅在此文件: " : "Only in this file: "}{only.join(", ") || (zh ? "无" : "None")}</p>
          </details>
          <button type="button" className="personal-secondary-action" disabled={busy || disabled} onClick={() => {if (snapshot) onReview(snapshot.data);}}>{before ? (zh ? "核对比较前快照以恢复" : "Review before snapshot for recovery") : (zh ? "核对比较后快照以恢复" : "Review after snapshot for recovery")}</button>
        </div>;
      })}</div>
      {comparison.changes.length === 0 ? <p>{zh ? "保存的配置内容一致。" : "Stored configuration contents are identical."}</p> : <div aria-label={zh ? "配置差异" : "Configuration differences"}>{comparison.changes.map(change => <details key={change.path}>
        <summary><code>{change.path}</code></summary>
        <div className="personal-backup-files">{[true, false].map(before => <div key={String(before)}><h4>{labels[before ? 0 : 1]}</h4>
          <pre>{(before ? change.before_present : change.after_present) ? JSON.stringify(before ? change.before : change.after, null, 2) : (zh ? "不存在" : "Absent")}</pre>
        </div>)}</div>
      </details>)}</div>}
    </> : null}
  </section>;
}
