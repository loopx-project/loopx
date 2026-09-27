/** Private-process test driver, never installed as an RPC effect. */
import {drainShadowOutbox} from "../coordination/shadow_drain.ts";
let input = "";
for await (const bytes of process.stdin) input += bytes;
const request = JSON.parse(input);
const phase = process.argv[2] === "between_unlinks" ? "after_unlink" : process.argv[2];
const result = await drainShadowOutbox(request, {afterEffect: async observed => {
  if (observed === phase) {
    process.stdout.write(`BARRIER ${JSON.stringify({native_pid: process.pid, request})}\n`);
    await new Promise(() => {setInterval(() => {}, 1000);});
  }
}});
process.stdout.write(JSON.stringify(result) + "\n");
