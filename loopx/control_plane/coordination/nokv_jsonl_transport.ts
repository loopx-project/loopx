import { randomUUID } from "node:crypto";
import {
  spawn,
  type ChildProcessWithoutNullStreams,
  type SpawnOptionsWithoutStdio,
} from "node:child_process";
import { once } from "node:events";

import type { JsonObject } from "../effect_program.ts";
import {
  NoKVTransportProtocolError,
  NoKVTransportUnavailableError,
  type NoKVBlobCasRequest,
  type NoKVBlobCasResult,
  type NoKVBlobReadResult,
  type NoKVBlobTransport,
  type NoKVStoreIdentityResult,
  type NoKVTransportFailure,
} from "./nokv_authority_store.ts";
import {
  isAuthorityJsonObject,
} from "./authority_store_codec.ts";

const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
const DEFAULT_MAX_RESPONSE_BYTES = 32 * 1024 * 1024;
const TRANSPORT_CONSTRUCTION_TOKEN = Symbol("NoKVJsonLinesTransport.open");

export type NoKVJsonLinesProcessFactory = (
  command: string,
  args: readonly string[],
  options: SpawnOptionsWithoutStdio,
) => ChildProcessWithoutNullStreams;

export interface NoKVJsonLinesTransportOptions {
  /** Explicit command plus arguments, for example `[python, helper.py]`. */
  argv: readonly string[];
  /** Sent only over stdin in the helper's open handshake. */
  config: JsonObject;
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  request_timeout_ms?: number;
  max_response_bytes?: number;
  process_factory?: NoKVJsonLinesProcessFactory;
}

interface PendingResponse {
  resolve(value: JsonObject): void;
  reject(error: Error): void;
  timer: NodeJS.Timeout;
}

function positiveSafeInteger(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) {
    throw new NoKVTransportProtocolError(`${name} must be a positive safe integer`);
  }
  return value as number;
}

function requiredString(value: unknown, name: string): string {
  if (typeof value !== "string" || value.trim() !== value || value.length === 0) {
    throw new NoKVTransportProtocolError(`${name} must be a non-empty trimmed string`);
  }
  return value;
}

function responseFailure(value: JsonObject): NoKVTransportFailure {
  const status = value.status;
  if (status !== "unavailable" && status !== "failed") {
    throw new NoKVTransportProtocolError("helper response status is invalid");
  }
  return {
    status,
    reason_code: requiredString(value.reason_code, "helper reason code"),
    reason: requiredString(value.reason, "helper reason"),
  };
}

function canonicalBase64(value: unknown): Uint8Array {
  if (typeof value !== "string") {
    throw new NoKVTransportProtocolError("helper bytes_base64 must be a string");
  }
  const bytes = Buffer.from(value, "base64");
  if (bytes.toString("base64") !== value) {
    throw new NoKVTransportProtocolError("helper bytes_base64 is not canonical base64");
  }
  return bytes;
}

/**
 * Reusable process connection to the NoKV Python SDK helper.
 *
 * Construction is intentionally asynchronous and requires an explicit argv;
 * no runtime path selects NoKV merely by importing this module.
 */
export class NoKVJsonLinesTransport implements NoKVBlobTransport {
  private readonly child: ChildProcessWithoutNullStreams;
  private readonly requestTimeoutMs: number;
  private readonly maxResponseBytes: number;
  private readonly pending = new Map<string, PendingResponse>();
  private stdoutBuffer = Buffer.alloc(0);
  private terminalError: Error | null = null;
  private closing = false;

  constructor(
    options: NoKVJsonLinesTransportOptions,
    constructionToken: typeof TRANSPORT_CONSTRUCTION_TOKEN,
  ) {
    if (constructionToken !== TRANSPORT_CONSTRUCTION_TOKEN) {
      throw new NoKVTransportProtocolError(
        "NoKV JSON-lines transport must be created with open()",
      );
    }
    if (!Array.isArray(options.argv) || options.argv.length === 0) {
      throw new NoKVTransportProtocolError("NoKV helper argv must not be empty");
    }
    for (const value of options.argv) {
      requiredString(value, "NoKV helper argv entry");
    }
    this.requestTimeoutMs = options.request_timeout_ms ?? DEFAULT_REQUEST_TIMEOUT_MS;
    this.maxResponseBytes = options.max_response_bytes ?? DEFAULT_MAX_RESPONSE_BYTES;
    positiveSafeInteger(this.requestTimeoutMs, "NoKV helper request timeout");
    positiveSafeInteger(this.maxResponseBytes, "NoKV helper max response bytes");
    const factory = options.process_factory ?? ((command, args, spawnOptions) =>
      spawn(command, args, { ...spawnOptions, stdio: ["pipe", "pipe", "pipe"] }));
    const [command, ...args] = options.argv;
    try {
      this.child = factory(command!, args, {
        cwd: options.cwd,
        env: options.env,
      });
    } catch {
      throw new NoKVTransportUnavailableError("NoKV helper failed to start");
    }
    this.child.stdout.on("data", (chunk: Buffer | string) => {
      this.onStdout(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    });
    // Drain stderr so a noisy SDK cannot block, but never promote arbitrary
    // provider output into LoopX errors where endpoints or credentials could
    // escape the provider boundary.
    this.child.stderr.on("data", () => {});
    this.child.stdin.on("error", () => {
      if (!this.closing) this.failUnavailable("NoKV helper stdin failed");
    });
    this.child.on("error", () => {
      this.failUnavailable("NoKV helper failed to start");
    });
    this.child.on("exit", (code, signal) => {
      if (this.closing && this.pending.size === 0) return;
      this.failUnavailable(
        `NoKV helper disconnected (code=${String(code)}, signal=${String(signal)})`,
        false,
      );
    });
  }

  static async open(options: NoKVJsonLinesTransportOptions): Promise<NoKVJsonLinesTransport> {
    const transport = new NoKVJsonLinesTransport(
      options,
      TRANSPORT_CONSTRUCTION_TOKEN,
    );
    let response: JsonObject;
    try {
      response = await transport.exchange("open", { config: options.config });
      if (response.status === "unavailable") {
        throw new NoKVTransportUnavailableError(
          requiredString(response.reason, "helper open reason"),
        );
      }
      if (response.status !== "ready") {
        throw new NoKVTransportProtocolError(
          response.status === "failed" && typeof response.reason === "string"
            ? response.reason
            : "NoKV helper did not acknowledge its open handshake",
        );
      }
      return transport;
    } catch (error) {
      await transport.close();
      throw error;
    }
  }

  private fail(error: Error, terminate: boolean): void {
    if (this.terminalError === null) this.terminalError = error;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(this.terminalError);
    }
    this.pending.clear();
    if (terminate && this.child.exitCode === null && this.child.signalCode === null) {
      this.child.kill();
    }
  }

  private failUnavailable(message: string, terminate = true): void {
    this.fail(new NoKVTransportUnavailableError(message), terminate);
  }

  private failProtocol(message: string): void {
    this.fail(new NoKVTransportProtocolError(message), true);
  }

  private onStdout(chunk: Buffer): void {
    if (this.terminalError !== null) return;
    this.stdoutBuffer = Buffer.concat([this.stdoutBuffer, chunk]);
    while (true) {
      const newline = this.stdoutBuffer.indexOf(0x0a);
      if (newline < 0) break;
      if (newline > this.maxResponseBytes) {
        this.failProtocol("NoKV helper response exceeded max_response_bytes");
        return;
      }
      const line = this.stdoutBuffer.subarray(0, newline);
      this.stdoutBuffer = this.stdoutBuffer.subarray(newline + 1);
      if (line.byteLength === 0) {
        this.failProtocol("NoKV helper emitted an empty response line");
        return;
      }
      let value: unknown;
      try {
        value = JSON.parse(line.toString("utf8"));
      } catch (error) {
        this.failProtocol(
          `NoKV helper emitted invalid JSON: ${
            error instanceof Error ? error.message : "invalid JSON"
          }`,
        );
        return;
      }
      if (!isAuthorityJsonObject(value)) {
        this.failProtocol("NoKV helper response must be an object");
        return;
      }
      const requestId = value.request_id;
      if (typeof requestId !== "string") {
        this.failProtocol("NoKV helper response omitted request_id");
        return;
      }
      const pending = this.pending.get(requestId);
      if (!pending) {
        this.failProtocol("NoKV helper responded with an unknown request_id");
        return;
      }
      this.pending.delete(requestId);
      clearTimeout(pending.timer);
      pending.resolve(value);
    }
    if (this.stdoutBuffer.byteLength > this.maxResponseBytes) {
      this.failProtocol("NoKV helper response exceeded max_response_bytes");
    }
  }

  private async exchange(operation: string, values: JsonObject): Promise<JsonObject> {
    if (this.terminalError) throw this.terminalError;
    if (this.closing) {
      throw new NoKVTransportUnavailableError("NoKV helper transport is closed");
    }
    const requestId = randomUUID();
    const response = new Promise<JsonObject>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId);
        const error = new NoKVTransportUnavailableError(
          `NoKV helper request ${operation} timed out`,
        );
        reject(error);
        this.fail(error, true);
      }, this.requestTimeoutMs);
      this.pending.set(requestId, { resolve, reject, timer });
    });
    const line = JSON.stringify({ request_id: requestId, operation, ...values }) + "\n";
    try {
      this.child.stdin.write(line);
    } catch {
      this.failUnavailable("NoKV helper request write failed");
    }
    return await response;
  }

  async storeIdentity(workbench: string): Promise<NoKVStoreIdentityResult> {
    const response = await this.exchange("store_identity", { workbench });
    if (response.status === "available") {
      return {
        status: "available",
        store_identity: requiredString(
          response.store_identity,
          "helper store identity",
        ),
      };
    }
    return responseFailure(response);
  }

  async readBlob(workbench: string, path: string): Promise<NoKVBlobReadResult> {
    const response = await this.exchange("read_blob", { workbench, path });
    if (response.status === "missing") return { status: "missing" };
    if (response.status === "loaded") {
      return {
        status: "loaded",
        bytes: canonicalBase64(response.bytes_base64),
        generation: positiveSafeInteger(response.generation, "helper read generation"),
      };
    }
    return responseFailure(response);
  }

  async casPublishBlob(request: NoKVBlobCasRequest): Promise<NoKVBlobCasResult> {
    const response = await this.exchange("cas_publish_blob", {
      workbench: request.workbench,
      path: request.path,
      expected_generation: request.expected_generation,
      expected_workspace_incarnation_id: request.expected_workspace_incarnation_id,
      bytes_base64: Buffer.from(request.bytes).toString("base64"),
      operation_id: request.operation_id,
      artifact_revision_id: request.artifact_revision_id,
    });
    if (response.status === "applied") {
      return {
        status: "applied",
        generation: positiveSafeInteger(response.generation, "helper publish generation"),
      };
    }
    if (response.status === "conflict") {
      const current = response.current_generation;
      return {
        status: "conflict",
        current_generation: current === null
          ? null
          : positiveSafeInteger(current, "helper conflict generation"),
      };
    }
    if (response.status === "ambiguous") {
      return {
        status: "ambiguous",
        reason_code: requiredString(response.reason_code, "helper reason code"),
        reason: requiredString(response.reason, "helper reason"),
      };
    }
    const failure = responseFailure(response);
    return {
      status: "failed",
      reason_code: failure.reason_code,
      reason: failure.reason,
    };
  }

  async close(): Promise<void> {
    if (this.closing) return;
    this.closing = true;
    if (this.child.exitCode !== null || this.child.signalCode !== null) return;
    this.child.stdin.end();
    await Promise.race([
      once(this.child, "exit"),
      new Promise<void>((resolve) => setTimeout(resolve, 1_000)),
    ]);
    if (this.child.exitCode === null && this.child.signalCode === null) {
      this.child.kill();
    }
  }
}
