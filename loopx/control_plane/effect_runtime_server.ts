import { writeSync } from "node:fs";
import { createServer, type Socket } from "node:net";
import { chmod, readFile, rm, type FileHandle } from "node:fs/promises";

import type { JsonObject } from "./effect_program.ts";
import {
  createEffectRuntimeHandlers,
  dispatchEffectRuntimeMethod,
} from "./effect_runtime_handlers.ts";
import {
  EffectRuntimeRequestError,
  effectRuntimeErrorPayload,
} from "./effect_runtime_errors.ts";
import { atomicWriteJson, withFileMutationLock } from "./effect_runtime_io.ts";
import { sqliteRuntimeIdentity } from "./coordination/sqlite_runtime.ts";
import {openPrivateResponseSink, readPrivateJsonSnapshot, writePrivateResponse} from "./effect_runtime_snapshot.ts";
import {
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
} from "./runtime_decode.ts";

const REQUEST_SCHEMA = "loopx_effect_runtime_request_v0";
const RESPONSE_SCHEMA = "loopx_effect_runtime_response_v1";
const INFO_SCHEMA = "loopx_effect_runtime_info_v0";
const STARTUP_ERROR_SCHEMA = "loopx_effect_runtime_startup_error_v0";
const MAX_REQUEST_BYTES = 2 * 1024 * 1024;
const MAX_INLINE_RESPONSE_BYTES = 2 * 1024 * 1024;
// Explicit opt-in: ordinary effects retain the 2 MiB request/response wire.
const LOCAL_SNAPSHOT_METHODS = new Set([
  "goal.checkpoint_read_context.source",
  "goal.checkpoint_read_context.evaluate",
  "goal.checkpoint_read_context.commit",
  "goal.checkpoint_read_context.inspect_replay",
]);
const DEFAULT_IDLE_MS = 5 * 60 * 1_000;
// Bounds for LOOPX_EFFECT_RUNTIME_IDLE_MS. The upper bound is the largest
// delay `setTimeout` accepts: a larger delay overflows and fires immediately,
// which is the same silent immediate-shutdown failure as a NaN or negative
// value.
const MIN_IDLE_MS = 1;
const MAX_IDLE_MS = 2 ** 31 - 1;
const STARTUP_CONFIG_EXIT_CODE = 2;
const INVALID_IDLE_TIMEOUT_CODE = "invalid_idle_timeout";
const MAX_REPORTED_RAW_LENGTH = 32;
let shutdownRequested = false;

function asObject(value: unknown): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
}

function parseArg(name: string): string {
  const index = process.argv.indexOf(name);
  if (index < 0 || index + 1 >= process.argv.length) {
    throw new Error(`missing ${name}`);
  }
  return requiredString(process.argv[index + 1], name);
}

function failStartup(code: string, message: string): never {
  writeSync(
    2,
    `${JSON.stringify({
      schema_version: STARTUP_ERROR_SCHEMA,
      code,
      message,
    })}\n`,
  );
  process.exit(STARTUP_CONFIG_EXIT_CODE);
}

function idleTimeoutGuidance(): string {
  return "LOOPX_EFFECT_RUNTIME_IDLE_MS must be a base-10 integer between " +
    `${MIN_IDLE_MS} and ${MAX_IDLE_MS} milliseconds; leave it unset to use ` +
    `the ${DEFAULT_IDLE_MS} ms default`;
}

function parseIdleMs(raw: string | undefined): number {
  if (raw === undefined) return DEFAULT_IDLE_MS;
  const received = JSON.stringify(raw.slice(0, MAX_REPORTED_RAW_LENGTH));
  if (!/^[0-9]+$/.test(raw)) {
    failStartup(
      INVALID_IDLE_TIMEOUT_CODE,
      `${idleTimeoutGuidance()} (received ${received})`,
    );
  }
  const parsed = Number(raw);
  if (
    !Number.isSafeInteger(parsed) ||
    parsed < MIN_IDLE_MS ||
    parsed > MAX_IDLE_MS
  ) {
    failStartup(
      INVALID_IDLE_TIMEOUT_CODE,
      `${idleTimeoutGuidance()} (received ${received})`,
    );
  }
  return parsed;
}

const infoPath = parseArg("--info");
const fingerprint = parseArg("--fingerprint");
const token = requiredString(process.env.LOOPX_EFFECT_RUNTIME_TOKEN, "runtime token");
const idleMs = parseIdleMs(process.env.LOOPX_EFFECT_RUNTIME_IDLE_MS);
let idleTimer: NodeJS.Timeout;
const handlers = createEffectRuntimeHandlers({
  fingerprint,
  requestShutdown: () => {
    shutdownRequested = true;
  },
});

function resetIdleTimer(server: ReturnType<typeof createServer>): void {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => server.close(), idleMs);
  idleTimer.unref();
}

async function writeResponse(socket: Socket, response: JsonObject, sink: FileHandle | null): Promise<void> {
  const encoded = Buffer.from(`${JSON.stringify(response)}\n`);
  if (encoded.length <= MAX_INLINE_RESPONSE_BYTES) {
    socket.end(encoded);
    return;
  }
  if (sink === null) {
    // The operation may already have committed. Never downgrade a lost large
    // response to a safe request rejection or retry it automatically.
    socket.destroy();
    return;
  }
  const ref = await writePrivateResponse(sink, encoded);
  socket.end(`${JSON.stringify({schema_version: RESPONSE_SCHEMA,
    request_id: response.request_id, ok: true, result_ref: ref})}\n`);
}

const server = createServer((socket) => {
  resetIdleTimer(server);
  socket.setEncoding("utf8");
  // A caller may close while a response is still buffered (for example after
  // reaching its byte budget). That socket's failure must not kill the shared
  // runtime or another in-flight operation. Business receipt recovery stays
  // with the caller; disconnecting never retries or reverses the handler.
  socket.on("error", () => socket.destroy());
  let raw = "";
  let receivedBytes = 0;
  socket.on("data", (chunk: string) => {
    receivedBytes += Buffer.byteLength(chunk, "utf8");
    if (receivedBytes > MAX_REQUEST_BYTES) {
      raw = "";
      socket.pause();
      socket.removeAllListeners("data");
      socket.end(`${JSON.stringify({
        schema_version: RESPONSE_SCHEMA,
        request_id: "unknown",
        ok: false,
        error: effectRuntimeErrorPayload(new EffectRuntimeRequestError(
          "Effect runtime request exceeds the 2 MiB limit",
          "request_too_large",
        )),
      })}\n`);
      return;
    }
    raw += chunk;
    if (!raw.includes("\n")) return;
    socket.pause();
    void (async () => {
      let requestId = "unknown";
      let sink: FileHandle | null = null;
      let dispatched = false;
      try {
        let parsed: unknown;
        try {
          parsed = JSON.parse(
            raw.slice(0, raw.indexOf("\n")),
          );
        } catch {
          throw new EffectRuntimeRequestError(
            "Effect runtime request is not valid JSON",
            "malformed_json",
          );
        }
        const request = requiredObject(parsed, "Effect runtime request");
        requestId = requiredString(request.request_id, "request_id");
        if (request.schema_version !== REQUEST_SCHEMA || request.token !== token) {
          throw new EffectRuntimeRequestError(
            "Effect runtime request authentication failed",
            "authentication_failed",
          );
        }
        const method = requiredString(request.method, "method");
        if (request.params_ref !== undefined || request.response_sink !== undefined) {
          if (!LOCAL_SNAPSHOT_METHODS.has(method) || request.response_sink === undefined ||
              (request.params_ref !== undefined && request.params !== undefined)) {
            throw new EffectRuntimeRequestError("local snapshot transport is unavailable for this request");
          }
          sink = await openPrivateResponseSink(request.response_sink);
        }
        let params: JsonObject;
        if (request.params_ref !== undefined) {
          const ref = requiredObject(request.params_ref, "params snapshot reference");
          if (ref.schema_version !== "loopx_effect_runtime_snapshot_v0") {
            throw new EffectRuntimeRequestError("invalid local snapshot schema");
          }
          params = requiredObject(await readPrivateJsonSnapshot(ref), "snapshot params");
        } else {
          params = asObject(request.params);
        }
        const result = await dispatchEffectRuntimeMethod(
          handlers,
          method,
          params,
        );
        dispatched = true;
        await writeResponse(socket, {
          schema_version: RESPONSE_SCHEMA,
          request_id: requestId,
          ok: true,
          result,
        }, sink);
        if (shutdownRequested) setImmediate(() => server.close());
      } catch (error) {
        if (dispatched) socket.destroy();
        else {
          try {
            await writeResponse(socket, {
              schema_version: RESPONSE_SCHEMA,
              request_id: requestId,
              ok: false,
              error: effectRuntimeErrorPayload(error),
            }, sink);
          } catch { socket.destroy(); }
        }
      } finally {
        await sink?.close();
      }
    })();
  });
});

server.on("close", () => {
  void withFileMutationLock(infoPath, async () => {
    let published: Record<string, unknown>;
    try {
      published = JSON.parse(await readFile(infoPath, "utf8"));
    } catch {
      return;
    }
    // A timed-out client may have published a replacement server. The old
    // server must never erase that server's locator when it finally exits.
    if (published.token === token && published.pid === process.pid &&
        published.fingerprint === fingerprint) {
      await rm(infoPath, { force: true });
    }
  }).finally(() => process.exit(0));
});

server.listen(0, "127.0.0.1", async () => {
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("invalid address");
  await withFileMutationLock(infoPath, async () => {
    await atomicWriteJson(infoPath, {
      schema_version: INFO_SCHEMA,
      fingerprint,
      pid: process.pid,
      host: "127.0.0.1",
      port: address.port,
      token,
      // A managed runtime is reused per source revision, so the Node/SQLite pair
      // serving a goal is not necessarily the one the caller resolves from PATH.
      runtime_identity: sqliteRuntimeIdentity(),
    });
    await chmod(infoPath, 0o600);
  });
  resetIdleTimer(server);
});
