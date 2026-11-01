import { decide, observerHealth } from "./native_policy.ts";
let raw = "";
for await (const chunk of process.stdin) {
  raw += chunk;
  if (raw.length > 131072) throw Error("Policy request exceeds limit");
}
const input = JSON.parse(raw);
process.stdout.write(JSON.stringify(input.operation === "observer-health" ? observerHealth(input) : decide(input)));
