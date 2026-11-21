import { createEffectRuntimeHandlers } from "./effect_runtime_handlers.ts";
import {
  exchangeEffectRuntimeRequest,
  MAX_REQUEST_BYTES,
  oversizedEffectRuntimeRequest,
} from "./effect_runtime_exchange.ts";
import { sqliteRuntimeIdentity } from "./coordination/sqlite_runtime.ts";
import { requireNonEmptyString } from "./runtime_decode.ts";

const fingerprint = requireNonEmptyString(process.argv[2], "runtime fingerprint");
const token = requireNonEmptyString(process.env.LOOPX_EFFECT_RUNTIME_TOKEN, "runtime token");
let stopping = false;
const handlers = createEffectRuntimeHandlers({
  fingerprint,
  localRuntimeIdentity: sqliteRuntimeIdentity(),
  requestShutdown: () => { stopping = true; },
});

// A caller can disconnect while an admitted effect is still running. Finish
// that effect before observing the failed write and retiring this process.
process.stdout.on("error", () => undefined);
function reply(frame: Buffer): Promise<boolean> {
  return new Promise(resolve => process.stdout.write(frame, error => resolve(!error)));
}

async function serve(): Promise<void> {
  let chunks: Buffer[] = [];
  let bytes = 0;
  for await (const chunk of process.stdin) {
    let start = 0;
    while (start < chunk.length) {
      const end = chunk.indexOf(10, start);
      const part = chunk.subarray(start, end === -1 ? chunk.length : end + 1) as Buffer;
      bytes += part.length;
      if (bytes > MAX_REQUEST_BYTES) {
        await reply(oversizedEffectRuntimeRequest());
        return;
      }
      chunks.push(part);
      if (end === -1) break;
      const line = Buffer.concat(chunks, bytes).toString("utf8", 0, bytes - 1);
      chunks = [];
      bytes = 0;
      const response = await exchangeEffectRuntimeRequest(line, token, handlers);
      if (response === null) return;
      if (!await reply(response) || stopping) return;
      start = end + 1;
    }
  }
}

await serve();
