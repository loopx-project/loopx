/** Host-local bulk transport for complete source operations. Business handlers
 * keep their schemas, admission, source locks and durable operation identities. */
import {constants} from "node:fs";
import {lstat, open, realpath} from "node:fs/promises";
import {join, isAbsolute} from "node:path";
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {hasExactAuthorityKeys} from "./authority_store_codec.ts";

import {
  COORDINATION_STATE_CONTRACT,
  COORDINATION_SOURCE_TRANSFER_REQUEST_SCHEMA as SOURCE_TRANSFER_SCHEMA,
  COORDINATION_SOURCE_TRANSFER_RESULT_SCHEMA as SOURCE_TRANSFER_RESULT_SCHEMA,
} from "./coordination_state_contract.generated.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";
export {SOURCE_TRANSFER_SCHEMA, SOURCE_TRANSFER_RESULT_SCHEMA};
export const MAX_SOURCE_TRANSFER_BYTES = COORDINATION_STATE_CONTRACT.source_transfer_limits.max_bytes;
type Handler = (value: JsonObject) => unknown | Promise<unknown>;
const digest = (bytes: Uint8Array): string => createHash("sha256").update(bytes).digest("hex");
function ensure(value: unknown, message: string): asserts value {
  if (!value) throw new EffectRuntimeRequestError(message, "coordination_source_transfer_invalid");
}

/** Registered only on source-carrying operations, not a generic RPC escape hatch.
 * Inline callers retain the shipped protocol; production Python uses artifacts. */
export function withCoordinationSourceTransfer(method: string, handler: Handler): Handler {
  return async envelope => {
    if (envelope.schema_version !== SOURCE_TRANSFER_SCHEMA) return handler(envelope);
    ensure(hasExactAuthorityKeys(envelope, ["schema_version", "method", "directory", "request_sha256", "request_bytes"]) &&
      envelope.method === method && typeof envelope.directory === "string" && isAbsolute(envelope.directory) &&
      typeof envelope.request_sha256 === "string" && BARE_SHA256_PATTERN.test(envelope.request_sha256) &&
      typeof envelope.request_bytes === "number" && Number.isSafeInteger(envelope.request_bytes) &&
      envelope.request_bytes > 0 && envelope.request_bytes <= MAX_SOURCE_TRANSFER_BYTES,
    "invalid coordination source transfer envelope or artifact size");
    const directory = envelope.directory;
    const directoryStat = await lstat(directory);
    ensure(directoryStat.isDirectory() && !directoryStat.isSymbolicLink() && await realpath(directory) === directory,
      "coordination source transfer requires a private real directory");
    if (process.platform !== "win32") ensure((directoryStat.mode & 0o077) === 0 &&
      directoryStat.uid === process.getuid!(), "coordination source transfer directory is not private to this user");
    const path = join(directory, "request.json");
    const before = await lstat(path);
    ensure(before.isFile() && !before.isSymbolicLink() && before.size === envelope.request_bytes,
      "coordination source transfer request is not the witnessed regular file");
    const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
    let bytes: Buffer;
    try {
      const opened = await handle.stat();
      ensure(before.dev === opened.dev && before.ino === opened.ino && opened.size === envelope.request_bytes,
        "coordination source transfer request changed before reading");
      bytes = Buffer.alloc(envelope.request_bytes + 1);
      let offset = 0;
      while (offset < bytes.length) {
        const read = await handle.read(bytes, offset, bytes.length - offset, null);
        if (read.bytesRead === 0) break;
        offset += read.bytesRead;
      }
      bytes = bytes.subarray(0, offset);
    } finally { await handle.close(); }
    ensure(bytes.length === envelope.request_bytes && digest(bytes) === envelope.request_sha256,
      "coordination source transfer request digest mismatch");
    const request: unknown = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes));
    ensure(request !== null && typeof request === "object" && !Array.isArray(request),
      "coordination source transfer request must be an object");
    // Reserve before domain execution. Existing files/symlinks cannot make a
    // request execute and then overwrite a caller-selected destination.
    const output = await open(join(directory, "result.json"), "wx", 0o600);
    try {
      const result = await handler(request as JsonObject);
      const encoded = Buffer.from(JSON.stringify(result), "utf8");
      ensure(encoded.length <= MAX_SOURCE_TRANSFER_BYTES, "coordination source transfer result exceeds 16 MiB");
      await output.writeFile(encoded);
      return {schema_version: SOURCE_TRANSFER_RESULT_SCHEMA, method,
        request_sha256: envelope.request_sha256, result_sha256: digest(encoded), result_bytes: encoded.length};
    } finally { await output.close(); }
  };
}
