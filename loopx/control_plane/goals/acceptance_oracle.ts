/** Optional provenance for an owner-pinned checker, never an Agent's verdict.
 * The local host executes the configured checker and observes file bytes. This
 * does not authenticate a remote reviewer or attest unpinned dependencies. */
import type {JsonObject} from "../effect_program.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";

export type AcceptanceOracleState = "unverified" | "recorded" | "current" | "stale" | "failed" | "unavailable";

function requireThat(value: unknown, message: string): asserts value {
  if (!value) throw new AuthorityStoreProtocolError(message);
}
function keys(row: JsonObject, fields: string[]): void {
  requireThat(Object.keys(row).length === fields.length && fields.every(key => Object.hasOwn(row, key)), "invalid acceptance oracle fields");
}
export function acceptanceArtifactRef(value: unknown): string {
  requireThat(typeof value === "string" && value.length <= 1024 && /^[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*$/.test(value)
    && value.split("/").every(part => part !== "." && part !== ".."), "oracle files must be safe repository-relative refs");
  return value;
}
function refs(value: unknown, required: boolean): string[] {
  requireThat(Array.isArray(value) && value.length >= (required ? 1 : 0) && value.length <= 20, "oracle needs bounded explicit file refs");
  const result = value.map(acceptanceArtifactRef).sort();
  requireThat(new Set(result).size === result.length, "duplicate oracle file ref");
  return result;
}
export interface AcceptanceOracle extends JsonObject {
  kind: "owner_pinned_oracle";
  source_id: string;
  input_refs: string[];
  output_refs: string[];
}
export function normalizeAcceptanceOracle(value: unknown, criterion: JsonObject): AcceptanceOracle {
  const row = canonicalAuthorityObject(value, "acceptance oracle");
  keys(row, ["kind", "source_id", "input_refs", "output_refs"]);
  requireThat(row.kind === "owner_pinned_oracle" && typeof row.source_id === "string"
    && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(row.source_id), "owner-pinned oracle source identity required");
  const input_refs = refs(row.input_refs, false), output_refs = refs(row.output_refs, true);
  requireThat(!input_refs.some(ref => output_refs.includes(ref)), "oracle input and output roles must be distinct");
  const pins = criterion.validation_files as {path: string; sha256: string}[];
  const argv = criterion.validation_argv as string[];
  requireThat(Array.isArray(pins) && pins.length > 0 && Array.isArray(argv),
    "oracle requires an explicitly pinned checker");
  // This is the supported invocation grammar, not interpreter attestation.
  // Arbitrary commands may ignore argv[1]; only Python's immediate script
  // position has that meaning here. Other runtimes can execute a pinned entry
  // point directly. The interpreter/environment remain trusted host inputs.
  const executableName = argv[0]?.split(/[\\/]/).at(-1) ?? "";
  const hostInterpreterPath = executableName === argv[0] || /^(?:\/|[A-Za-z]:[\\/])/.test(argv[0] ?? "");
  const pythonScript = hostInterpreterPath && /^python(?:3(?:\.\d+)?)?(?:\.exe)?$/.test(executableName)
    && typeof argv[1] === "string" && !argv[1].startsWith("-")
    && pins.some(pin => pin.path === argv[1]);
  const directSource = typeof argv[0] === "string" && argv[0].includes("/")
    && pins.some(pin => pin.path === argv[0]);
  requireThat(directSource || pythonScript,
    "oracle requires a pinned relative executable path containing '/' or a pinned script immediately after a supported Python interpreter; arbitrary commands and interpreter options are unsupported");
  requireThat(!pins.some(pin => input_refs.includes(pin.path) || output_refs.includes(pin.path)),
    "oracle source and checked artifacts must be distinct");
  return {kind: "owner_pinned_oracle", source_id: row.source_id, input_refs, output_refs};
}
export function acceptanceOracleBasis(criterion: JsonObject): string {
  return canonicalAuthoritySha256({oracle: criterion.oracle, validation_argv: criterion.validation_argv,
    validation_files: criterion.validation_files, validation_timeout_seconds: criterion.validation_timeout_seconds});
}
export interface OracleVersion extends JsonObject {ref: string; sha256: string}
export function normalizeOracleVersions(value: unknown): OracleVersion[] {
  requireThat(Array.isArray(value) && value.length <= 20, "bounded oracle versions required");
  const result = value.map(item => {
    const row = canonicalAuthorityObject(item, "oracle artifact version"); keys(row, ["ref", "sha256"]);
    requireThat(typeof row.sha256 === "string" && BARE_SHA256_PATTERN.test(row.sha256), "oracle artifact version requires SHA-256");
    return {ref: acceptanceArtifactRef(row.ref), sha256: row.sha256};
  }).sort((a,b) => a.ref < b.ref ? -1 : a.ref > b.ref ? 1 : 0);
  requireThat(new Set(result.map(row => row.ref)).size === result.length, "duplicate oracle artifact version");
  return result;
}
export function normalizeOracleEvidence(value: unknown): JsonObject {
  const row = canonicalAuthorityObject(value, "oracle execution evidence");
  keys(row, ["source_id", "source_digest", "checked_at", "input_versions", "output_versions"]);
  requireThat(typeof row.source_id === "string" && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(row.source_id)
    && typeof row.source_digest === "string" && BARE_SHA256_PATTERN.test(row.source_digest), "oracle evidence source required");
  requireThat(typeof row.checked_at === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(row.checked_at)
    && Number.isFinite(Date.parse(row.checked_at)), "oracle check time required");
  return {...row, input_versions: normalizeOracleVersions(row.input_versions), output_versions: normalizeOracleVersions(row.output_versions)};
}
export function validateOracleResults(criteria: JsonObject[], results: JsonObject[]): void {
  for (const result of results) {
    const criterion = criteria.find(row => row.id === result.criterion_id);
    if (result.oracle_evidence === undefined) {
      requireThat(result.passed !== true || criterion?.oracle === undefined, "successful oracle check omitted execution evidence");
      continue;
    }
    requireThat(criterion?.oracle !== undefined && result.passed === true, "unconfigured or failed check cannot attest oracle evidence");
    const oracle = criterion.oracle as AcceptanceOracle, evidence = normalizeOracleEvidence(result.oracle_evidence);
    requireThat(evidence.source_id === oracle.source_id && evidence.source_digest === acceptanceOracleBasis(criterion), "oracle evidence source changed");
    for (const role of ["input", "output"]) {
      const versions = evidence[`${role}_versions`] as OracleVersion[];
      requireThat(canonicalAuthoritySha256(versions.map(row => row.ref)) === canonicalAuthoritySha256(oracle[`${role}_refs`]), "oracle evidence omitted or added a checked artifact");
    }
  }
}
/** Raw canonical projections say recorded, never current, without host IO. */
export function projectAcceptanceOracle(criterion: JsonObject, result: JsonObject | undefined, stale: boolean): JsonObject {
  const oracle = criterion.oracle as AcceptanceOracle;
  return {kind: oracle.kind, source_id: oracle.source_id, source_digest: acceptanceOracleBasis(criterion),
    input_refs: oracle.input_refs, output_refs: oracle.output_refs,
    state: stale ? "stale" : !result ? "unverified" : result.passed === false ? "failed" : result.oracle_evidence ? "recorded" : "unavailable"};
}
/** Host observations only; a read never runs checks, certifies a new version or
 * changes canonical acceptance/completion. Unavailable facts are not success. */
export function observeAcceptanceOracles(params: JsonObject): JsonObject {
  const contract = canonicalAuthorityObject(params.contract, "acceptance projection");
  if (contract.enabled !== true) return contract;
  requireThat(Array.isArray(contract.criteria) && Array.isArray(params.observations), "oracle observations required");
  const receipt = contract.verification as JsonObject | null;
  const results = receipt?.results as JsonObject[] | undefined;
  return {...contract, criteria: (contract.criteria as JsonObject[]).map(criterion => {
    if (!criterion.oracle) return criterion;
    const oracle = criterion.oracle as JsonObject;
    if (oracle.state !== "recorded") return criterion;
    const evidence = results?.find(row => row.criterion_id === criterion.id)?.oracle_evidence as JsonObject | undefined;
    const facts = (params.observations as JsonObject[]).find(row => row.criterion_id === criterion.id);
    let state: AcceptanceOracleState = "unavailable";
    if (facts?.available === true && evidence) {
      state = facts.source_current === true && ["input", "output"].every(role =>
        canonicalAuthoritySha256(normalizeOracleVersions(facts[`${role}_versions`])) === canonicalAuthoritySha256(evidence[`${role}_versions`])) ? "current" : "stale";
    }
    return {...criterion, oracle: {...oracle, state}};
  })};
}

export function planAcceptanceOracle(params: JsonObject): JsonObject {
  const criterion = canonicalAuthorityObject(params.criterion, "oracle criterion");
  const oracle = normalizeAcceptanceOracle(criterion.oracle, criterion);
  return {...oracle, source_digest: acceptanceOracleBasis({...criterion, oracle})};
}
