/** Local Git data capture for the existing planned-transfer owner. Historical
 * Task workspace identities locate data only; they grant no execution. */
import {execFile} from "node:child_process";
import {readFile, stat} from "node:fs/promises";
import {createHash} from "node:crypto";
import {isAbsolute, relative, resolve, sep} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {canonicalAuthorityBytes} from "../coordination/authority_store_codec.ts";
import {indexCoordinationProjection} from "../coordination/coordination_projection.ts";
import {normalizeTodoRepository} from "../todos/work_requirements.ts";
import {observeLeaseWorktree, sameLeaseWorkspace} from "../work_items/task_lease_workspace.ts";
import {evaluateTaskLeaseWriteScopesOverlap} from "../work_items/task_lease_acquire_decision.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {inspectTransferFile, requirePhysicalTransferPath} from "./planned_transfer_container.ts";

interface RepositoryEntry extends JsonObject {path: string; mode: string; object: string}
interface WorkingFile extends JsonObject {path: string; mode: string; source_path: string; material_path: string; sha256: string; bytes: number}
export interface TransferRepository extends JsonObject {
  origin: string; repository: string; commit: string; branch: string; source_workspace: string;
  index: RepositoryEntry[]; tracked_paths: string[]; files: WorkingFile[];
}
const EXECUTION_MATERIAL_DIRECTORY = "agent-workspace/";
export const executionWorkspaceScopes = (path: string) => path !== ".git" && !path.startsWith(".git/");
function reject(message: string): never {throw new EffectRuntimeConflictError(message);}
function same(a: unknown, b: unknown): boolean {return canonicalAuthorityBytes(a).equals(canonicalAuthorityBytes(b));}
function beneath(root: string, path: string): boolean {
  const suffix = relative(root, path);
  return suffix === "" || (!isAbsolute(suffix) && suffix !== ".." && !suffix.startsWith(`..${sep}`));
}
function filePath(path: string): string {
  if (!path || isAbsolute(path) || path.includes("\0") || path.split(sep).some(part =>
    !part || [".", "..", ".git", ".loopx"].includes(part))) reject("Repository material contains reserved or redirected control paths");
  return path;
}

/** Fixed local IO; never inherit alternate Git roots, templates, helpers,
 * hooks, global filters or transport configuration. Stdin is data, not shell. */
export async function gitAt(path: string, args: readonly string[], input?: string): Promise<string> {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")));
  return new Promise((accept, refuse) => {
    const child = execFile("git", ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
      "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-c", "credential.helper=", "-C", path, ...args],
    {env: {...env, GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: "/dev/null", GIT_TERMINAL_PROMPT: "0"},
      timeout: 30_000, maxBuffer: 4 * 1024 * 1024}, (error, stdout) => error ? refuse(error) : accept(stdout));
    child.stdin?.on("error", refuse);
    child.stdin?.end(input);
  });
}
async function repositoryIdentity(root: string, repository: string) {
  await requirePhysicalTransferPath(root);
  if ((await gitAt(root, ["rev-parse", "--show-toplevel"])).trim() !== root) reject("Transfer needs the original repository root");
  const origin = (await gitAt(root, ["remote", "get-url", "origin"])).trim();
  const scp = /^git@([^/:]+):(.+)$/u.exec(origin);
  let url: URL;
  try {url = new URL(scp ? `ssh://${scp[1]}/${scp[2]}` : origin);} catch {reject("Source repository origin is unavailable");}
  if (!["https:", "http:", "ssh:", "git:"].includes(url.protocol) || !url.hostname || url.password || url.search || url.hash ||
      (url.username && !(url.protocol === "ssh:" && url.username === "git")) || normalizeTodoRepository(origin) !== repository) {
    reject("Source origin differs from the original Task repository or needs credential rebinding");
  }
  return {origin, repository, commit: (await gitAt(root, ["rev-parse", "--verify", "HEAD^{commit}"])).trim()};
}
function entries(raw: string, index: boolean): RepositoryEntry[] {
  return raw.split("\0").filter(Boolean).map(line => {
    const match = (index ? /^(100644|100755) ([a-f0-9]{40}|[a-f0-9]{64}) 0\t([\s\S]+)$/u
      : /^(100644|100755) blob ([a-f0-9]{40}|[a-f0-9]{64})\t([\s\S]+)$/u).exec(line);
    if (!match) reject("Repository links, submodules or conflicted index require their owning transfer profile");
    return {mode: match[1]!, object: match[2]!, path: filePath(match[3]!)};
  });
}

/** Only the original canonical Task leases select source data locations. A
 * caller-supplied path cannot nominate another checkout with a similar name. */
export async function locateGoalTransferAgentWorkspace(original: JsonObject, projection: JsonObject) {
  const index = indexCoordinationProjection(projection, String(original.id));
  const repositories = new Set<string>(), bindings: JsonObject[] = [];
  for (const [id, todo] of index.todos) {
    if (todo.done === true || todo.archive_state === "archive" || !todo.task_repository) continue;
    const repository = normalizeTodoRepository(todo.task_repository);
    if (!repository) reject("Original Task repository is unavailable");
    repositories.add(repository);
    const lease = index.leases.get(id);
    if (!lease?.write_workspace) reject("Original code Task needs its source workspace mapping before preparation");
    bindings.push({identity: requireJsonObject(lease.write_workspace, "original Task workspace"), scopes: lease.write_scopes});
  }
  if (!repositories.size) return null;
  if (repositories.size !== 1) reject("Multiple original repositories require their owning workspace mapping before preparation");
  const project = String(original.repo), repository = [...repositories][0]!;
  const paths = (await gitAt(project, ["worktree", "list", "--porcelain", "-z"]))
    .split("\0").filter(field => field.startsWith("worktree ")).map(field => field.slice(9));
  const matched = new Set<string>();
  for (const binding of bindings) {
    const workspace = requireJsonObject(binding.identity, "original workspace identity");
    let actual: string | null = null;
    for (const path of paths) {
      try {const info = await stat(path);
        if (createHash("sha256").update(`${info.dev}:${info.ino}`).digest("hex") === workspace.worktree) actual = path;
      } catch (error) {if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;}
    }
    if (!actual || !sameLeaseWorkspace(await observeLeaseWorktree(actual, () => false), workspace)) {
      reject("Original Agent workspace is unavailable; do not replace it with the canonical checkout");
    }
    await repositoryIdentity(actual, repository);
    matched.add(actual);
  }
  if (matched.size !== 1) reject("Distinct original Agent workspaces require their owning mapping before preparation");
  return {path: [...matched][0]!, repository, scopes: bindings.flatMap(binding => binding.scopes as string[])};
}

/** A transport namespace separates canonical Goal inputs from different actual
 * Agent bytes. It is a material mapping, never a new runtime/authority root. */
export function goalTransferMaterialPath(project: string, workspace: string | null, path: string): string {
  if (beneath(project, path)) return filePath(relative(project, path));
  if (workspace && beneath(workspace, path)) return EXECUTION_MATERIAL_DIRECTORY + filePath(relative(workspace, path));
  reject("Transfer material must belong to the original Goal or its recorded Task workspace");
}
export function goalTransferMaterialSource(project: string, repository: JsonObject, row: JsonObject): string {
  return row.task_workspace_relative_path !== undefined
    ? resolve(String(repository.source_workspace), filePath(String(row.task_workspace_relative_path)))
    : resolve(project, filePath(String(row.source_relative_path)));
}

export async function planGoalTransferRepository(original: JsonObject, projection: JsonObject,
  selectedPaths: readonly string[]): Promise<TransferRepository | null> {
  const binding = await locateGoalTransferAgentWorkspace(original, projection);
  if (!binding) return null;
  const project = String(original.repo), root = binding.path;
  const canonical = await repositoryIdentity(project, binding.repository);
  // Keep the prior canonical-checkout hold; the actual Agent's branch/index
  // and dirty tree below are now captured rather than silently substituted.
  for (const entry of entries(await gitAt(project, ["ls-tree", "-rz", "--full-tree", canonical.commit]), false)) {
    const path = await requirePhysicalTransferPath(resolve(project, entry.path)), info = await stat(path), bytes = await readFile(path);
    const object = createHash(entry.object.length === 40 ? "sha1" : "sha256").update(`blob ${bytes.length}\0`).update(bytes).digest("hex");
    if (!info.isFile() || info.nlink !== 1 || object !== entry.object || ((info.mode & 0o111) !== 0) !== (entry.mode === "100755")) reject("Tracked canonical repository changed before preparation");
  }
  if ((await gitAt(project, ["diff-index", "--cached", "--no-ext-diff", "--no-textconv", "--name-only", canonical.commit, "--"])).trim()) reject("Canonical index changed before preparation");
  const identity = await repositoryIdentity(root, binding.repository);
  const branch = (await gitAt(root, ["rev-parse", "--symbolic-full-name", "HEAD"])).trim();
  const rawIndex = await gitAt(root, ["ls-files", "--stage", "-z"]), index = entries(rawIndex, true);
  const flags = (await gitAt(root, ["ls-files", "-v", "-z"])).split("\0").filter(Boolean);
  if (flags.some(row => !row.startsWith("H ")) ||
      await gitAt(root, ["diff", "--cached", "--raw", "--ita-visible-in-index"]) !== await gitAt(root, ["diff", "--cached", "--raw", "--ita-invisible-in-index"])) {
    reject("Sparse, assumed-unchanged or intent-to-add index needs its owning transfer profile");
  }
  const tracked = new Set([...entries(await gitAt(root, ["ls-tree", "-rz", "--full-tree", identity.commit]), false), ...index].map(row => row.path));
  const selected = new Set(selectedPaths);
  const ordinary = new Set((await gitAt(root, ["ls-files", "--others", "--exclude-standard", "-z"])).split("\0").filter(Boolean));
  const paths = new Set(tracked);
  for (const path of (await gitAt(root, ["ls-files", "--others", "-z"])).split("\0").filter(Boolean)) {
    if (ordinary.has(path) || selected.has(resolve(root, path)) || selected.has(resolve(project, path)) ||
        evaluateTaskLeaseWriteScopesOverlap({left: binding.scopes, right: [path]}).overlap === true) paths.add(filePath(path));
  }
  const files: WorkingFile[] = [];
  for (const path of [...paths].sort()) {
    const source = await requirePhysicalTransferPath(resolve(root, filePath(path)));
    let info;
    try {info = await stat(source);} catch (error) {if ((error as NodeJS.ErrnoException).code === "ENOENT" && tracked.has(path)) continue; throw error;}
    if (!info.isFile() || info.nlink !== 1) reject("Original working input must be a regular, unlinked file");
    const facts = await inspectTransferFile(source);
    let input = source;
    const canonicalPath = resolve(project, path);
    try {if (same(await inspectTransferFile(await requirePhysicalTransferPath(canonicalPath)), facts)) input = canonicalPath;}
    catch (error) {if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;}
    files.push({path, mode: (info.mode & 0o111) !== 0 ? "100755" : "100644", source_path: input,
      material_path: goalTransferMaterialPath(project, root, input), ...facts});
  }
  if (!same(identity, await repositoryIdentity(root, binding.repository)) || branch !== (await gitAt(root, ["rev-parse", "--symbolic-full-name", "HEAD"])).trim() ||
      rawIndex !== await gitAt(root, ["ls-files", "--stage", "-z"])) reject("Original Agent repository changed during preparation");
  return {...identity, branch, source_workspace: root, index, tracked_paths: [...tracked].sort(), files};
}

/** Preserve current file/deletion state while keeping the exact staged index.
 * Canonical-only primary documents remain available unless the actual tracked
 * tree explicitly deleted them. Conflicting destinations are refused. */
export function repositoryMaterialMapping(repository: TransferRepository, materials: JsonObject) {
  const selected = new Map<string, JsonObject>();
  for (const row of materials.materials as JsonObject[]) {
    const path = String(row.source_relative_path);
    if (row.task_workspace_relative_path === undefined && !repository.tracked_paths.includes(path)) selected.set(path, row);
  }
  for (const file of repository.files) {
    const row = (materials.materials as JsonObject[]).find(item => item.source_relative_path === file.material_path);
    if (!row || row.sha256 !== file.sha256 || row.bytes !== file.bytes) reject("Original working file is absent from the protected inventory");
    selected.set(file.path, row);
  }
  return [...selected].map(([path, row]) => ({row, path}));
}
