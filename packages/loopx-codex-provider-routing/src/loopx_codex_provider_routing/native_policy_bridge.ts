import { decide } from "./native_policy.ts";
let raw = "";
for await (const chunk of process.stdin) {
  raw += chunk;
  if (raw.length > 131072) throw Error("Policy request exceeds limit");
}
process.stdout.write(JSON.stringify(decide(JSON.parse(raw))));
