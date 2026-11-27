// Bounded source witness: execute shipped owners, without defining vocabulary
// values, classifying sources, or enrolling them in the production scanner.
import { readFileSync } from "node:fs";
import readline from "node:readline";
import { reduceTurnSettlementTransaction } from "../loopx/control_plane/turn_driver/settlement.ts";
import { readQuotaSettlement } from "../loopx/control_plane/quota/settlement_readback.ts";
import { executeTaskLeaseAcquire } from "../loopx/control_plane/work_items/task_lease_acquire.ts";

async function executeWitness(request: { operation: string; input: unknown }): Promise<unknown> {
  switch (request.operation) {
    case "turn":
      return reduceTurnSettlementTransaction(request.input as never);
    case "readback":
      return await readQuotaSettlement(request.input as never);
    case "lease_acquire":
      return await executeTaskLeaseAcquire(request.input as never);
    default:
      throw new Error("unsupported settlement source witness operation");
  }
}

if (process.argv.includes("--server")) {
  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of rl) {
    if (!line.trim()) continue;
    try {
      const result = await executeWitness(JSON.parse(line));
      process.stdout.write(JSON.stringify({ ok: true, result }) + "\n");
    } catch (error) {
      process.stdout.write(
        JSON.stringify({ ok: false, error: error instanceof Error ? error.message : String(error) }) + "\n",
      );
    }
  }
} else {
  const request = JSON.parse(readFileSync(0, "utf8"));
  process.stdout.write(JSON.stringify(await executeWitness(request)));
}
