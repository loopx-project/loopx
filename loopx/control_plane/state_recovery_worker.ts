/** Disposable offline verifier: untrusted database work cannot stall the daemon. */
import {auditStateRecoveryOwners} from "./state_recovery.ts";

try {
  let size = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    const data = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += data.length;
    if (size > 2 * 1024 * 1024) throw new Error("input capacity");
    chunks.push(data);
  }
  const result = await auditStateRecoveryOwners(JSON.parse(Buffer.concat(chunks).toString("utf8")));
  process.stdout.write(JSON.stringify(result));
  process.exitCode = result.ok === true ? 0 : 1;
} catch {
  process.stdout.write(JSON.stringify({ok: false, error_code: "recovery_owner_unavailable",
    execution_authority_granted: false}));
  process.exitCode = 1;
}
