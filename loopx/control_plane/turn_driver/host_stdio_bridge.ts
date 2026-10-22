/** Raw stdio relay for SDKs that detach their children from the managed Host.
 * EOF is loss of the owning client, not permission to finish pending tool work.
 * All termination, descendant drain and process-group decisions stay in the
 * existing runHostProcess owner. This transport neither parses MCP nor grants
 * authority to the selected command.
 */
import {once} from "node:events";
import {StringDecoder} from "node:string_decoder";
import {runHostProcess} from "./host_process.ts";

const owner = new AbortController();
const abort = () => owner.abort();
process.stdin.on("end", abort);
process.stdin.on("error", abort);
process.stdout.on("error", abort);
process.on("SIGTERM", abort);
process.on("SIGINT", abort);

try {
  const result = await runHostProcess({argv: process.argv.slice(2), cwd: process.cwd(),
    input: "", timeout_ms: null, drain_timeout_ms: 1000, stdout_limit_bytes: null}, async item => {
    const stream = item.kind === "stdout" ? process.stdout : process.stderr;
    if (!stream.write(item.text)) await once(stream, "drain");
  }, owner.signal, undefined, {openInput: write => {
    void (async () => {
      try {
        const decoder = new StringDecoder("utf8");
        for await (const chunk of process.stdin) {
          const text = decoder.write(chunk as Buffer);
          if (text) await write(text);
        }
        const tail = decoder.end();
        if (tail) await write(tail);
      } catch { abort(); }
    })();
  }});
  process.exitCode = result.outcome === "exited" && result.output_complete ? result.returncode ?? 1 : 1;
} catch {
  // Do not forward command arguments, tool payloads or provider diagnostics.
  process.stderr.write("Managed stdio process cleanup was not confirmed.\n");
  process.exitCode = 1;
} finally {
  process.stdin.destroy();
}
