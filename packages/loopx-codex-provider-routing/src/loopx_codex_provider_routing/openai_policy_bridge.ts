import { apiKeys, apiOutcome, apiStreamOutcome } from "./openai_policy.ts";
let raw = "";
for await (const chunk of process.stdin) {
  raw += chunk;
  if (raw.length > 131072) throw Error("Policy request exceeds limit");
}
const input = JSON.parse(raw);
process.stdout.write(JSON.stringify(input.operation === "candidates" ? apiKeys(input)
  : input.operation === "stream-outcome" ? apiStreamOutcome(input.event) : apiOutcome(input)));
