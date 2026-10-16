import {useEffect, useRef, useState} from "react";
import {inspectStateBackup, listStateRecoveries, readStateRecovery, stateRecoveryProfile, uploadRecoveryInput,
  type RecoveryHistoryItem, type StateRecoveryResult} from "../../data/state-recovery";
import {useWorkspaceI18n} from "./i18n";
import {ChatApiError} from "../../data/chat";
import "./state-recovery-settings.css";

const ownerNames: Record<string, [string, string]> = {
  configuration: ["配置", "Configuration"], coordination_authority: ["Goal 工作与历史", "Goal work and history"],
  sqlite_storage: ["数据库", "Database"], recovery: ["其他保存的状态", "Other saved state"],
};
const nextActions: Record<string, [string, string]> = {
  retain_isolated_configuration: ["保留隔离配置，通过原设置页另行启用。", "Keep the isolated configuration; use its existing editor for any later activation."],
  review_goal_identity_before_adoption: ["继续执行前，先由 Goal owner 核对身份与未完成工作。", "Before resuming, have the Goal owner review identity and unfinished work."],
  inspect_captured_authority_with_owner: ["请存储 owner 检查保存的工作记录。", "Have the storage owner inspect the saved work records."],
  use_provider_recovery_owner: ["通过该存储服务的恢复流程检查。", "Use this storage service's recovery procedure."],
  retain_historical_database: ["保留数据库副本，暂不用于执行。", "Keep the database copy as historical data."],
  inspect_database_with_owner: ["请数据库 owner 检查完整性和格式。", "Have the database owner check integrity and format."],
  inspect_configuration_with_owner: ["请配置 owner 检查备份版本和内容。", "Have the configuration owner check the saved format and contents."],
  classify_remaining_owner_state: ["启用前，需分别确认其他状态的归属、会话、租约和未决操作。", "Identify the remaining state owners, sessions, leases and pending operations before any activation."],
};
const errors: Record<string, [string, string]> = {
  archive_digest_mismatch: ["归档与清单不匹配，请选择同一次备份的两个文件。", "The archive and manifest do not match. Choose the pair from the same backup."],
  manifest_disagreement: ["内外清单不一致，请重新选择原始备份。", "The embedded and external manifests disagree. Choose the original backup pair."],
  recovery_destination_exists: ["这份检查已有隔离副本，请读取历史结果。", "An isolated copy already exists for this inspection. Read its saved result."],
  recovery_capacity_exceeded: ["备份超过当前检查容量，请使用较小的备份；未建立新副本。", "This backup exceeds the inspection limits. Use a smaller backup; no new copy was published."],
  recovery_input_unavailable: ["上传文件已不可用，请重新选择归档与清单。", "The uploaded files are unavailable. Select the archive and manifest again."],
  recovery_owner_timeout: ["离线检查超时，未发布副本；可使用较小的备份重试。", "Offline verification timed out without publishing a copy. Retry with a smaller backup."],
  authority_history_capacity: ["单个 Goal 的历史超过 20,000 次提交，未发布副本。请保留完整备份，通过存储 owner 的恢复流程检查。", "One Goal exceeds the 20,000-commit inspection limit; no copy was published. Keep the complete backup and use the storage owner's recovery procedure."],
  member_witness_mismatch: ["保存的文件与清单记录不符，已停止检查。", "Saved files differ from their manifest witnesses. Inspection stopped."],
};

export function StateRecoverySettings() {
  const {locale} = useWorkspaceI18n(), zh = locale === "zh-CN", index = zh ? 0 : 1;
  const [archive, setArchive] = useState<File | null>(null);
  const [manifest, setManifest] = useState<File | null>(null);
  const [audit, setAudit] = useState<StateRecoveryResult | null>(null);
  const [history, setHistory] = useState<RecoveryHistoryItem[] | null>(null);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [supported, setSupported] = useState<boolean | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    let active = true;
    void stateRecoveryProfile().then(profile => {if (active) setSupported(profile.supported);})
      .catch(() => {if (active) setSupported(false);});
    return () => {active = false;};
  }, []);
  async function perform(operation: () => Promise<void>) {
    setBusy(true); setError("");
    try { await operation(); } catch (failure) {
      const code = failure instanceof ChatApiError ? String(failure.payload.error_code ?? "") : "";
      if (code === "authority_history_capacity") setPending(null);
      setError(errors[code]?.[index] ?? (zh ? "检查未完成，请核对文件或稍后重试。" : "Inspection did not finish. Check the files or retry later."));
    }
    finally { setBusy(false); }
  }
  function choose(file: File | null, kind: "archive" | "manifest") {
    generation.current++; setAudit(null); setError(""); setPending(null);
    if (kind === "archive") setArchive(file); else setManifest(file);
  }
  return <section className="personal-recovery" aria-label={zh ? "完整备份检查" : "Full backup inspection"}>
    {supported === false ? <p role="alert">{zh ? "当前服务或平台不支持完整归档检查。配置快照仍可使用。" : "This service or platform does not support full archive inspection. Configuration snapshots remain available."}</p> : null}
    <p>{zh ? "选择完整归档和配套清单，建立私有隔离副本并查看可读内容。此处不会恢复 Goal 运行；旧备份缺少一致性信息时会明确保留未知。"
      : "Choose an archive and its matching manifest to inspect a private, isolated copy. This does not resume a Goal; missing consistency information stays unknown."}</p>
    <div className="personal-recovery-files"><label>{zh ? "完整归档（tar.gz）" : "Full archive (tar.gz)"}<input type="file" accept=".gz,.tgz" disabled={busy || supported !== true}
      aria-label={zh ? "完整归档" : "Full archive"} onChange={event => choose(event.target.files?.[0] ?? null, "archive")} /></label>
    <label>{zh ? "配套清单（manifest.json）" : "Matching manifest (manifest.json)"}<input type="file" accept=".json" disabled={busy || supported !== true}
      aria-label={zh ? "配套清单" : "Matching manifest"} onChange={event => choose(event.target.files?.[0] ?? null, "manifest")} /></label></div>
    <div className="personal-recovery-actions">
      <button className="personal-primary-action" type="button" disabled={busy || supported !== true || !archive || !manifest} onClick={() => void perform(async () => {
        if (!archive || !manifest) return;
        const current = generation.current;
        setAudit(null); setPending(null);
        const savedArchive = await uploadRecoveryInput(archive, "archive");
        const savedManifest = await uploadRecoveryInput(manifest, "manifest");
        if (current !== generation.current) return;
        setPending(savedArchive.checkpoint_ref ?? null);
        const result = await inspectStateBackup(savedArchive.input_ref, savedManifest.input_ref);
        if (current === generation.current) { setAudit(result); setPending(null); }
      })}>{busy ? (zh ? "处理中…" : "Working…") : (zh ? "验证并建立隔离副本" : "Verify and create isolated copy")}</button>
      <button className="personal-secondary-action" type="button" disabled={busy || supported !== true} onClick={() => void perform(async () => {
        const result = await listStateRecoveries(); setHistory(result.items); setMore(result.has_more);
      })}>{zh ? "历史检查结果" : "Previous inspections"}</button>
    </div>
    {error ? <p role="alert">{error}</p> : null}
    {error && pending ? <button className="personal-secondary-action" type="button" disabled={busy} onClick={() => void perform(async () => {
      setAudit(await readStateRecovery(pending)); setPending(null);
    })}>{zh ? "检查是否已有结果" : "Check for an existing result"}</button> : null}
    {audit ? <div className="personal-recovery-result" aria-label={zh ? "备份检查结果" : "Backup inspection result"}>
      <strong role="status">{audit.historical_readback
        ? (zh ? "已读取历史检查记录；尚未重新验证副本。" : "Historical inspection loaded; the copy has not been reverified.")
        : (zh ? "隔离副本已核对；Goal 尚未启用。" : "Isolated copy inspected; Goal execution remains inactive.")}</strong>
      <p>{zh ? "检查时刻" : "Observed at"}: <time dateTime={audit.observed_at}>{new Date(audit.observed_at).toLocaleString(locale)}</time></p>
      <dl className="personal-recovery-counts">
        <div><dt>{zh ? "可读组件" : "Readable components"}</dt><dd>{audit.summary.readable}</dd></div>
        <div><dt>{zh ? "仅历史组件" : "Historical components"}</dt><dd>{audit.summary.historical_only}</dd></div>
        <div><dt>{zh ? "未知组件" : "Unknown components"}</dt><dd>{audit.summary.unknown}</dd></div>
      </dl>
      <p>{zh ? "旧格式没有完整的捕获版本与一致性声明，以上结果只证明记录时的读取，不证明可以安全继续执行。"
        : "This legacy archive has no complete capture-revision or consistency declaration. These observations do not authorize resuming work."}</p>
      <ul className="personal-recovery-components">{audit.components.map(row => <li key={row.component_id}>
        <strong>{ownerNames[row.owner_kind]?.[index] ?? (zh ? "待核对组件" : "Component requiring review")}</strong>
        {typeof row.facts.goal_id === "string" ? <span> · {row.facts.goal_id}</span> : null}
        {" · "}{row.state === "readable" ? (zh ? "可读" : "Readable") : row.state === "historical_only" ? (zh ? "仅历史" : "Historical only") : (zh ? "未知" : "Unknown")}
        {typeof row.facts.todo_count === "number" ? <p>{zh ? "保存的任务" : "Recorded tasks"}: {row.facts.todo_count} · {zh ? "历史提交" : "Historical commits"}: {String(row.facts.history_commits)}</p> : null}
        <p>{nextActions[row.next_action]?.[index] ?? (zh ? "请对应 owner 核对。" : "Ask the owning module to review this component.")}</p>
      </li>)}</ul>
      <details><summary>{zh ? "核对信息" : "Verification details"}</summary><code>{audit.recovery_id}</code>
        {audit.measurements ? <p>{zh ? "归档字节" : "Archive bytes"}: {audit.measurements.archive_bytes.toLocaleString(locale)} · {zh ? "普通文件字节" : "Regular file bytes"}: {audit.measurements.regular_file_bytes.toLocaleString(locale)}<br />
          {zh ? "归档检查与复制" : "Archive checks and copy"}: {audit.measurements.archive_processing_ms.toLocaleString(locale)} ms · {zh ? "记录读取" : "Owner reads"}: {audit.measurements.owner_read_ms.toLocaleString(locale)} ms</p>
          : <p>{zh ? "此历史报告未记录数据量和阶段耗时。" : "This historical report has no recorded size or phase durations."}</p>}
        <ul>{audit.components.map(row => <li key={row.component_id}><code>{row.component_id}: {row.reason_code}</code>
          {row.owner_kind === "coordination_authority" && row.state === "readable" ? <p>
            {typeof row.facts.provider_revision === "string"
              ? <>{zh ? "备份中的存储检查点" : "Checkpoint in backup"}: <code>{row.facts.provider_revision}</code> · {zh ? "游标" : "Cursor"}: <code>{String(row.facts.cursor)}</code></>
              : (zh ? "这份历史报告未记录存储检查点。" : "This historical report did not record a storage checkpoint.")}
          </p> : null}
        </li>)}</ul>
      </details>
    </div> : null}
    {history ? <div aria-label={zh ? "历史备份检查" : "Previous backup inspections"}>
      {history.length === 0 ? <p>{zh ? "尚无检查结果。" : "No saved inspection yet."}</p> : null}
      {history.map(row => <p key={row.checkpoint_ref}><button className="personal-secondary-action" type="button" disabled={busy}
        onClick={() => void perform(async () => setAudit(await readStateRecovery(row.checkpoint_ref)))}>
        {row.available && row.observed_at ? new Date(row.observed_at).toLocaleString(locale) : (zh ? "无法读取的历史报告" : "Unavailable historical report")}
      </button></p>)}
      {more ? <p>{zh ? "显示最近 10 份检查。" : "Showing the 10 latest inspections."}</p> : null}
    </div> : null}
  </section>;
}
