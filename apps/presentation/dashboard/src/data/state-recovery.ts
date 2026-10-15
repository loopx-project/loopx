import {requestJson} from "./chat";
import {decodeStateRecoveryAudit, type StateRecoveryAudit} from "../../../../../loopx/control_plane/state_recovery_contract";

export type StateRecoveryResult = StateRecoveryAudit & {checkpoint_ref: string; historical_readback?: boolean};
export type RecoveryHistoryItem = {
  checkpoint_ref: string; available: boolean; observed_at?: string;
  summary?: StateRecoveryAudit["summary"];
};
const endpoint = "/api/chat/state-recovery";

export async function stateRecoveryProfile() {
  const result = await requestJson<{supported: boolean; activation_available: false}>(`${endpoint}/profile`);
  if (typeof result.supported !== "boolean" || result.activation_available !== false) throw new Error("Recovery profile is invalid");
  return result;
}

export async function uploadRecoveryInput(file: File, kind: "archive" | "manifest") {
  return requestJson<{input_ref: string; checkpoint_ref?: string}>(`${endpoint}/${kind}`, {
    method: "POST", headers: {"Content-Type": "application/octet-stream"}, body: file,
  });
}
function result(value: unknown): StateRecoveryResult {
  const audit = decodeStateRecoveryAudit(value);
  if (typeof audit.checkpoint_ref !== "string" || !/^[a-f0-9]{32}$/.test(audit.checkpoint_ref)) {
    throw new Error("Recovery checkpoint reference is invalid");
  }
  return audit as StateRecoveryResult;
}
export async function inspectStateBackup(archiveRef: string, manifestRef: string) {
  return result(await requestJson<unknown>(`${endpoint}/verify`, {method: "POST",
    body: JSON.stringify({archive_ref: archiveRef, manifest_ref: manifestRef, execute: true})}));
}
export async function readStateRecovery(checkpointRef: string) {
  return result(await requestJson<unknown>(`${endpoint}/read`, {method: "POST",
    body: JSON.stringify({checkpoint_ref: checkpointRef})}));
}
export function listStateRecoveries() {
  return requestJson<{items: RecoveryHistoryItem[]; has_more: boolean}>(endpoint);
}
