import type { JsonObject } from "../effect_program.ts";
import {
  AuthorityStoreProtocolError,
  canonicalAuthorityObject,
} from "./authority_store_codec.ts";
import { COORDINATION_STATE_CONTRACT } from "./coordination_state_contract.generated.ts";

export const COORDINATION_STATE_CONTRACT_SCHEMA =
  "loopx_coordination_state_contract_v0";

if (COORDINATION_STATE_CONTRACT.schema_version !== COORDINATION_STATE_CONTRACT_SCHEMA) {
  throw new AuthorityStoreProtocolError("coordination state contract schema mismatch");
}

interface RecordContract {
  readonly fields: readonly string[];
  readonly required_fields: readonly string[];
}

export { COORDINATION_STATE_CONTRACT };
export const TODO_CANONICAL_READ_RECORD_SCHEMA =
  COORDINATION_STATE_CONTRACT.todo_read_record.schema_version;
export const TODO_ITEM_SCHEMA =
  COORDINATION_STATE_CONTRACT.todo_read_record.item_schema_version;
export const TODO_CANONICAL_READ_RECORD_FIELDS =
  COORDINATION_STATE_CONTRACT.todo_read_record.fields;
export const TODO_CANONICAL_REQUIRED_READ_FIELDS =
  COORDINATION_STATE_CONTRACT.todo_read_record.required_fields;

// The legacy manifest remains an immutable compatibility contract. Native
// provider records have their own version and never require Markdown location.
export const TODO_DOMAIN_READ_RECORD_SCHEMA =
  COORDINATION_STATE_CONTRACT.todo_domain_record.schema_version;
export const TODO_DOMAIN_ITEM_SCHEMA =
  COORDINATION_STATE_CONTRACT.todo_domain_record.item_schema_version;
export const TODO_DOMAIN_RECORD_CONTRACT: RecordContract = Object.freeze({
  fields: Object.freeze(TODO_CANONICAL_READ_RECORD_FIELDS.filter((field) =>
    !(COORDINATION_STATE_CONTRACT.todo_projection_metadata.fields as readonly string[]).includes(field))),
  required_fields: COORDINATION_STATE_CONTRACT.todo_domain_record.required_fields,
});

export interface TodoDomainRecord extends JsonObject {
  schema_version: string;
  todo_id: string;
  role: "user" | "agent";
  status: "open" | "done" | "blocked" | "deferred";
  done: boolean;
  text: string;
  archive_state: "active" | "archive";
}

export interface TodoProjectionMetadata {
  readonly source_section: string;
  readonly index?: number;
}

export function canonicalTodoDomainRecord(value: unknown, label = "Todo domain record"): TodoDomainRecord {
  const record = canonicalCoordinationRecord(value, TODO_DOMAIN_RECORD_CONTRACT, label);
  const terminal = record.status === "done" || record.status === "deferred";
  if (record.schema_version !== TODO_DOMAIN_ITEM_SCHEMA ||
      typeof record.todo_id !== "string" || !record.todo_id ||
      (record.role !== "user" && record.role !== "agent") ||
      typeof record.status !== "string" ||
      !["open", "done", "blocked", "deferred"].includes(String(record.status)) ||
      typeof record.done !== "boolean" || record.done !== terminal ||
      typeof record.text !== "string" ||
      (record.archive_state !== "active" && record.archive_state !== "archive")) {
    throw new AuthorityStoreProtocolError(`${label} has invalid required semantics`);
  }
  return record as TodoDomainRecord;
}

export function canonicalCoordinationRecord(
  value: unknown,
  contract: RecordContract,
  label: string,
): JsonObject {
  const record = canonicalAuthorityObject(value, label);
  const allowed = new Set(contract.fields);
  const unknownRequired = contract.required_fields.filter(
    (field) => !allowed.has(field),
  );
  if (unknownRequired.length > 0) {
    throw new AuthorityStoreProtocolError(
      `${label} required fields are absent from fields: ${unknownRequired.join(", ")}`,
    );
  }
  const unexpected = Object.keys(record)
    .filter((field) => !allowed.has(field))
    .sort((left, right) => left.localeCompare(right));
  if (unexpected.length > 0) {
    throw new AuthorityStoreProtocolError(
      `${label} has unversioned fields: ${unexpected.join(", ")}`,
    );
  }
  const missing = contract.required_fields.filter((field) => !(field in record));
  if (missing.length > 0) {
    throw new AuthorityStoreProtocolError(
      `${label} omits required fields: ${missing.join(", ")}`,
    );
  }
  // Preserve the generic contract's legacy `in` selection for inherited fields.
  // Shipped Todo contracts need no reconstruction: normalization already owns
  // every allowed field and nested container in canonical order.
  if (contract.fields.some((field) => !Object.hasOwn(record, field) && field in record)) {
    return canonicalAuthorityObject(Object.fromEntries(
      contract.fields.flatMap((field) => field in record ? [[field, record[field]]] : []),
    ), label);
  }
  return record;
}

export function canonicalCoordinationTodoRecord(
  value: unknown,
  label = "coordination Todo read record",
): JsonObject {
  const record = canonicalCoordinationRecord(
    value,
    COORDINATION_STATE_CONTRACT.todo_read_record,
    label,
  );
  if (record.schema_version !== TODO_ITEM_SCHEMA ||
      (record.role !== "user" && record.role !== "agent") ||
      typeof record.status !== "string" || record.status.length === 0 ||
      typeof record.done !== "boolean" || typeof record.text !== "string" ||
      typeof record.archive_state !== "string" || record.archive_state.length === 0 ||
      typeof record.source_section !== "string" || record.source_section.length === 0) {
    throw new AuthorityStoreProtocolError(`${label} has invalid required semantics`);
  }
  return record;
}
