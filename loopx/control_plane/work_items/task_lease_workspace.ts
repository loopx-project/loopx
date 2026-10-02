/** Filesystem observations for code-edit leases, never a shared-state fence. */
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {realpath, stat, readFile, lstat, opendir} from "node:fs/promises";
import {platform} from "node:os";
import {createHash} from "node:crypto";
import {isAbsolute, resolve} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {leaseWriteRepository} from "./task_lease_repository.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";

export interface LeaseWorkspace extends JsonObject {
  host: string;
  common_directory: string;
  worktree: string;
  repository: string;
}
const run = promisify(execFile);
const digest = (value: string) => createHash("sha256").update(value).digest("hex");

export function leaseWorkspace(value: unknown): LeaseWorkspace | null {
  if (value == null) return null;
  const row = requireJsonObject(value, "lease worktree identity");
  if (Object.keys(row).sort().join(",") !== "common_directory,host,repository,worktree" ||
      [row.host, row.common_directory, row.worktree].some(v => typeof v !== "string" || !BARE_SHA256_PATTERN.test(v))) {
    throw new EffectRuntimeRequestError("invalid lease worktree identity");
  }
  const repository = leaseWriteRepository(row.repository);
  if (!repository) throw new EffectRuntimeRequestError("lease worktree requires a repository");
  return {...row, repository} as LeaseWorkspace;
}

export function sameLeaseWorkspace(left: unknown, right: unknown): boolean {
  const a = leaseWorkspace(left), b = leaseWorkspace(right);
  return a === null || b === null ? a === b : a.host === b.host &&
    a.common_directory === b.common_directory && a.worktree === b.worktree && a.repository === b.repository;
}

/** Only positively observed sibling worktrees can turn overlap into an advisory. */
export function independentLeaseWorktrees(left: unknown, right: unknown): boolean {
  const a = leaseWorkspace(left), b = leaseWorkspace(right);
  return a !== null && b !== null && a.host === b.host &&
    a.repository.toLowerCase() === b.repository.toLowerCase() &&
    a.common_directory === b.common_directory && a.worktree !== b.worktree;
}

export async function observeLeaseWorktree(path: string, overlaps: (path: string) => boolean): Promise<LeaseWorkspace> {
  if (!isAbsolute(path)) throw new EffectRuntimeRequestError("worktree observation requires an absolute caller path");
  const cwd = await realpath(path);
  const git = async (...args: string[]) => (await run("git", ["-C", cwd, ...args],
    {timeout: 5000, maxBuffer: 4 * 1024 * 1024, env: {...process.env, GIT_OPTIONAL_LOCKS: "0"}})).stdout.trim();
  const root = await realpath(await git("rev-parse", "--show-toplevel"));
  const directory = await realpath(await git("rev-parse", "--absolute-git-dir"));
  const common = await realpath(resolve(cwd, await git("rev-parse", "--git-common-dir")));
  if (root !== cwd || directory === common || !(await stat(resolve(root, ".git"))).isFile()) {
    throw new EffectRuntimeRequestError("--write-worktree requires the root of an independent Git worktree");
  }
  // Code-edit leases must not describe shared Git administration or paths
  // redirected out of the checkout. Unknown/untracked symlinks also fail closed.
  if (overlaps(".git")) throw new EffectRuntimeRequestError("worktree scopes cannot include Git administration");
  // Inspect physical entries, including ignored files. Git's tracked/untracked
  // inventory is not an isolation boundary. Subtree overlap also checks a
  // redirected ancestor when the requested leaf does not exist yet.
  const pending = [""];
  while (pending.length > 0) {
    const parent = pending.pop()!;
    for await (const entry of await opendir(resolve(root, parent))) {
      const relative = parent ? `${parent}/${entry.name}` : entry.name;
      if (!overlaps(relative) && !overlaps(`${relative}/**`)) continue;
      const full = resolve(root, relative);
      try {
        const info = await lstat(full);
        if (info.isSymbolicLink() || !(await realpath(full)).startsWith(`${root}/`)) {
          throw new EffectRuntimeRequestError("worktree scope contains a redirected path");
        }
        if (info.isDirectory()) pending.push(relative);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      }
    }
  }
  const remote = await git("remote", "get-url", "origin");
  const scp = /^[^@/:]+@([^/:]+):(.+)$/u.exec(remote);
  const url = scp ? new URL(`ssh://${scp[1]}/${scp[2]}`) : new URL(remote);
  if (!["ssh:", "https:", "http:"].includes(url.protocol) || !url.hostname || url.search || url.hash) {
    throw new EffectRuntimeRequestError("worktree origin must identify a Git repository");
  }
  const repository = leaseWriteRepository(`git:${url.host.toLowerCase()}/${url.pathname.replace(/^\/+|\/+$/gu, "").replace(/\.git$/u, "")}`)!;
  // Private local paths are not persisted. Filesystem inode identity collapses
  // symlink/case aliases; a recreated directory gets a new identity.
  const key = async (p: string) => {const s = await stat(p); return digest(`${s.dev}:${s.ino}`);};
  const machine = platform() === "darwin"
    ? /"IOPlatformUUID"\s*=\s*"([^"]+)"/u.exec((await run("ioreg", ["-rd1", "-c", "IOPlatformExpertDevice"], {timeout: 5000})).stdout)?.[1]
    : platform() === "linux" ? (await readFile("/etc/machine-id", "utf8")).trim() : null;
  if (!machine) throw new EffectRuntimeRequestError("host identity unavailable for worktree isolation");
  return {host: digest(machine), common_directory: await key(common), worktree: await key(root), repository};
}
