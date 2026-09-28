import { createHash, randomUUID } from "node:crypto";
import { mkdir, open, readFile, rename, rm, stat } from "node:fs/promises";
import { dirname } from "node:path";

import type { JsonObject } from "./effect_program.ts";
import { EffectRuntimeLockTimeoutError } from "./effect_runtime_errors.ts";

const MUTATION_LOCK_TIMEOUT_MS = 5_000;
const MUTATION_LOCK_POLL_MS = 25;
const INVALID_LOCK_STALE_MS = 10_000;
export const MUTATION_LOCK_TOKEN_MAX_LENGTH = 256;
const INVALID_LOCK_CLAIM_TOKEN = "__invalid_lock_reclaim__";

export interface MutationLockOwner {
  pid: number;
  token: string;
}

interface CreatedFileIdentity {
  dev: number;
  ino: number;
  birthtimeMs: number;
  ctimeMs: number;
}

function sameFileIdentity(
  left: CreatedFileIdentity | null,
  right: CreatedFileIdentity | null,
): boolean {
  if (left === null || right === null) return false;
  const leftHasDeviceIdentity = left.dev !== 0 || left.ino !== 0;
  const rightHasDeviceIdentity = right.dev !== 0 || right.ino !== 0;
  if (leftHasDeviceIdentity !== rightHasDeviceIdentity) return false;
  if (!leftHasDeviceIdentity) {
    // Some Windows providers expose zero device/inode values.  A non-zero
    // creation marker is still useful; two all-zero identities are not.
    const leftMarker = left.birthtimeMs || left.ctimeMs;
    const rightMarker = right.birthtimeMs || right.ctimeMs;
    return leftMarker !== 0 && rightMarker !== 0 && leftMarker === rightMarker;
  }
  if (left.dev !== right.dev || left.ino !== right.ino) return false;
  // When both sides expose a creation marker, use it to guard against rapid
  // inode reuse.  If only one side exposes it, fail closed.
  if (left.birthtimeMs !== 0 || right.birthtimeMs !== 0) {
    return left.birthtimeMs !== 0 && right.birthtimeMs !== 0 &&
      left.birthtimeMs === right.birthtimeMs;
  }
  return true;
}

async function readFileIdentity(path: string): Promise<CreatedFileIdentity | null> {
  try {
    return createdFileIdentity(await stat(path));
  } catch {
    return null;
  }
}

async function publishedMutationLockMatches(
  lockPath: string,
  identity: CreatedFileIdentity,
  ownerPid: number,
  token: string,
): Promise<boolean> {
  if (!sameFileIdentity(identity, await readFileIdentity(lockPath))) return false;
  const owner = await readMutationLockOwner(lockPath);
  if (owner?.pid !== ownerPid || owner.token !== token) return false;
  return sameFileIdentity(identity, await readFileIdentity(lockPath));
}

function processIsAlive(pid: number): boolean {
  if (!Number.isSafeInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
}

async function readMutationLockOwner(path: string): Promise<MutationLockOwner | null> {
  try {
    const payload: unknown = JSON.parse(await readFile(path, "utf8"));
    if (
      typeof payload === "object" &&
      payload !== null &&
      !Array.isArray(payload) &&
      typeof (payload as Record<string, unknown>).pid === "number" &&
      Number.isSafeInteger((payload as Record<string, unknown>).pid) &&
      typeof (payload as Record<string, unknown>).token === "string" &&
      validMutationLockToken((payload as Record<string, unknown>).token)
    ) {
      return {
        pid: (payload as Record<string, unknown>).pid as number,
        token: (payload as Record<string, unknown>).token as string,
      };
    }
  } catch {
    // A partially written or concurrently released lock is retried below.
  }
  return null;
}

function validMutationLockToken(value: unknown): value is string {
  return typeof value === "string" &&
    value.length > 0 &&
    value.length <= MUTATION_LOCK_TOKEN_MAX_LENGTH &&
    value.trim().length > 0;
}

function createdFileIdentity(stats: {
  dev: number;
  ino: number;
  birthtimeMs: number;
  ctimeMs: number;
}): CreatedFileIdentity {
  return {
    dev: stats.dev,
    ino: stats.ino,
    birthtimeMs: stats.birthtimeMs,
    ctimeMs: stats.ctimeMs,
  };
}

async function removeCreatedFile(
  path: string,
  identity: CreatedFileIdentity | null,
): Promise<void> {
  if (!identity) return;
  try {
    const current = await stat(path);
    if (sameFileIdentity(identity, createdFileIdentity(current))) {
      await rm(path, { force: true });
    }
  } catch {
    // The path was already retired or replaced; never remove an unknown file.
  }
}

function mutationLockClaimPath(targetPath: string, token: string): string {
  const lockPath = `${targetPath}.ts-effect.lock`;
  const tokenDigest = createHash("sha256").update(token, "utf8").digest("hex");
  return `${lockPath}.claim.${tokenDigest}`;
}

export async function mutationLockOwner(
  targetPath: string,
): Promise<MutationLockOwner | null> {
  return readMutationLockOwner(`${targetPath}.ts-effect.lock`);
}

export interface FileMutationLockClaim {
  claimPath: string;
  token: string;
  /** Identity of the claim inode created by this caller. */
  identity: CreatedFileIdentity;
}

async function removeDeadMutationLockClaim(path: string): Promise<boolean> {
  const identity = await readFileIdentity(path);
  if (!identity) return false;
  const owner = await readMutationLockOwner(path);
  if (owner && processIsAlive(owner.pid)) return false;
  if (!owner) {
    try {
      if (Date.now() - (await stat(path)).mtimeMs < INVALID_LOCK_STALE_MS) {
        return false;
      }
    } catch {
      return false;
    }
  }
  // Re-read the identity after the owner/staleness check.  A later claim may
  // have reused the same pathname while we were inspecting the old one.
  if (!sameFileIdentity(identity, await readFileIdentity(path))) return false;
  try {
    await rm(path, { force: true });
    return true;
  } catch {
    return false;
  }
}

export async function claimFileMutationLock(
  targetPath: string,
  token: string,
): Promise<FileMutationLockClaim | null> {
  if (!validMutationLockToken(token)) return null;
  const claimPath = mutationLockClaimPath(targetPath, token);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const handle = await open(claimPath, "wx", 0o600);
      let identity: CreatedFileIdentity | null = null;
      try {
        try {
          identity = createdFileIdentity(await handle.stat());
          await handle.writeFile(
            JSON.stringify({ pid: process.pid, token }),
            "utf8",
          );
          await handle.sync();
        } finally {
          await handle.close();
        }
      } catch (error) {
        await removeCreatedFile(claimPath, identity);
        throw error;
      }
      if (!identity) {
        // The claim was opened and published, so a missing identity indicates
        // an impossible internal state.  Fail closed rather than returning a
        // claim that cannot be cleaned up safely.
        throw new Error("mutation lock claim identity was not captured");
      }
      return { claimPath, token, identity };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      if (!(await removeDeadMutationLockClaim(claimPath))) return null;
    }
  }
  return null;
}

export async function releaseFileMutationLockClaim(
  claim: FileMutationLockClaim,
): Promise<void> {
  const owner = await readMutationLockOwner(claim.claimPath);
  if (owner?.pid === process.pid && owner.token === claim.token) {
    // Even the apparently normal owner match is followed by an inode check;
    // the pathname can be replaced between the JSON read and unlink.
    await removeCreatedFile(claim.claimPath, claim.identity);
    return;
  }
  // The claim may have been partially published, replaced, or corrupted while
  // the lock owner changed.  Remove only the inode this caller created; never
  // unlink a later claim that reused the same path.
  await removeCreatedFile(claim.claimPath, claim.identity);
}

async function reclaimStaleMutationLock(path: string): Promise<boolean> {
  const identity = await readFileIdentity(path);
  if (!identity) return false;
  const owner = await readMutationLockOwner(path);
  if (owner && processIsAlive(owner.pid)) return false;
  if (!owner) {
    try {
      if (Date.now() - (await stat(path)).mtimeMs < INVALID_LOCK_STALE_MS) return false;
    } catch {
      return false;
    }
  }
  const targetPath = path.slice(0, -".ts-effect.lock".length);
  const claim = await claimFileMutationLock(
    targetPath,
    owner?.token ?? INVALID_LOCK_CLAIM_TOKEN,
  );
  if (!claim) return false;
  const stalePath = `${path}.stale.${randomUUID()}`;
  try {
    const current = await readMutationLockOwner(path);
    if (owner && (!current || current.token !== owner.token)) return false;
    if (current && processIsAlive(current.pid)) return false;
    if (!current) {
      try {
        if (Date.now() - (await stat(path)).mtimeMs < INVALID_LOCK_STALE_MS) {
          return false;
        }
      } catch {
        return false;
      }
    }
    // The lock pathname is not a compare-and-swap primitive.  Holding the
    // token claim serializes compliant writers; the identity check additionally
    // prevents a replacement inode from being retired after a stale read.
    if (!sameFileIdentity(identity, await readFileIdentity(path))) return false;
    await rename(path, stalePath);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    return false;
  } finally {
    if (claim) {
      try {
        await releaseFileMutationLockClaim(claim);
      } catch {
        // Claim cleanup is best effort; token and PID keep it reclaimable.
      }
    }
  }
  await rm(stalePath, { force: true });
  return true;
}

export interface FileMutationLock {
  targetPath: string;
  lockPath: string;
  token: string;
}

export async function acquireFileMutationLock(
  targetPath: string,
  ownerPid = process.pid,
  timeoutMs = MUTATION_LOCK_TIMEOUT_MS,
): Promise<FileMutationLock> {
  if (!Number.isSafeInteger(ownerPid) || ownerPid <= 0) {
    throw new TypeError("mutation lock owner PID must be a positive safe integer");
  }
  if (!Number.isFinite(timeoutMs) || timeoutMs < 0) {
    throw new TypeError("mutation lock timeout must be a non-negative number");
  }
  await mkdir(dirname(targetPath), { recursive: true, mode: 0o700 });
  const lockPath = `${targetPath}.ts-effect.lock`;
  const token = randomUUID();
  const deadline = Date.now() + timeoutMs;
  let retriedAfterReclaim = false;
  while (true) {
    try {
      const handle = await open(lockPath, "wx", 0o600);
      let identity: CreatedFileIdentity | null = null;
      try {
        try {
          identity = createdFileIdentity(await handle.stat());
          await handle.writeFile(
            JSON.stringify({
              pid: ownerPid,
              token,
            }),
            "utf8",
          );
          // The lock coordinates live processes only. Persisting it across a
          // system crash adds latency and can only leave stale coordination
          // state; close still publishes the owner bytes before work begins.
        } finally {
          await handle.close();
        }
      } catch (error) {
        await removeCreatedFile(lockPath, identity);
        throw error;
      }
      if (!identity) {
        throw new Error("mutation lock identity was not captured");
      }
      if (
        !(await publishedMutationLockMatches(
          lockPath,
          identity,
          ownerPid,
          token,
        ))
      ) {
        await removeCreatedFile(lockPath, identity);
        if (Date.now() >= deadline) {
          throw new EffectRuntimeLockTimeoutError();
        }
        continue;
      }
      return { targetPath, lockPath, token };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      const reclaimed = await reclaimStaleMutationLock(lockPath);
      // Removing a dead owner is progress, not waiting for a live owner. Allow
      // one immediate acquisition even for a zero-wait caller. Bound the retry
      // so repeated replacement cannot extend that caller's lock budget.
      if (reclaimed && !retriedAfterReclaim) {
        retriedAfterReclaim = true;
        continue;
      }
      if (Date.now() >= deadline) {
        throw new EffectRuntimeLockTimeoutError();
      }
      await new Promise((resolve) =>
        setTimeout(
          resolve,
          Math.min(MUTATION_LOCK_POLL_MS, Math.max(0, deadline - Date.now())),
        )
      );
    }
  }
}

export async function releaseFileMutationLock(
  targetPath: string,
  token: string,
  existingClaim: FileMutationLockClaim | null = null,
  suppressErrors = false,
): Promise<boolean> {
  let claim: FileMutationLockClaim | null = existingClaim;
  try {
    const lockPath = `${targetPath}.ts-effect.lock`;
    if (!validMutationLockToken(token)) return false;
    const lockIdentity = await readFileIdentity(lockPath);
    if (!lockIdentity) return false;
    const owner = await readMutationLockOwner(lockPath);
    if (owner?.token !== token) return false;
    claim = existingClaim ?? await claimFileMutationLock(targetPath, token);
    if (!claim || claim.token !== token) return false;
    const retiredPath = `${lockPath}.released.${randomUUID()}`;
    try {
      const current = await readMutationLockOwner(lockPath);
      if (current?.token !== token) return false;
      if (!sameFileIdentity(lockIdentity, await readFileIdentity(lockPath))) return false;
      try {
        await rename(lockPath, retiredPath);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
        throw error;
      }
      await rm(retiredPath, { force: true });
      return true;
    } finally {
      await rm(retiredPath, { force: true });
    }
  } catch (error) {
    if (suppressErrors) return false;
    throw error;
  } finally {
    if (claim) {
      try {
        await releaseFileMutationLockClaim(claim);
      } catch {
        // Claim cleanup is best effort.  In particular, never replace the
        // original lock/operation error with a cleanup failure.
      }
    }
  }
}

export async function withFileMutationLock<T>(
  targetPath: string,
  operation: () => Promise<T>,
  timeoutMs = MUTATION_LOCK_TIMEOUT_MS,
): Promise<T> {
  const lock = await acquireFileMutationLock(targetPath, process.pid, timeoutMs);
  try {
    return await operation();
  } finally {
    // The operation's durable result (or its original error) is authoritative;
    // best-effort lock cleanup must never replace it with a secondary failure.
    await releaseFileMutationLock(lock.targetPath, lock.token, null, true);
  }
}

async function atomicWriteTextFile(
  path: string,
  content: string,
): Promise<void> {
  const directory = dirname(path);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const temporary = `${path}.${process.pid}.${randomUUID()}.tmp`;
  const handle = await open(temporary, "wx", 0o600);
  try {
    try {
      await handle.writeFile(content, "utf8");
      await handle.sync();
    } finally {
      await handle.close();
    }
    await rename(temporary, path);
    if (process.platform !== "win32") {
      const directoryHandle = await open(directory, "r");
      try {
        await directoryHandle.sync();
      } finally {
        await directoryHandle.close();
      }
    }
  } finally {
    await rm(temporary, { force: true });
  }
}

export async function atomicWriteText(
  path: string,
  content: string,
): Promise<void> {
  await atomicWriteTextFile(path, content);
}

export async function atomicWriteJson(
  path: string,
  payload: JsonObject,
): Promise<void> {
  await atomicWriteTextFile(path, `${JSON.stringify(payload, null, 2)}\n`);
}

export async function appendJsonLine(
  path: string,
  payload: JsonObject,
): Promise<void> {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const handle = await open(path, "a", 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(payload)}\n`, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function syncDirectoryForDurableWrite(directory: string): Promise<void> {
  if (process.platform === "win32") return;
  const handle = await open(directory, "r");
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}

/**
 * Write JSON so that a crash cannot leave a torn or unlinked file behind:
 * temp file in the same directory, fsync, atomic rename, directory fsync.
 */
export async function durableWriteJson(
  path: string,
  payload: JsonObject,
): Promise<void> {
  const directory = dirname(path);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const temporary = `${path}.${process.pid}.${randomUUID()}.tmp`;
  const handle = await open(temporary, "wx", 0o600);
  try {
    try {
      await handle.writeFile(`${JSON.stringify(payload, null, 1)}\n`, "utf8");
      await handle.sync();
    } finally {
      await handle.close();
    }
    await rename(temporary, path);
    await syncDirectoryForDurableWrite(directory);
  } finally {
    await rm(temporary, { force: true });
  }
}
