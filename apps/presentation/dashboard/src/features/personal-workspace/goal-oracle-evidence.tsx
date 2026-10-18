import type {OracleEvidence} from "../../data/goal-acceptance-observation";
import {goalAcceptanceContractCopy} from "./delivery-review-copy";
export function GoalOracleEvidence({evidence, zh}: {evidence: OracleEvidence; zh: boolean}) {
  const copy = goalAcceptanceContractCopy[zh ? "zh-CN" : "en"];
  return <div className="delivery-oracle-evidence">
    <p><strong>{copy.oracleChecker}</strong> · {evidence.source_id}</p>
    <p><time dateTime={evidence.checked_at}>{evidence.checked_at}</time></p>
    <p>{zh ? "检查在声明文件的隔离副本上执行；不代表远程复核者身份或异议已解决。" : "Checks ran on copies of the declared files. This does not authenticate a remote reviewer or resolve an objection."}</p>
    <details><summary>{zh ? "查看来源与实际检查版本" : "Source and tested versions"}</summary>
      <p>{copy.oracleSourceDigest} · <code>{evidence.source_digest}</code></p>
      {(["input", "output"] as const).map(role => <div key={role}>
        <h4>{role === "input" ? copy.oracleInputVersions : copy.oracleOutputVersions}</h4>
        <ul>{evidence[`${role}_versions`].map(row => <li key={row.ref}>{row.ref} · <code>{row.sha256}</code></li>)}</ul>
      </div>)}
    </details>
  </div>;
}
