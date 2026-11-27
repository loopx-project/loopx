import {useLayoutEffect, useRef, useState} from "react";
import {CheckCircle2, LoaderCircle, RefreshCw, AlertTriangle} from "lucide-react";
import {inspectGoalRemoteAuthority, type RemoteAuthorityInspection} from "../../data/remote-authority";
import {useWorkspaceI18n} from "./i18n";

// Presentation copy only; the existing owner decides the diagnostic and authority.
const chineseDiagnostics: Record<string, string> = {
  goal_unregistered: "当前 Goal 未注册，请重新选择已有 Goal。",
  actor_unregistered: "远程配置中的成员未在此 Goal 注册，请检查成员配置。",
  disabled: "此 Goal 尚未启用远程连接，本地任务不受影响。",
  binding_incomplete: "此 Goal 的远程连接配置不完整，请补齐已有配置后重试。",
  binding_invalid: "此 Goal 的远程连接配置无效，请检查配置格式。",
  actor_mismatch: "当前成员与远程配置不一致，请检查成员配置。",
  endpoint_invalid: "远程服务地址无效，请使用不含凭据或路径的 HTTPS 地址。",
  credential_required: "缺少服务凭据，请在运行 LoopX 的环境中配置后重试。",
  credential_invalid: "服务凭据格式无效，请检查运行 LoopX 的环境配置。",
  ca_invalid: "无法读取证书文件，请检查证书文件路径和权限。",
  dns: "无法解析远程服务地址，请检查地址和网络连接。",
  connection_refused: "远程服务拒绝连接，请检查服务是否运行及端口是否正确。",
  tls: "安全连接未通过验证，请检查服务证书、域名和证书有效期。",
  connection: "连接中断，请检查网络和服务状态后重试。",
  timeout: "检查超时，请检查网络和服务响应后重试。",
  http: "远程服务返回 HTTP 错误，请检查服务和代理配置。",
  response_invalid: "服务响应无法验证，请检查客户端与服务端的兼容性。",
  response_too_large: "服务响应超出读取限制，请检查服务地址是否正确。",
  unauthenticated: "服务拒绝了当前凭据，请检查凭据是否有效。",
  scope_denied: "当前凭据无权读取此 Goal 的任务，请联系服务管理员核对权限。",
  rejected: "服务拒绝读取，请联系服务管理员核对配置和访问策略。",
  todo_missing: "未找到配置中的检查任务，请核对任务和访问范围。",
  read_unverified: "任务读取未通过验证，请检查服务和连接配置。",
  read_verified: "已验证配置中的任务读取。此检查不会启动任务。",
  registry_unreadable: "无法读取本地 Goal 注册信息，请检查文件格式和访问权限。",
  node_unavailable: "本地运行环境不可用，请运行 LoopX 安装诊断并修复。",
  probe_unavailable: "本地检查程序未返回有效结果，请运行 LoopX 安装诊断后重试。",
};

export function GoalRemoteAuthorityInspection({goalId}: Readonly<{goalId: string}>) {
  const {locale} = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [result, setResult] = useState<RemoteAuthorityInspection | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  useLayoutEffect(() => {
    generation.current++;
    setResult(null); setFailed(false); setBusy(false);
    return () => {generation.current++;};
  }, [goalId]);

  async function inspect() {
    const current = ++generation.current;
    setBusy(true); setFailed(false); setResult(null);
    try {
      const value = await inspectGoalRemoteAuthority(goalId);
      if (current === generation.current) setResult(value);
    } catch {
      if (current === generation.current) setFailed(true);
    } finally {
      if (current === generation.current) setBusy(false);
    }
  }

  const title = zh ? "远程任务连接" : "Remote task connection";
  const translated = result && zh ? chineseDiagnostics[result.reason_code.replace(/^remote_authority_/, "")] : null;
  return <section className="personal-remote-authority" aria-label={title}>
    <div className="personal-remote-authority-heading">
      <div><h3>{title}</h3><p>{zh ? "读取此 Goal 的已配置任务，不会启动工作。" : "Read this Goal’s configured task without starting work."}</p></div>
      <button type="button" disabled={busy} onClick={() => void inspect()}>
        {busy ? <LoaderCircle aria-hidden size={16} className="personal-spin" /> : <RefreshCw aria-hidden size={16} />}
        {busy ? (zh ? "检查中…" : "Checking…") : result || failed ? (zh ? "重新检查" : "Check again") : (zh ? "检查连接" : "Check connection")}
      </button>
    </div>
    <div aria-live="polite" aria-busy={busy}>
      {failed ? <p className="personal-machine-error" role="alert">{zh ? "未能完成检查，请重试。" : "The check could not finish. Please try again."}</p> : null}
      {result ? <div className="personal-remote-authority-result" data-verified={result.ok}>
        <strong>{result.ok ? <CheckCircle2 aria-hidden size={16} /> : <AlertTriangle aria-hidden size={16} />}
          {result.ok ? (zh ? "读取已验证" : "Read verified") : (zh ? "读取未验证" : "Read unverified")}</strong>
        <p>{translated || result.message}</p>
        {!result.ok && !translated ? <p>{result.recommended_action}</p> : null}
        <details><summary>{zh ? "诊断详情" : "Diagnostic details"}</summary>
          <code>{result.reason_code}</code>
          {result.http_status !== undefined ? <p>HTTP {result.http_status}</p> : null}
          <p>{zh ? "检查结果不会授予任务执行权限。" : "This result does not admit task execution."}</p>
        </details>
      </div> : null}
    </div>
  </section>;
}
