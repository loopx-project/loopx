// Bounded source witness: execute shipped owners, without defining vocabulary
// values, classifying sources, or enrolling them in the production scanner.
import { readFileSync } from "node:fs";
import { reduceTurnSettlementTransaction } from "../loopx/control_plane/turn_driver/settlement.ts";
import { readQuotaSettlement } from "../loopx/control_plane/quota/settlement_readback.ts";
import { executeTaskLeaseAcquire } from "../loopx/control_plane/work_items/task_lease_acquire.ts";

const request = JSON.parse(readFileSync(0, "utf8"));
let result: unknown;
switch (request.operation) {
  case "turn":
    result = reduceTurnSettlementTransaction(request.input);
    break;
  case "readback":
    result = await readQuotaSettlement(request.input);
    break;
  case "lease_acquire":
    result = await executeTaskLeaseAcquire(request.input);
    break;
  default:
    throw new Error("unsupported settlement source witness operation");
}
process.stdout.write(JSON.stringify(result));
