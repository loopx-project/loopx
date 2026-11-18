/** A local registry witness binds projected authority facts to their source.
 * It is not a grant or a transaction spanning registry and provider storage. */
import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import {isAbsolute} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";

export type AuthoritySourceCheck = () => Promise<boolean>;
/** Legacy wires and service-owned callers retain their existing fact contract. */
export const uncheckedAuthoritySource: AuthoritySourceCheck = async () => true;
export const AUTHORITY_SOURCE_CHANGED = {
  code: "authority_source_changed",
  reason: "Todo authority registration changed; review the current state before continuing",
} as const;

function decodeRegistryWitness(value: unknown, field: string): {path: string; sha256: string} {
  const witness = requireJsonObject(value, field);
  if (typeof witness.path !== "string" || !isAbsolute(witness.path) ||
      typeof witness.sha256 !== "string" || !BARE_SHA256_PATTERN.test(witness.sha256)) {
    throw new TypeError(`${field} requires an absolute path and SHA-256 digest`);
  }
  return {path: witness.path, sha256: witness.sha256};
}

async function registryMatches(witness: {path: string; sha256: string}): Promise<boolean> {
  return createHash("sha256").update(await readFile(witness.path)).digest("hex") === witness.sha256;
}

export function requireReviewedRegistrySha256(value: unknown): string {
  if (typeof value !== "string" || !BARE_SHA256_PATTERN.test(value)) {
    throw new TypeError("expected_registry_sha256 must be a SHA-256 digest");
  }
  return value;
}

export function registryAuthoritySourceCheck(
  input: JsonObject, witnessed: boolean, expectedDigest: unknown = null,
): AuthoritySourceCheck {
  if (!witnessed) {
    if (Object.hasOwn(input, "registry_source")) {
      throw new TypeError("registry_source requires the witnessed request version");
    }
    return uncheckedAuthoritySource;
  }
  const source = requireJsonObject(input.registry_source, "registry_source");
  // Copy primitive values: later mutation of the decoded request cannot change
  // the witness while an awaited store operation or validation effect runs.
  const witness = decodeRegistryWitness(source, "registry_source");
  const routing = Object.hasOwn(source, "routing_registry")
    ? decodeRegistryWitness(source.routing_registry, "registry_source.routing_registry")
    : undefined;
  return async () => {
    if (expectedDigest != null && expectedDigest !== witness.sha256) return false;
    try { return await registryMatches(witness) && (routing === undefined || await registryMatches(routing)); }
    catch (error) {
      if (error instanceof Error && "code" in error && error.code === "ENOENT") return false;
      throw error;
    }
  };
}
