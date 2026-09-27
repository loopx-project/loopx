import type {JsonObject} from "../effect_program.ts";
import {
  AuthorityStoreProtocolError,
  canonicalAuthorityObject,
  canonicalAuthoritySha256,
  requireAuthorityStoreId,
} from "../coordination/authority_store_codec.ts";
import {normalizeTodoAgent} from "../coordination/todo_agents.ts";
import {normalizeTodoCompletionValidationDeclaration} from "./completion_validation_declaration.ts";

export const COMPLETION_VALIDATION_REVISION_SCHEMA =
  "loopx_todo_completion_validation_revision_v0";
export const COMPLETION_VALIDATION_BINDING_SCHEMA =
  "loopx_todo_completion_validation_revision_v1";
export const COMPLETION_VALIDATION_REVISION_RECEIPT_SCHEMA =
  "loopx_todo_completion_validation_revision_receipt_v0";
export const COMPLETION_VALIDATION_BINDING_RECEIPT_SCHEMA =
  "loopx_todo_completion_validation_revision_receipt_v1";

export interface CompletionValidationRevision extends JsonObject {
  readonly schema_version: typeof COMPLETION_VALIDATION_REVISION_SCHEMA |
    typeof COMPLETION_VALIDATION_BINDING_SCHEMA;
  readonly expected_declaration_sha256: string | null;
  readonly declaration: JsonObject;
}

const digest = (value: unknown, label: string): string => {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/u.test(value)) {
    throw new AuthorityStoreProtocolError(`${label} must be a SHA-256 digest`);
  }
  return value;
};

/** The exact absent/default markers are retained, not a fabricated old digest.
 * A missing digest beside a required validator is corruption, not first binding. */
function unboundValidationAuthority(todo: JsonObject): JsonObject {
  if (Object.hasOwn(todo, "completion_validation_sha256") ||
      (Object.hasOwn(todo, "completion_validation_required") &&
        todo.completion_validation_required !== false) ||
      (Object.hasOwn(todo, "completion_validation_revision") &&
        todo.completion_validation_revision !== 0) ||
      (Object.hasOwn(todo, "completion_validation_revision_history") &&
        (!Array.isArray(todo.completion_validation_revision_history) ||
          todo.completion_validation_revision_history.length !== 0))) {
    throw new AuthorityStoreProtocolError(
      "first binding requires absent completion validation authority",
    );
  }
  return Object.fromEntries(Object.entries(todo).filter(([field]) => [
    "completion_validation_required", "completion_validation_revision",
    "completion_validation_revision_history",
  ].includes(field)));
}

export const completionValidationRevisionHistory = (
  value: unknown,
  priorRevision: number,
  currentDigest: string | null,
): JsonObject[] => {
  if (value === undefined && priorRevision === 0) return [];
  if (!Array.isArray(value)) {
    throw new AuthorityStoreProtocolError(
      "Todo completion_validation_revision_history must be an array",
    );
  }
  const history = value.map((entry, index) => {
    const receipt = canonicalAuthorityObject(
      entry,
      `completion validation revision history[${index}]`,
    );
    const firstBinding = receipt.schema_version ===
      COMPLETION_VALIDATION_BINDING_RECEIPT_SCHEMA;
    const fields = [
      "schema_version",
      "revision",
      "operation_id",
      "previous_declaration_sha256",
      "declaration_sha256",
      "actor_agent_id",
      "revised_at",
      ...(firstBinding ? ["previous_validation_authority"] : []),
    ];
    if (Object.keys(receipt).some((field) => !fields.includes(field)) ||
        (!firstBinding && receipt.schema_version !==
          COMPLETION_VALIDATION_REVISION_RECEIPT_SCHEMA) ||
        (firstBinding && (index !== 0 || receipt.previous_declaration_sha256 !== null)) ||
        !Number.isSafeInteger(receipt.revision) || Number(receipt.revision) < 1 ||
        receipt.revision !== index + 1 ||
        typeof receipt.revised_at !== "string" || receipt.revised_at.length === 0) {
      throw new AuthorityStoreProtocolError(
        "Todo completion validation revision history is not canonical",
      );
    }
    let previousAuthority: JsonObject | undefined;
    if (firstBinding) {
      previousAuthority = canonicalAuthorityObject(
        receipt.previous_validation_authority, "previous validation authority",
      );
      if (Object.keys(previousAuthority).some(field => ![
        "completion_validation_required", "completion_validation_revision",
        "completion_validation_revision_history",
      ].includes(field))) {
        throw new AuthorityStoreProtocolError("previous validation authority has unsupported fields");
      }
      unboundValidationAuthority(previousAuthority);
    }
    return {
      schema_version: receipt.schema_version,
      revision: Number(receipt.revision),
      operation_id: requireAuthorityStoreId(
        receipt.operation_id,
        "revision history operation id",
      ),
      previous_declaration_sha256: firstBinding ? null : digest(
        receipt.previous_declaration_sha256,
        "revision history previous declaration",
      ),
      declaration_sha256: digest(
        receipt.declaration_sha256,
        "revision history declaration",
      ),
      actor_agent_id: normalizeTodoAgent(
        receipt.actor_agent_id,
        "revision history actor_agent_id",
      ),
      revised_at: receipt.revised_at,
      ...(previousAuthority === undefined ? {} : {previous_validation_authority: previousAuthority}),
    };
  });
  const last = history.at(-1);
  if (history.length !== priorRevision ||
      history.some((entry, index) => index > 0 &&
        entry.previous_declaration_sha256 !== history[index - 1]!.declaration_sha256) ||
      (last !== undefined && last.declaration_sha256 !== currentDigest)) {
    throw new AuthorityStoreProtocolError(
      "Todo completion validation revision history does not match current state",
    );
  }
  return history;
};

export function decodeCompletionValidationRevision(
  value: unknown,
): CompletionValidationRevision {
  const revision = canonicalAuthorityObject(
    value,
    "completion validation revision",
  );
  const unexpected = Object.keys(revision).filter(
    (field) => ![
      "schema_version",
      "expected_declaration_sha256",
      "declaration",
    ].includes(field),
  );
  if (unexpected.length > 0 ||
      (revision.schema_version !== COMPLETION_VALIDATION_REVISION_SCHEMA &&
        revision.schema_version !== COMPLETION_VALIDATION_BINDING_SCHEMA)) {
    throw new AuthorityStoreProtocolError(
      "completion validation revision has unsupported fields or schema",
    );
  }
  const declaration = normalizeTodoCompletionValidationDeclaration(
    canonicalAuthorityObject(
      revision.declaration,
      "completion validation declaration",
    ),
    {
      strict_fields: true,
      require_command: true,
      require_canonical_input: true,
    },
  );
  if (!declaration.ok) {
    throw new AuthorityStoreProtocolError(declaration.summary);
  }
  const firstBinding = revision.schema_version === COMPLETION_VALIDATION_BINDING_SCHEMA;
  if (firstBinding && revision.expected_declaration_sha256 !== null) {
    throw new AuthorityStoreProtocolError("first binding must explicitly expect an absent declaration");
  }
  return {
    schema_version: firstBinding ? COMPLETION_VALIDATION_BINDING_SCHEMA : COMPLETION_VALIDATION_REVISION_SCHEMA,
    expected_declaration_sha256: firstBinding ? null : digest(
      revision.expected_declaration_sha256,
      "expected_declaration_sha256",
    ),
    declaration: declaration.value,
  };
}

export function planCompletionValidationRevision(args: {
  readonly todo: JsonObject;
  readonly revision: CompletionValidationRevision;
  readonly actor_agent_id: string | null;
  readonly operation_id: string;
  readonly revised_at: string;
}): {updates: JsonObject; receipt: JsonObject} {
  if (args.todo.status !== "open" || args.todo.done === true ||
      args.todo.archive_state !== "active") {
    throw new AuthorityStoreProtocolError(
      "completion validation can be revised only while the Todo is open and active",
    );
  }
  const firstBinding = args.revision.schema_version === COMPLETION_VALIDATION_BINDING_SCHEMA;
  if (firstBinding !== (args.revision.expected_declaration_sha256 === null)) {
    throw new AuthorityStoreProtocolError("validator binding schema and absence witness must agree");
  }
  const previousAuthority = firstBinding ? unboundValidationAuthority(args.todo) : undefined;
  if (!firstBinding && args.todo.completion_validation_required !== true) {
    throw new AuthorityStoreProtocolError(
      "Todo has no completion validation declaration to revise",
    );
  }
  const previousDigest = firstBinding ? null : digest(
    args.todo.completion_validation_sha256,
    "Todo completion_validation_sha256",
  );
  if (previousDigest !== args.revision.expected_declaration_sha256) {
    throw new AuthorityStoreProtocolError(
      "completion validation declaration changed; reread before retrying",
    );
  }
  const nextDigest = canonicalAuthoritySha256(args.revision.declaration);
  if (nextDigest === previousDigest) {
    throw new AuthorityStoreProtocolError(
      "replacement completion validation declaration must change its digest",
    );
  }
  const actor = args.actor_agent_id === null
    ? null
    : normalizeTodoAgent(args.actor_agent_id, "actor_agent_id");
  if (actor === null) {
    throw new AuthorityStoreProtocolError(
      "completion validation revision requires a registered actor",
    );
  }
  const priorRevision = args.todo.completion_validation_revision;
  if (priorRevision !== undefined &&
      (!Number.isSafeInteger(priorRevision) || Number(priorRevision) < 0)) {
    throw new AuthorityStoreProtocolError(
      "Todo completion_validation_revision must be a non-negative safe integer",
    );
  }
  const currentRevision = Number(priorRevision ?? 0);
  const history = completionValidationRevisionHistory(
    args.todo.completion_validation_revision_history,
    currentRevision,
    previousDigest,
  );
  const revision = currentRevision + 1;
  const receipt = {
    schema_version: firstBinding ? COMPLETION_VALIDATION_BINDING_RECEIPT_SCHEMA :
      COMPLETION_VALIDATION_REVISION_RECEIPT_SCHEMA,
    revision,
    operation_id: requireAuthorityStoreId(args.operation_id, "operation id"),
    previous_declaration_sha256: previousDigest,
    declaration_sha256: nextDigest,
    actor_agent_id: actor,
    revised_at: args.revised_at,
    ...(previousAuthority === undefined ? {} : {previous_validation_authority: previousAuthority}),
  };
  return {
    updates: {
      completion_validation_required: true,
      completion_validation_sha256: nextDigest,
      completion_validation_revision: revision,
      completion_validation_revision_history: [...history, receipt],
    },
    receipt,
  };
}
