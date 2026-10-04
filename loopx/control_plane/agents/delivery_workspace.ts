import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString,
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
  requireStringLiteral,
} from "../runtime_decode.ts";
import {
  DELIVERY_WORKSPACE_SNAPSHOT_LEGACY_SNAPSHOT_SCHEMA,
  DELIVERY_WORKSPACE_SNAPSHOT_REQUEST_SCHEMA,
  DELIVERY_WORKSPACE_SNAPSHOT_RESULT_SCHEMA,
  DELIVERY_WORKSPACE_SNAPSHOT_SNAPSHOT_SCHEMA,
} from "../coordination/coordination_state_contract.generated.ts";

export const DELIVERY_WORKSPACE_SCHEMA_VERSION =
  DELIVERY_WORKSPACE_SNAPSHOT_SNAPSHOT_SCHEMA;
export const LEGACY_DELIVERY_WORKSPACE_SCHEMA_VERSION =
  DELIVERY_WORKSPACE_SNAPSHOT_LEGACY_SNAPSHOT_SCHEMA;
export const DELIVERY_WORKSPACE_REQUEST_SCHEMA =
  DELIVERY_WORKSPACE_SNAPSHOT_REQUEST_SCHEMA;
export const DELIVERY_WORKSPACE_RESULT_SCHEMA =
  DELIVERY_WORKSPACE_SNAPSHOT_RESULT_SCHEMA;

export const DELIVERY_WORKSPACE_IDENTITY_KINDS = [
  "git_repository",
  "local_goal",
] as const;
export type DeliveryWorkspaceIdentityKind =
  (typeof DELIVERY_WORKSPACE_IDENTITY_KINDS)[number];

export const DELIVERY_WORKSPACE_KINDS = [
  "canonical_checkout",
  "independent_git_worktree",
  "local_goal_workspace",
] as const;
export type DeliveryWorkspaceKind =
  (typeof DELIVERY_WORKSPACE_KINDS)[number];

export interface DeliveryWorkspaceSnapshot extends JsonObject {
  schema_version: typeof DELIVERY_WORKSPACE_SCHEMA_VERSION;
  workspace_identity: string;
  identity_kind: DeliveryWorkspaceIdentityKind;
  task_repository: string | null;
  workspace_revision_digest?: string;
  repository_source: string;
  workspace_kind: DeliveryWorkspaceKind;
  peer_independent_worktree_required: boolean;
}

type DeliveryWorkspaceOperation = "build" | "normalize" | "isolation";

const GIT_IDENTITY_PATTERN =
  /^git:[a-z0-9.-]+(?::[0-9]{1,5})?\/[A-Za-z0-9._~+/-]+$/i;
const LOCAL_GOAL_IDENTITY_PATTERN =
  /^loopx:[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
const GIT_REVISION_DIGEST_PATTERN = /^[0-9a-f]{64}$/i;

function operation(value: unknown): DeliveryWorkspaceOperation {
  return requireStringLiteral(
    value,
    ["build", "normalize", "isolation"] as const,
    "delivery workspace operation",
    "delivery workspace operation is unsupported",
  );
}

function requestObject(value: unknown): JsonObject {
  const request = requireJsonObject(value, "delivery workspace request");
  if (request.schema_version !== DELIVERY_WORKSPACE_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("delivery workspace request schema mismatch");
  }
  return request;
}

function canonicalGitIdentity(value: unknown, label: string): string | null {
  const identity = optionalNonEmptyString(value, label);
  if (!identity || !GIT_IDENTITY_PATTERN.test(identity)) return null;
  const suffix = identity.slice("git:".length);
  const slash = suffix.indexOf("/");
  if (slash <= 0) return null;
  const host = suffix.slice(0, slash).toLowerCase();
  let path = suffix.slice(slash + 1).replace(/\/{2,}/g, "/");
  if (path.endsWith(".git")) path = path.slice(0, -4);
  if (
    !path || path.split("/").some((segment) => segment === "." || segment === "..")
  ) return null;
  return `git:${host}/${path}`;
}

function localGoalIdentity(value: unknown, label: string): string | null {
  const identity = optionalNonEmptyString(value, label);
  return identity && LOCAL_GOAL_IDENTITY_PATTERN.test(identity) ? identity : null;
}

function snapshot(
  workspaceIdentity: string,
  identityKind: DeliveryWorkspaceIdentityKind,
  workspaceRevisionDigest: string | null,
  repositorySource: string,
  workspaceKind: DeliveryWorkspaceKind,
  peerIndependentWorktreeRequired: boolean,
): DeliveryWorkspaceSnapshot | null {
  if (identityKind === "git_repository") {
    const taskRepository = canonicalGitIdentity(
      workspaceIdentity,
      "workspace_identity",
    );
    if (
      !taskRepository ||
      (workspaceRevisionDigest !== null &&
        !GIT_REVISION_DIGEST_PATTERN.test(workspaceRevisionDigest)) ||
      (workspaceKind !== "canonical_checkout" &&
        workspaceKind !== "independent_git_worktree")
    ) return null;
    return {
      schema_version: DELIVERY_WORKSPACE_SCHEMA_VERSION,
      workspace_identity: taskRepository,
      identity_kind: identityKind,
      task_repository: taskRepository,
      ...(workspaceRevisionDigest === null
        ? {}
        : { workspace_revision_digest: workspaceRevisionDigest.toLowerCase() }),
      repository_source: repositorySource,
      workspace_kind: workspaceKind,
      peer_independent_worktree_required: peerIndependentWorktreeRequired,
    };
  }

  const localIdentity = localGoalIdentity(workspaceIdentity, "workspace_identity");
  if (
    !localIdentity || workspaceRevisionDigest !== null ||
    workspaceKind !== "local_goal_workspace" ||
    peerIndependentWorktreeRequired
  ) return null;
  return {
    schema_version: DELIVERY_WORKSPACE_SCHEMA_VERSION,
    workspace_identity: localIdentity,
    identity_kind: identityKind,
    task_repository: null,
    repository_source: repositorySource,
    workspace_kind: workspaceKind,
    peer_independent_worktree_required: false,
  };
}

export function buildDeliveryWorkspaceSnapshot(
  value: unknown,
): DeliveryWorkspaceSnapshot | null {
  const candidate = requireJsonObject(value, "delivery workspace observation");
  const identityKind = requireStringLiteral(
    candidate.identity_kind,
    DELIVERY_WORKSPACE_IDENTITY_KINDS,
    "identity_kind",
  );
  const workspaceKind = requireStringLiteral(
    candidate.workspace_kind,
    DELIVERY_WORKSPACE_KINDS,
    "workspace_kind",
  );
  return snapshot(
    requireNonEmptyString(candidate.workspace_identity, "workspace_identity"),
    identityKind,
    optionalNonEmptyString(
      candidate.workspace_revision_digest,
      "workspace_revision_digest",
    ),
    requireNonEmptyString(candidate.repository_source, "repository_source"),
    workspaceKind,
    requireBoolean(
      candidate.peer_independent_worktree_required,
      "peer_independent_worktree_required",
    ),
  );
}

export function normalizeDeliveryWorkspaceSnapshot(
  value: unknown,
): DeliveryWorkspaceSnapshot | null {
  if (value === null || value === undefined) return null;
  const candidate = requireJsonObject(value, "delivery workspace snapshot");
  const schemaVersion = optionalNonEmptyString(
    candidate.schema_version,
    "schema_version",
  );
  if (schemaVersion === LEGACY_DELIVERY_WORKSPACE_SCHEMA_VERSION) {
    const taskRepository = canonicalGitIdentity(
      candidate.task_repository,
      "task_repository",
    );
    if (!taskRepository) return null;
    const workspaceKind = requireStringLiteral(
      candidate.workspace_kind,
      ["canonical_checkout", "independent_git_worktree"] as const,
      "workspace_kind",
    );
    return snapshot(
      taskRepository,
      "git_repository",
      null,
      requireNonEmptyString(candidate.repository_source, "repository_source"),
      workspaceKind,
      requireBoolean(
        candidate.peer_independent_worktree_required,
        "peer_independent_worktree_required",
      ),
    );
  }
  if (schemaVersion !== DELIVERY_WORKSPACE_SCHEMA_VERSION) return null;

  const identityKind = requireStringLiteral(
    candidate.identity_kind,
    DELIVERY_WORKSPACE_IDENTITY_KINDS,
    "identity_kind",
  );
  const workspaceKind = requireStringLiteral(
    candidate.workspace_kind,
    DELIVERY_WORKSPACE_KINDS,
    "workspace_kind",
  );
  const normalized = snapshot(
    requireNonEmptyString(candidate.workspace_identity, "workspace_identity"),
    identityKind,
    optionalNonEmptyString(
      candidate.workspace_revision_digest,
      "workspace_revision_digest",
    ),
    requireNonEmptyString(candidate.repository_source, "repository_source"),
    workspaceKind,
    requireBoolean(
      candidate.peer_independent_worktree_required,
      "peer_independent_worktree_required",
    ),
  );
  if (!normalized) return null;
  const declaredRepository = optionalNonEmptyString(
    candidate.task_repository,
    "task_repository",
  );
  if (
    declaredRepository !== null &&
    canonicalGitIdentity(declaredRepository, "task_repository") !==
      normalized.task_repository
  ) return null;
  return normalized;
}

export function evaluateDeliveryWorkspace(value: unknown): JsonObject {
  const request = requestObject(value);
  const selectedOperation = operation(request.operation);
  if (selectedOperation === "isolation") {
    const workspace = normalizeDeliveryWorkspaceSnapshot(request.workspace);
    const multiAgent = requireBoolean(request.multi_agent_goal, "multi_agent_goal");
    const explicit = request.explicit_peer_worktree_requirement == null
      ? null
      : requireBoolean(request.explicit_peer_worktree_requirement, "explicit_peer_worktree_requirement");
    const repository = optionalNonEmptyString(request.task_repository, "task_repository");
    if (repository !== null && canonicalGitIdentity(repository, "task_repository") === null) {
      throw new EffectRuntimeRequestError("task_repository must identify a Git repository");
    }
    // A local Goal receipt proves the registered non-Git workspace. It is a
    // delivery, so keep independent Todo acceptance; do not relabel it as
    // non-delivery just because there is no Git worktree to isolate.
    const required = multiAgent && explicit !== false && (
      explicit === true || repository !== null || workspace?.identity_kind !== "local_goal"
    );
    return {
      schema_version: DELIVERY_WORKSPACE_RESULT_SCHEMA,
      peer_independent_worktree_required: required,
      workspace: workspace === null || (
        repository !== null && workspace.task_repository !== canonicalGitIdentity(repository, "task_repository")
      ) ? null : snapshot(
        workspace.workspace_identity, workspace.identity_kind,
        workspace.workspace_revision_digest ?? null, workspace.repository_source,
        workspace.workspace_kind, required,
      ),
    };
  }
  return {
    schema_version: DELIVERY_WORKSPACE_RESULT_SCHEMA,
    workspace: selectedOperation === "build"
      ? buildDeliveryWorkspaceSnapshot(request.observation)
      : normalizeDeliveryWorkspaceSnapshot(request.workspace),
  };
}
