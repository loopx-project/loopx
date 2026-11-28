import type { FileHandle } from "node:fs/promises";

import type { JsonObject } from "./effect_program.ts";
import {
  type EffectRuntimeHandler,
  dispatchEffectRuntimeMethod,
} from "./effect_runtime_handlers.ts";
import {
  EffectRuntimeRequestError,
  effectRuntimeErrorPayload,
} from "./effect_runtime_errors.ts";
import {
  MAX_INLINE_RESPONSE_BYTES,
  openPrivateResponseSink,
  readPrivateJsonSnapshot,
  writePrivateResponse,
} from "./effect_runtime_snapshot.ts";
import localSnapshotMethods from "./effect_runtime_snapshot_methods.json" with { type: "json" };
import {
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
} from "./runtime_decode.ts";

const REQUEST_SCHEMA = "loopx_effect_runtime_request_v0";
const RESPONSE_SCHEMA = "loopx_effect_runtime_response_v1";
export const MAX_REQUEST_BYTES = 8 * 1024 * 1024;
// Transport eligibility stays shared with the caller. The selected handler owns authority.
const LOCAL_SNAPSHOT_METHODS = new Set(localSnapshotMethods);

function asParams(value: unknown): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
}

function encode(value: JsonObject): Buffer {
  return Buffer.from(`${JSON.stringify(value)}\n`);
}

function failure(requestId: string, error: unknown): JsonObject {
  return {
    schema_version: RESPONSE_SCHEMA,
    request_id: requestId,
    ok: false,
    error: effectRuntimeErrorPayload(error),
  };
}

export function oversizedEffectRuntimeRequest(): Buffer {
  return encode(failure("unknown", new EffectRuntimeRequestError(
    "Effect runtime request exceeds the 8 MiB limit",
    "request_too_large",
  )));
}

async function writeEncodedResponse(
  requestId: unknown,
  encoded: Buffer,
  sink: FileHandle | null,
): Promise<Buffer | null> {
  if (encoded.length <= MAX_INLINE_RESPONSE_BYTES) return encoded;
  // A success may already be durable. Return no frame when the existing
  // bounded snapshot path cannot deliver it, so callers recover its receipt.
  if (sink === null) return null;
  return encode({
    schema_version: RESPONSE_SCHEMA,
    request_id: requestId,
    ok: true,
    result_ref: await writePrivateResponse(sink, encoded),
  });
}

function decodeExchangeEnvelope(
  line: string,
  token: string,
  onRequestId: (id: string) => void,
): { requestId: string; method: string; paramsRef: unknown; inlineParams: JsonObject; responseSinkRef: unknown } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch {
    throw new EffectRuntimeRequestError(
      "Effect runtime request is not valid JSON",
      "malformed_json",
    );
  }
  const request = requiredObject(parsed, "Effect runtime request");
  const requestId = requiredString(request.request_id, "request_id");
  onRequestId(requestId);
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
  }
  return {
    requestId,
    method,
    paramsRef: request.params_ref,
    inlineParams: asParams(request.params),
    responseSinkRef: request.response_sink,
  };
}

export async function exchangeEffectRuntimeRequest(
  rawLine: string, token: string, handlers: ReadonlyMap<string, EffectRuntimeHandler>,
): Promise<Buffer | null> {
  let line: string | undefined = rawLine;
  let requestId = "unknown";
  let sink: FileHandle | null = null;
  let encoded: Buffer;
  try {
    const envelope = decodeExchangeEnvelope(line, token, (id) => { requestId = id; });
    // Drop the raw wire string before running async snapshot reads or handlers.
    line = undefined;
    if (envelope.responseSinkRef !== undefined) {
      sink = await openPrivateResponseSink(envelope.responseSinkRef);
    }
    let params: JsonObject | undefined;
    if (envelope.paramsRef !== undefined) {
      const ref = requiredObject(envelope.paramsRef, "params snapshot reference");
      if (ref.schema_version !== "loopx_effect_runtime_snapshot_v0") {
        throw new EffectRuntimeRequestError("invalid local snapshot schema");
      }
      params = requiredObject(await readPrivateJsonSnapshot(ref), "snapshot params");
    } else {
      params = envelope.inlineParams;
    }
    const callParams = params;
    params = undefined;
    const result = await dispatchEffectRuntimeMethod(handlers, envelope.method, callParams);
    encoded = encode({
      schema_version: RESPONSE_SCHEMA,
      request_id: requestId,
      ok: true,
      result,
    });
  } catch (error) {
    line = undefined;
    encoded = encode(failure(requestId, error));
  }
  try {
    return await writeEncodedResponse(requestId, encoded, sink);
  } finally {
    await sink?.close();
  }
}
