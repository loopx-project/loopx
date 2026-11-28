import { writeSync } from "node:fs";
import { createServer, type Socket } from "node:net";
import { realpath } from "node:fs/promises";

import { createEffectRuntimeHandlers } from "./effect_runtime_handlers.ts";
import {
  exchangeEffectRuntimeRequest,
  MAX_REQUEST_BYTES,
  oversizedEffectRuntimeRequest,
} from "./effect_runtime_exchange.ts";
import { effectRuntimeErrorPayload } from "./effect_runtime_errors.ts";
import { atomicWriteJson, withFileMutationLock } from "./effect_runtime_io.ts";
import { retireRuntime } from "./effect_runtime_compile_cache.ts";
import { sqliteRuntimeIdentity } from "./coordination/sqlite_runtime.ts";
import { requireNonEmptyString as requiredString } from "./runtime_decode.ts";

const INFO_SCHEMA = "loopx_effect_runtime_info_v0";
const STARTUP_ERROR_SCHEMA = "loopx_effect_runtime_startup_error_v0";
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
let publishedInfoPath: string | undefined;
const fingerprint = parseArg("--fingerprint");
const token = requiredString(process.env.LOOPX_EFFECT_RUNTIME_TOKEN, "runtime token");
const idleMs = parseIdleMs(process.env.LOOPX_EFFECT_RUNTIME_IDLE_MS);
let idleTimer: ReturnType<typeof setTimeout> | undefined;
let pendingRequests = 0;
let resolveDrain: (() => void) | undefined;
let publicationComplete = false;
let resolvePublication: () => void;
const publicationReady = new Promise<void>((resolve) => { resolvePublication = resolve; });
const handlers = createEffectRuntimeHandlers({
  fingerprint,
  get infoPath() { return publishedInfoPath; },
  requestShutdown: () => {
    shutdownRequested = true;
  },
});

function resetIdleTimer(server: ReturnType<typeof createServer>): void {
  clearTimeout(idleTimer);
  idleTimer = undefined;
  // Idleness starts after effects finish, not when their sockets connect.
  if (!publicationComplete || pendingRequests > 0 || shutdownRequested || !server.listening) return;
  idleTimer = setTimeout(() => {
    idleTimer = undefined;
    server.close();
    // close() waits for every connection; one whose peer died without a
    // request would keep this process alive but no longer listening.
    for (const socket of sockets) socket.destroy();
  }, idleMs);
  idleTimer.unref();
}

const sockets = new Set<Socket>();
const pendingDispatchSockets = new Set<Socket>();

async function dispatchSocketRequest(
  server: ReturnType<typeof createServer>,
  socket: Socket,
  requestLine: string,
): Promise<void> {
  let line: string | undefined = requestLine;
  pendingRequests += 1;
  clearTimeout(idleTimer);
  idleTimer = undefined;
  try {
    // The locator becomes visible inside the publication lock. A first
    // response must wait for its release: otherwise an immediate process
    // death can strand an incomplete cleanup claim before the next write.
    await publicationReady;
    const input = line;
    line = undefined;
    const frame = await exchangeEffectRuntimeRequest(input, token, handlers);
    if (frame === null || socket.destroyed || !socket.writable) socket.destroy();
    else {
      socket.setTimeout(idleMs);
      socket.end(frame);
    }
  } catch {
    socket.destroy();
  } finally {
    line = undefined;
    pendingRequests -= 1;
    if (pendingRequests === 0) {
      resolveDrain?.();
      resolveDrain = undefined;
    }
    // Stop accepting new work immediately on shutdown, but drain every
    // already accepted handler, including those whose caller disconnected.
    if (shutdownRequested) {
      if (server.listening) server.close();
      for (const idleSocket of pendingDispatchSockets) idleSocket.destroy();
    }
    resetIdleTimer(server);
  }
}

const server = createServer((socket) => {
  sockets.add(socket);
  pendingDispatchSockets.add(socket);
  resetIdleTimer(server);
  socket.setEncoding("utf8");
  let raw = "";
  let receivedBytes = 0;
  let dispatched = false;
  const clearIncomingBuffer = (): void => {
    raw = "";
    receivedBytes = 0;
    socket.removeAllListeners("data");
  };
  socket.setTimeout(idleMs);
  socket.on("timeout", () => {
    if (!dispatched || socket.writableFinished) {
      clearIncomingBuffer();
      socket.destroy();
    }
  });
  // A caller may close while a response is still buffered (for example after
  // reaching its byte budget). That socket's failure must not kill the shared
  // runtime or another in-flight operation. Business receipt recovery stays
  // with the caller; disconnecting never retries or reverses the handler.
  socket.on("error", () => {
    clearIncomingBuffer();
    pendingDispatchSockets.delete(socket);
    socket.destroy();
  });
  socket.on("end", () => {
    if (!dispatched) {
      clearIncomingBuffer();
      pendingDispatchSockets.delete(socket);
    }
  });
  socket.on("close", () => {
    clearIncomingBuffer();
    pendingDispatchSockets.delete(socket);
    sockets.delete(socket);
  });
  socket.on("data", (chunk: string) => {
    receivedBytes += Buffer.byteLength(chunk, "utf8");
    if (receivedBytes > MAX_REQUEST_BYTES) {
      dispatched = true;
      clearIncomingBuffer();
      pendingDispatchSockets.delete(socket);
      socket.pause();
      socket.setTimeout(idleMs);
      socket.end(oversizedEffectRuntimeRequest());
      return;
    }
    raw += chunk;
    // Earlier chunks had no delimiter. Scanning the accumulated string on each
    // chunk repeatedly flattens it; only the newly received chunk can finish it.
    if (!chunk.includes("\n")) return;
    dispatched = true;
    const line = raw.slice(0, raw.indexOf("\n"));
    clearIncomingBuffer();
    pendingDispatchSockets.delete(socket);
    socket.setTimeout(0);
    socket.pause();
    void dispatchSocketRequest(server, socket, line);
  });
});

server.on("close", () => {
  clearTimeout(idleTimer);
  idleTimer = undefined;
  void (async () => {
    // TCP close does not wait for a handler after its client times out.
    if (pendingRequests > 0) {
      await new Promise<void>((resolve) => { resolveDrain = resolve; });
    }
    await retireRuntime({ infoPath, token, pid: process.pid, fingerprint });
  })().finally(() => process.exit(0));
});

function failListenerStartup(error: Error): never {
  const { code } = effectRuntimeErrorPayload(error);
  const diagnosticCode = code === "io_permission_denied"
    ? "runtime_host_permission_denied" : code;
  failStartup(diagnosticCode,
    `TypeScript Effect runtime could not start its loopback listener (${diagnosticCode})`);
}

server.once("error", failListenerStartup);
server.listen(0, "127.0.0.1", async () => {
  server.removeListener("error", failListenerStartup);
  try {
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
      // Freeze the native publication namespace while it exists. Test cleanup
      // may observe it after the fixture directory was removed; a copied
      // locator's contents cannot rewrite this authenticated boot observation.
      publishedInfoPath = await realpath(infoPath);
    });
    publicationComplete = true;
    resolvePublication();
    resetIdleTimer(server);
  } catch (error) {
    // Reuse the shared error owner's safe codes, never the raw Node error,
    // locator path, token or stack. The launcher already consumes this startup
    // envelope; publication failure must not collapse into a bare exit code.
    const { code } = effectRuntimeErrorPayload(error);
    failStartup(code, `TypeScript Effect runtime could not publish its startup locator (${code})`);
  }
});
