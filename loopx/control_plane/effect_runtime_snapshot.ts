/** Private same-UID files for complete local facts that exceed the RPC wire.
 * The digest covers the bytes used by the typed owner; paths are never evidence. */
import {createHash} from "node:crypto";
import {constants} from "node:fs";
import {lstat, open, type FileHandle} from "node:fs/promises";
import {basename, dirname, isAbsolute} from "node:path";
import {EffectRuntimeRequestError} from "./effect_runtime_errors.ts";
import {requireJsonObject, requireNonEmptyString} from "./runtime_decode.ts";

export const MAX_LOCAL_SNAPSHOT_BYTES = 64 * 1024 * 1024;

async function privateFile(path: string, flags: number): Promise<FileHandle> {
  if (!isAbsolute(path) || !basename(dirname(path)).startsWith("loopx-effect-")) {
    throw new Error("invalid private snapshot path");
  }
  const parent = await lstat(dirname(path));
  if (!parent.isDirectory() || (process.getuid &&
      (parent.uid !== process.getuid() || (parent.mode & 0o077) !== 0))) {
    throw new Error("invalid private snapshot directory");
  }
  if ((await lstat(path)).isSymbolicLink()) throw new Error("private snapshot is a symlink");
  const file = await open(path, flags | (constants.O_NOFOLLOW ?? 0));
  try {
    const stat = await file.stat();
    if (!stat.isFile() || (process.getuid &&
        (stat.uid !== process.getuid() || (stat.mode & 0o077) !== 0))) {
      throw new Error("invalid private snapshot file");
    }
    return file;
  } catch (error) {
    await file.close();
    throw error;
  }
}

export async function readPrivateJsonSnapshot(value: unknown): Promise<unknown> {
  try {
    const ref = requireJsonObject(value, "private snapshot reference");
    const path = requireNonEmptyString(ref.path, "snapshot path");
    const digest = requireNonEmptyString(ref.sha256, "snapshot sha256");
    const size = ref.byte_count;
    if (!/^[a-f0-9]{64}$/.test(digest) || typeof size !== "number" ||
        !Number.isSafeInteger(size) || size <= 0 || size > MAX_LOCAL_SNAPSHOT_BYTES) {
      throw new Error("invalid private snapshot reference");
    }
    const file = await privateFile(path, constants.O_RDONLY);
    try {
      if ((await file.stat()).size !== size) throw new Error("snapshot size mismatch");
      const bytes = await file.readFile();
      if (bytes.length !== size || createHash("sha256").update(bytes).digest("hex") !== digest) {
        throw new Error("snapshot digest mismatch");
      }
      return JSON.parse(bytes.toString("utf8"));
    } finally {
      await file.close();
    }
  } catch {
    throw new EffectRuntimeRequestError("private snapshot unavailable or invalid; regenerate from the source");
  }
}

export async function openPrivateResponseSink(path: unknown): Promise<FileHandle> {
  try {
    return await privateFile(requireNonEmptyString(path, "response sink"), constants.O_WRONLY);
  } catch {
    throw new EffectRuntimeRequestError("private response sink unavailable or invalid");
  }
}

export async function writePrivateResponse(
  file: FileHandle, encoded: Buffer,
): Promise<{byte_count: number; sha256: string}> {
  if (encoded.length > MAX_LOCAL_SNAPSHOT_BYTES) {
    // The handler may already have committed. The caller must recover its
    // exact receipt rather than treating this as a safe request rejection.
    throw new Error("private response exceeds the local snapshot budget");
  }
  await file.truncate(0);
  await file.writeFile(encoded);
  await file.sync();
  return {byte_count: encoded.length, sha256: createHash("sha256").update(encoded).digest("hex")};
}
