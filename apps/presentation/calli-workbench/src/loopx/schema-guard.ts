/**
 * Gate on status_contract.schema_version BEFORE any mapping runs.
 *
 * Rules (main-agent review):
 *  - observed < minimum_dashboard_schema_version → SchemaVersionError.
 *    This is the ONLY version rejection. There is no upper bound: a newer
 *    schema (e.g. 3) is handed to zod and parsed on its merits.
 *  - zod parse failure at any accepted version → ContractFormatError
 *    (a FORMAT problem, never reported as version incompatibility).
 */
import { ContractFormatError, SchemaVersionError } from './errors.ts';
import { statusPayloadSchema, SUPPORTED_STATUS_SCHEMA_VERSION, type StatusPayload } from './contracts/status-schema.ts';

export { SchemaVersionError };
export { SUPPORTED_STATUS_SCHEMA_VERSION };

function readContract(raw: unknown): { observed: number | null; minimum: number | null } {
  try {
    const contract = (raw as { status_contract?: { schema_version?: unknown; minimum_dashboard_schema_version?: unknown } } | null)?.status_contract;
    return {
      observed: typeof contract?.schema_version === 'number' ? contract.schema_version : null,
      minimum: typeof contract?.minimum_dashboard_schema_version === 'number' ? contract.minimum_dashboard_schema_version : null,
    };
  } catch {
    return { observed: null, minimum: null };
  }
}

export function parseStatusPayload(raw: unknown, endpoint = '/status.json'): StatusPayload {
  const rawContract = readContract(raw);
  const minimum = rawContract.minimum ?? SUPPORTED_STATUS_SCHEMA_VERSION;
  if (rawContract.observed != null && rawContract.observed < minimum) {
    throw new SchemaVersionError(
      `LoopX status contract schema_version ${rawContract.observed} is below the required minimum ${minimum}; update the LoopX service`,
      minimum,
      rawContract.observed,
    );
  }
  const parsed = statusPayloadSchema.safeParse(raw);
  if (!parsed.success) {
    const issues = parsed.error.issues.map((i) => `${i.path.join('.') || 'root'}: ${i.message}`);
    throw new ContractFormatError(
      `LoopX ${endpoint} body does not match the status contract format (${issues.length} issues; first: ${issues[0] ?? 'n/a'}); this is a format error, not a version incompatibility`,
      endpoint,
      issues,
    );
  }
  const payload = parsed.data;
  // A body with no status_contract at all defaults to schema_version 0; reject
  // that as a version problem rather than accepting an unknowable shape.
  const requiredMinimum = payload.status_contract.minimum_dashboard_schema_version;
  if (payload.status_contract.schema_version < requiredMinimum) {
    throw new SchemaVersionError(
      `LoopX status contract schema_version ${payload.status_contract.schema_version} is below the required minimum ${requiredMinimum}; update the LoopX service`,
      requiredMinimum,
      payload.status_contract.schema_version,
    );
  }
  return payload;
}
