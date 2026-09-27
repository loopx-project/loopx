/** Private-process test driver, never installed as an RPC effect. */
import {existsSync} from "node:fs";
import {drainShadowOutbox} from "../coordination/shadow_drain.ts";
let input = "";
for await (const bytes of process.stdin) input += bytes;
const request = JSON.parse(input);
const phase = process.argv[2] === "between_unlinks" ? "after_unlink" : process.argv[2];
// Without a release path the observed barrier is terminal (a crash window).
// With one, the same real batch continues after the caller resumes it, which is
// what a management interleaving needs.
const release = process.argv[3];
const result = await drainShadowOutbox(request, {afterEffect: async observed => {
  if (observed === phase) {
    process.stdout.write(`BARRIER ${JSON.stringify({native_pid: process.pid, request})}\n`);
    if (release === undefined) await new Promise(() => {setInterval(() => {}, 1000);});
    else while (!existsSync(release)) await new Promise(resolve => {setTimeout(resolve, 10);});
  }
}});
process.stdout.write(JSON.stringify(result) + "\n");
