/** Reviewed material placement for the stopped Goal transfer. This owns only
 * selected workspace files, never Goal admission or authority. Credential policy
 * remains with its owner; selected bytes are not privacy-certified here. */
import {constants} from "node:fs";
import {copyFile, mkdir, open, readFile, chmod, stat} from "node:fs/promises";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {createHash} from "node:crypto";
import {dirname, isAbsolute, join, relative, resolve, sep} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {durableWriteJson} from "../effect_runtime_io.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys} from "../coordination/authority_store_codec.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {inspectTransferFile, requirePhysicalTransferPath} from "./planned_transfer_container.ts";
import {decodePlannedTransferMaterial, type ProtectedGoalMaterial} from "./planned_transfer.ts";
import {indexCoordinationProjection} from "../coordination/coordination_projection.ts";
import {normalizeTodoRepository} from "../todos/work_requirements.ts";
import {observeLeaseWorktree, sameLeaseWorkspace} from "../work_items/task_lease_workspace.ts";
import {evaluateTaskLeaseWriteScopesOverlap} from "../work_items/task_lease_acquire_decision.ts";

interface WorkspaceMaterial {source: ProtectedGoalMaterial; destination: string; relativePath: string}
export interface GoalTransferWorkspacePlan {materials: WorkspaceMaterial[]; destinations: JsonObject[]}
type MaterialPresence = "missing" | "matched" | "occupied";
interface MaterialObservation extends JsonObject {id: string; relative_path: string; presence: MaterialPresence; sha256: string | null; bytes: number | null}
interface WorkspaceReview extends JsonObject {preview_sha256: string; ready: boolean; materials: MaterialObservation[]}
function reject(message: string): never { throw new EffectRuntimeConflictError(message); }
function same(a: unknown, b: unknown): boolean { return canonicalAuthorityBytes(a).equals(canonicalAuthorityBytes(b)); }
function beneath(root: string, path: string): boolean {
  const suffix = relative(root, path);
  return suffix === "" || (!isAbsolute(suffix) && suffix !== ".." && !suffix.startsWith(`..${sep}`));
}
async function optionalRecord(path: string): Promise<JsonObject | null> {
  await requirePhysicalTransferPath(path);
  try { return requireJsonObject(JSON.parse(await readFile(path, "utf8")), "original material placement"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
}
async function retainRecord(path: string, value: JsonObject): Promise<void> {
  const prior = await optionalRecord(path);
  if (prior) { if (!same(prior, value)) reject("Material placement belongs to another original operation"); return; }
  await mkdir(dirname(path), {recursive: true, mode: 0o700});
  await durableWriteJson(path, value);
  if (!same(await optionalRecord(path), value)) reject("Material placement receipt readback failed");
}

/** A null mapping keeps original relative names. An explicit mapping must
 * account for every selected member; no basename guessing or silent omission.
 * Reserved control state stays with its existing owner, outside this operation. */
export function planGoalTransferWorkspace(project: string, runtime: string,
  materials: ProtectedGoalMaterial[], raw: unknown): GoalTransferWorkspacePlan {
  if (process.platform === "win32") reject("Workspace transfer requires the local POSIX profile");
  const mapping = raw === null ? Object.fromEntries(materials.map(row => [row.id, row.source_relative_path])) : requireJsonObject(raw, "material destinations");
  if (!hasExactAuthorityKeys(mapping, materials.map(row => row.id))) reject("Review a destination for every selected material");
  const planned = materials.map(source => {
    const destination = mapping[source.id];
    if (typeof destination !== "string" || !destination || destination.includes("\0") || isAbsolute(destination) ||
        destination.split(sep).some(part => part === "" || part === "." || part === "..")) reject("Material destination must be a normalized relative workspace path");
    const path = resolve(project, destination);
    if (!beneath(project, path) || path === project || beneath(runtime, path) ||
        beneath(join(project, ".git"), path) || beneath(join(project, ".loopx"), path)) reject("Material destination overlaps runtime, registry or repository control state");
    return {source, destination: path, relativePath: destination};
  });
  const paths = new Set(planned.map(row => row.destination));
  if (paths.size !== planned.length) reject("Material destinations collide");
  for (const path of paths) {
    for (let parent = dirname(path); parent !== project; parent = dirname(parent)) {
      if (paths.has(parent)) reject("Material destinations contain one another");
    }
  }
  return {materials: planned, destinations: planned.map(row => ({id: row.source.id, relative_path: row.relativePath}))};
}

export async function reviewGoalTransferWorkspace(plan: GoalTransferWorkspacePlan, artifact: string,
  target: JsonObject, witness: JsonObject): Promise<WorkspaceReview> {
  const materials: MaterialObservation[] = [];
  for (const row of plan.materials) {
    await requirePhysicalTransferPath(row.destination);
    let observed: Awaited<ReturnType<typeof inspectTransferFile>> | null = null;
    try { observed = await inspectTransferFile(row.destination); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    const presence: MaterialPresence = !observed ? "missing"
      : observed.sha256 === row.source.sha256 && observed.bytes === row.source.bytes ? "matched" : "occupied";
    materials.push({id: row.source.id, relative_path: row.relativePath, presence,
      sha256: observed?.sha256 ?? null, bytes: observed?.bytes ?? null});
  }
  const facts = {schema_version: "loopx_planned_transfer_workspace_preview_v0", artifact_sha256: artifact,
    target, registry_witness: witness, materials};
  return {...facts, ready: materials.every(row => row.presence !== "occupied"), preview_sha256: canonicalAuthoritySha256(facts)};
}

/** A retained review binds the original operation, not a fresh confirmation on
 * each retry. Publishing a file is preceded by an intent. A missing file after
 * that intent is ambiguous and held, never recreated over a later deletion. */
export async function verifyGoalTransferWorkspaceReview(plan: GoalTransferWorkspacePlan, review: WorkspaceReview,
  directory: string, identity: JsonObject, expected: unknown, completed: boolean): Promise<void> {
  if (typeof expected !== "string" || !BARE_SHA256_PATTERN.test(expected)) reject("Workspace material placement requires its reviewed target preview");
  const original = await optionalRecord(join(directory, "workspace-review.json"));
  const binding = {identity_sha256: canonicalAuthoritySha256(identity), preview_sha256: expected};
  if (original ? !same(original, binding) : review.preview_sha256 !== expected) reject("Destination material preview changed; review the current target before staging");
  for (const [index, row] of plan.materials.entries()) {
    const observed = review.materials[index]!;
    if (observed.presence === "occupied") reject("Destination workspace material changed or is occupied; preserve it");
    const intent = await optionalRecord(join(directory, "workspace-materials", `${row.source.id}.json`));
    if (completed && !intent) reject("Completed workspace material intent is missing; recover the original stage");
    if (intent && !same(intent, materialIntent(row))) reject("Destination material intent differs from the original operation");
    if (intent && observed.presence === "missing") reject("Previously attempted workspace material is missing; recover it without repeating publication");
  }
}
function materialIntent(row: WorkspaceMaterial): JsonObject {
  return {id: row.source.id, destination: row.destination, sha256: row.source.sha256, bytes: row.source.bytes};
}
export async function readGoalTransferWorkspaceReview(directory: string, identity: JsonObject): Promise<string | null> {
  const original = await optionalRecord(join(directory, "workspace-review.json"));
  if (!original) return null;
  if (!hasExactAuthorityKeys(original, ["identity_sha256", "preview_sha256"]) ||
      original.identity_sha256 !== canonicalAuthoritySha256(identity) || !BARE_SHA256_PATTERN.test(String(original.preview_sha256))) reject("Original destination review changed; recover its existing operation");
  return String(original.preview_sha256);
}
export async function retainGoalTransferWorkspace(plan: GoalTransferWorkspacePlan, directory: string,
  identity: JsonObject, expected: string): Promise<void> {
  await retainRecord(join(directory, "workspace-review.json"), {identity_sha256: canonicalAuthoritySha256(identity), preview_sha256: expected});
  for (const row of plan.materials) {
    await requirePhysicalTransferPath(row.destination);
    let observed: Awaited<ReturnType<typeof inspectTransferFile>> | null = null;
    try { observed = await inspectTransferFile(row.destination); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    const path = join(directory, "workspace-materials", `${row.source.id}.json`);
    const intent = await optionalRecord(path);
    if (intent && !same(intent, materialIntent(row))) reject("Destination material intent changed");
    if (intent && !observed) reject("Previously attempted workspace material is missing; preserve the original operation");
    if (observed && (observed.sha256 !== row.source.sha256 || observed.bytes !== row.source.bytes)) reject("Destination workspace material changed; preserve it");
    await retainRecord(path, materialIntent(row));
    if (!observed) {
      const input = await inspectTransferFile(row.source.path);
      if (input.sha256 !== row.source.sha256 || input.bytes !== row.source.bytes) reject("Authenticated workspace material changed");
      await mkdir(dirname(row.destination), {recursive: true, mode: 0o700});
      await requirePhysicalTransferPath(row.destination);
      await copyFile(row.source.path, row.destination, constants.COPYFILE_EXCL);
      const handle = await open(row.destination, constants.O_RDONLY | constants.O_NOFOLLOW);
      try { await handle.sync(); } finally { await handle.close(); }
      const parent = await open(dirname(row.destination), constants.O_RDONLY);
      try { await parent.sync(); } finally { await parent.close(); }
    }
    const final = await inspectTransferFile(row.destination);
    if (final.sha256 !== row.source.sha256 || final.bytes !== row.source.bytes) reject("Destination workspace material readback failed");
  }
}

interface RepositoryFile extends JsonObject {path: string; mode: string; object: string}
interface TransferRepository extends JsonObject {origin: string; repository: string; commit: string; files: RepositoryFile[]}
const runGit = promisify(execFile);
const workspaceScopes = (path: string) => path !== ".git" && !path.startsWith(".git/");

/** Git is only local snapshot IO here. Do not inherit an alternate Git root,
 * templates, credential helpers, hooks, global filters or transport settings. */
async function gitAt(path: string, ...args: string[]): Promise<string> {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")));
  return (await runGit("git", ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
    "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-c", "credential.helper=", "-C", path, ...args],
    {env: {...env, GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: "/dev/null", GIT_TERMINAL_PROMPT: "0"},
      timeout: 30_000, maxBuffer: 4 * 1024 * 1024})).stdout;
}

async function observeRepository(project: string, repository: string): Promise<TransferRepository> {
  const root = await requirePhysicalTransferPath(project);
  try {
    if ((await gitAt(root, "rev-parse", "--show-toplevel")).trim() !== root) reject("Transfer needs the original repository root");
    const origin = (await gitAt(root, "remote", "get-url", "origin")).trim();
    // A source credential/configuration is not a destination credential. Only
    // credential-free ordinary Git origins may enter this local snapshot.
    const scp = /^git@([^/:]+):(.+)$/u.exec(origin);
    const url = new URL(scp ? `ssh://${scp[1]}/${scp[2]}` : origin);
    if (!["https:", "http:", "ssh:", "git:"].includes(url.protocol) || !url.hostname ||
        url.password || url.search || url.hash || (url.username && !(url.protocol === "ssh:" && url.username === "git")) ||
        normalizeTodoRepository(origin) !== repository) reject("Source origin differs from the original Task repository or needs credential rebinding");
    const commit = (await gitAt(root, "rev-parse", "--verify", "HEAD^{commit}")).trim();
    const entries = (await gitAt(root, "ls-tree", "-rz", "--full-tree", commit)).split("\0").filter(Boolean);
    const files: RepositoryFile[] = [];
    for (const entry of entries) {
      const match = /^(100644|100755) blob ([a-f0-9]{40}|[a-f0-9]{64})\t([\s\S]+)$/u.exec(entry);
      if (!match) reject("Repository links/submodules need their owning transfer profile before preparation");
      const mode = match[1]!, object = match[2]!, path = match[3]!;
      const physical = await requirePhysicalTransferPath(resolve(root, path));
      const child = relative(root, physical);
      if (!child || isAbsolute(child) || child === ".." || child.startsWith(`..${sep}`) ||
          child.split(sep).some(part => part === ".git" || part === ".loopx")) reject("Repository snapshot contains reserved or redirected control paths");
      const info = await stat(physical), bytes = await readFile(physical);
      const objectHash = createHash(object.length === 40 ? "sha1" : "sha256")
        .update(`blob ${bytes.length}\0`).update(bytes).digest("hex");
      if (!info.isFile() || info.nlink !== 1 || objectHash !== object ||
          ((info.mode & 0o111) !== 0) !== (mode === "100755")) reject("Tracked repository changes require their source owner before preparation");
      files.push({path, mode, object});
    }
    // Deleted/added tracked entries and an index-only change must not disappear
    // behind a clean-looking worktree or a selected material override.
    if ((await gitAt(root, "diff-index", "--cached", "--no-ext-diff", "--no-textconv", "--name-only", commit, "--")).trim()) reject("Staged repository changes require their source owner before preparation");
    if ((await gitAt(root, "rev-parse", "HEAD")).trim() !== commit) reject("Source repository changed during preparation");
    return {origin, repository, commit, files};
  } catch (error) {
    if (error instanceof EffectRuntimeConflictError) throw error;
    reject("Original repository snapshot is unavailable; preserve the source and resolve its repository owner");
  }
}

/** Code Tasks keep their real repository and tracked dependency closure. This
 * is not a directory sweep: unselected untracked/ignored files stay outside the
 * snapshot. Machine/Git configuration is not adopted; selected bytes are not
 * privacy-certified. */
export async function planGoalTransferRepository(original: JsonObject, projection: JsonObject,
  selectedPaths: readonly string[]): Promise<TransferRepository | null> {
  const repositories = new Set<string>();
  const index = indexCoordinationProjection(projection, String(original.id));
  const workspaces: JsonObject[] = [];
  for (const [id, todo] of index.todos) {
    if (todo.done === true || todo.archive_state === "archive" || !todo.task_repository) continue;
    const repository = normalizeTodoRepository(todo.task_repository);
    if (!repository) reject("Original Task repository is unavailable");
    repositories.add(repository);
    const lease = index.leases.get(id);
    if (!lease?.write_workspace) reject("Original code Task needs its source workspace mapping before preparation");
    workspaces.push({identity: requireJsonObject(lease.write_workspace, "original Task workspace"), scopes: lease.write_scopes});
  }
  if (!repositories.size) return null;
  if (repositories.size !== 1) reject("Multiple original repositories require their owning workspace mapping before preparation");
  const observed = await observeRepository(String(original.repo), [...repositories][0]!);
  // A clean canonical checkout alone does not prove the Agent's unfinished
  // work is carried. Historical lease identities locate its actual checkout
  // for read-only comparison; they grant no current execution permission.
  const paths = (await gitAt(String(original.repo), "worktree", "list", "--porcelain", "-z"))
    .split("\0").filter(field => field.startsWith("worktree ")).map(field => field.slice(9));
  for (const binding of workspaces) {
    const workspace = requireJsonObject(binding.identity, "original workspace identity");
    let actual: string | null = null;
    for (const path of paths) {
      try {
        const info = await stat(path);
        if (createHash("sha256").update(`${info.dev}:${info.ino}`).digest("hex") === workspace.worktree) actual = path;
      } catch (error) {if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;}
    }
    if (!actual || !sameLeaseWorkspace(await observeLeaseWorktree(actual, () => false), workspace)) reject("Original Agent workspace is unavailable; do not replace it with the canonical checkout");
    if (!same(await observeRepository(actual, observed.repository), observed)) reject("Original Agent branch differs from the canonical snapshot; preserve its unfinished work for workspace mapping");
    const ordinary = new Set((await gitAt(actual, "ls-files", "--others", "--exclude-standard", "-z")).split("\0").filter(Boolean));
    for (const path of (await gitAt(actual, "ls-files", "--others", "-z")).split("\0").filter(Boolean)) {
      if (!ordinary.has(path) && !selectedPaths.includes(path) &&
          evaluateTaskLeaseWriteScopesOverlap({left: binding.scopes, right: [path]}).overlap !== true) continue;
      if (!selectedPaths.includes(path) || !same(
          await inspectTransferFile(await requirePhysicalTransferPath(resolve(actual, path))),
          await inspectTransferFile(await requirePhysicalTransferPath(resolve(String(original.repo), path))))) {
        reject("Original Agent has uncarried working files; preserve them for the workspace owner before preparation");
      }
    }
  }
  return observed;
}

/** The existing operation retains a workspace witness, not another admission
 * grant. Before confirmation it includes unchanged input bytes and HEAD; after
 * admission ordinary new writes/commits are legal under the fresh Task lease. */
export async function verifyGoalTransferExecutionWorkspace(record: JsonObject, materials: JsonObject,
  originalFiles: boolean): Promise<void> {
  const path = await requirePhysicalTransferPath(record.path);
  const identity = await observeLeaseWorktree(path, workspaceScopes);
  if (!same(identity, record.identity) || record.materials_sha256 !== canonicalAuthoritySha256(materials)) reject("Original execution workspace identity changed; preserve its operation");
  if (!originalFiles) return;
  const repository = requireJsonObject(record.repository, "original execution repository");
  if ((await gitAt(path, "rev-parse", "HEAD")).trim() !== repository.commit) reject("Reviewed execution repository changed before confirmation");
  const destinations = materials.workspace_destinations as JsonObject[];
  const expectedPaths = new Set(destinations.map(row => row.relative_path));
  if ((await gitAt(path, "ls-files", "--cached", "--others", "-z")).split("\0").filter(Boolean)
      .some(file => !expectedPaths.has(file))) reject("Execution workspace contains unreviewed files before confirmation");
  for (const row of materials.materials as JsonObject[]) {
    const destination = destinations.find(item => item.id === row.id);
    if (!destination) reject("Execution material has no reviewed mapping");
    const physical = await requirePhysicalTransferPath(resolve(path, String(destination.relative_path)));
    if (!same(await inspectTransferFile(physical), {sha256: row.sha256, bytes: row.bytes})) reject("Reviewed execution material changed before confirmation");
  }
  for (const file of repository.files as RepositoryFile[]) {
    if ((((await stat(resolve(path, file.path))).mode & 0o111) !== 0) !== (file.mode === "100755")) reject("Reviewed execution file mode changed before confirmation");
  }
}

export async function readGoalTransferExecutionWorkspace(directory: string, materials: JsonObject): Promise<JsonObject | null> {
  const record = await optionalRecord(join(directory, "execution-workspace.json"));
  if (record) await verifyGoalTransferExecutionWorkspace(record, materials, true);
  return record;
}

/** Build a separate local repository with one pinned source commit/tree, then
 * populate a new independent worktree through the existing material owner.
 * No source hooks/configuration, checkout filters, network fetch or old
 * lease/session is adopted. The protected material inventory carries the actual
 * tracked bytes; cross-machine repository delivery remains separately held. */
export async function prepareGoalTransferExecutionWorkspace(directory: string, original: JsonObject,
  expected: JsonObject, materials: JsonObject, context: JsonObject, witness: JsonObject): Promise<void> {
  const path = join(dirname(String(requireJsonObject(context.goal, "staged Goal").repo)), "agent-workspace"), bare = join(directory, "repository.git");
  const intent = {repository: expected, materials_sha256: canonicalAuthoritySha256(materials), path};
  const previous = await optionalRecord(join(directory, "execution-intent.json"));
  if (previous && !same(previous, intent)) reject("Original execution workspace preparation changed; preserve it");
  if (await optionalRecord(join(directory, "execution-workspace.json"))) {
    await readGoalTransferExecutionWorkspace(directory, materials); return;
  }
  if (previous) reject("Execution workspace preparation was interrupted; preserve the target and use the original source cancellation before a new preparation");
  if (!same(await observeRepository(String(original.repo), String(expected.repository)), expected)) reject("Source repository changed after its initial review");
  for (const file of expected.files as RepositoryFile[]) {
    if (!(materials.materials as JsonObject[]).some(row => row.source_relative_path === file.path)) reject("Tracked dependency is absent from the protected material inventory");
  }
  await retainRecord(join(directory, "execution-intent.json"), intent);
  await mkdir(bare, {mode: 0o700});
  await gitAt(bare, "init", "--bare", "--template=", `--object-format=${String(expected.commit).length === 64 ? "sha256" : "sha1"}`);
  await gitAt(bare, "-c", "protocol.file.allow=always", "fetch", "--no-tags", "--depth=1", "--", String(original.repo), String(expected.commit));
  await gitAt(bare, "remote", "add", "origin", String(expected.origin));
  await gitAt(bare, "worktree", "add", "--no-checkout", "-b", `codex/continue-${canonicalAuthoritySha256(intent).slice(0, 16)}`, path, String(expected.commit));
  await gitAt(path, "read-tree", String(expected.commit));
  const mapping = materials.workspace_destinations as JsonObject[];
  const selected = (materials.materials as JsonObject[]).map((row, index) => ({...decodePlannedTransferMaterial(row, index),
    path: resolve(String(requireJsonObject(context.goal, "staged Goal").repo), String(mapping.find(item => item.id === row.id)!.relative_path))}));
  const plan = planGoalTransferWorkspace(path, String(context.runtime_root), selected,
    Object.fromEntries(mapping.map(row => [String(row.id), row.relative_path])));
  const review = await reviewGoalTransferWorkspace(plan, canonicalAuthoritySha256(materials), context, witness);
  const identity = {repository: expected, materials_sha256: canonicalAuthoritySha256(materials)};
  await verifyGoalTransferWorkspaceReview(plan, review, join(directory, "execution-materials"), identity, review.preview_sha256, false);
  await retainGoalTransferWorkspace(plan, join(directory, "execution-materials"), identity, review.preview_sha256);
  for (const file of expected.files as RepositoryFile[]) await chmod(resolve(path, file.path), file.mode === "100755" ? 0o700 : 0o600);
  if (!same(await observeRepository(String(original.repo), String(expected.repository)), expected)) reject("Source repository changed while preparing its execution workspace");
  const record = {...intent, identity: await observeLeaseWorktree(path, workspaceScopes)};
  await verifyGoalTransferExecutionWorkspace(record, materials, true);
  await retainRecord(join(directory, "execution-workspace.json"), record);
}
