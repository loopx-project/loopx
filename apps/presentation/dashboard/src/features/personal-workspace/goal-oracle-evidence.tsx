import type {OracleEvidence} from "../../data/goal-acceptance-observation";
export function GoalOracleEvidence({evidence, zh}: {evidence: OracleEvidence; zh: boolean}) {
  return <div className="delivery-oracle-evidence">
    <p><strong>{zh ? "负责人指定的版本固定检查器" : "Owner-pinned checker"}</strong> · {evidence.source_id}</p>
    <p><time dateTime={evidence.checked_at}>{evidence.checked_at}</time></p>
    <p>{zh ? "检查在声明文件的隔离副本上执行；不代表远程复核者身份或异议已解决。" : "Checks ran on copies of the declared files. This does not authenticate a remote reviewer or resolve an objection."}</p>
    <details><summary>{zh ? "查看来源与实际检查版本" : "Source and tested versions"}</summary>
      <p>{zh ? "来源摘要" : "Source digest"} · <code>{evidence.source_digest}</code></p>
      {(["input", "output"] as const).map(role => <div key={role}>
        <h4>{role === "input" ? (zh ? "输入版本" : "Input versions") : (zh ? "输出版本" : "Output versions")}</h4>
        <ul>{evidence[`${role}_versions`].map(row => <li key={row.ref}>{row.ref} · <code>{row.sha256}</code></li>)}</ul>
      </div>)}
    </details>
  </div>;
}
