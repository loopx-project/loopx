/** Private one-Turn JSONL boundary. The parent Host owns the hard deadline. */
import type {Writable} from "node:stream";
import type {OarOutputRecord, OarSessionConfig} from "./runtime.ts";

// Wire fragments stay below HostOutputLines' 1 MiB limit even when JSON must
// escape every code unit. The limit applies to the complete serialized record.
const FRAGMENT_THRESHOLD = 256 * 1024;
const FRAGMENT_CHARACTERS = 64 * 1024;
const MAX_RECORD_BYTES = 32 * 1024 * 1024;
const cancelled = new AbortController();
const abort = () => cancelled.abort();
process.on("SIGTERM", abort);
process.on("SIGINT", abort);
process.stdout.on("error", abort);
process.stderr.on("error", abort);
let writes = Promise.resolve();
let sequence = 0;

function write(stream: Writable, text: string): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    stream.write(text, error => error ? reject(error) : resolve());
  });
}

async function writeRecord(serialized: string): Promise<void> {
  const bytes = Buffer.byteLength(serialized, "utf8");
  if (bytes > MAX_RECORD_BYTES) throw new Error("OAR record exceeds transport limit");
  if (bytes <= FRAGMENT_THRESHOLD) {
    await write(process.stdout, serialized + "\n");
    return;
  }
  const recordSequence = sequence++;
  let index = 0;
  for (let start = 0; start < serialized.length;) {
    let end = Math.min(start + FRAGMENT_CHARACTERS, serialized.length);
    // Avoid turning one astral character into two isolated surrogates when the
    // Python JSON decoder observes the individually escaped fragment strings.
    const previous = serialized.charCodeAt(end - 1);
    const next = serialized.charCodeAt(end);
    if (previous >= 0xd800 && previous <= 0xdbff && next >= 0xdc00 && next <= 0xdfff) end--;
    await write(process.stdout, JSON.stringify({type: "fragment", sequence: recordSequence,
      index: index++, last: end === serialized.length, data: serialized.slice(start, end)}) + "\n");
    start = end;
  }
}

function emit(record: OarOutputRecord): void {
  // Observer callbacks cannot throw into the OAR owner. Retain a rejected write
  // instead, abort the Turn, and refuse successful process exit below.
  try {
    const serialized = JSON.stringify(record);
    writes = writes.then(() => writeRecord(serialized));
  } catch (error) {
    writes = writes.then(() => { throw error; });
  }
  void writes.catch(abort);
}

try {
  let input = Buffer.alloc(0);
  for await (const chunk of process.stdin) {
    input = Buffer.concat([input, chunk as Buffer]);
    if (input.length > 8 * 1024 * 1024) throw new Error("OAR request exceeds transport limit");
  }
  const request: unknown = JSON.parse(input.toString("utf8"));
  if (!request || typeof request !== "object" || Array.isArray(request)) throw new TypeError("invalid OAR request");
  const value = request as OarSessionConfig & {prompt: string};
  if (!["codex", "pi", "dsh"].includes(value.runtime) || typeof value.cwd !== "string" || !value.cwd ||
      typeof value.prompt !== "string") throw new TypeError("invalid OAR request fields");
  // This dedicated process owns the OAR environment. It cannot mutate the
  // caller's environment or the policy of another concurrent Session.
  process.env.OAR_KILL_GRACE_MS = "100";
  if (value.runtime === "codex") process.env.OAR_CHILD_STDERR = "inherit";
  const {runOarTurn} = await import("./runtime.ts");
  const result = await runOarTurn(value, value.prompt, {signal: cancelled.signal, onRecord: emit});
  await writes;
  process.exitCode = result.outcome.kind === "completed" ? 0 : 1;
} catch {
  process.stderr.write("OAR runtime request failed; check runtime installation and the selected native provider.\n");
  process.exitCode = 1;
} finally {
  await writes.catch(() => { process.exitCode = 1; });
  // Explicit exit must not discard native stderr already queued by OAR. The
  // callback runs only after preceding writes have reached the parent pipe.
  await write(process.stderr, "").catch(() => { process.exitCode = 1; });
  process.removeListener("SIGTERM", abort);
  process.removeListener("SIGINT", abort);
}
// This is a one-Turn process, never the reusable session API. OAR's documented
// exit hook also kills owned groups whose leader exited while descendants kept
// native pipes open; leaving the event loop alive would strand those tools.
process.exit(process.exitCode ?? 1);
