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
  if (typeof source.path !== "string" || !isAbsolute(source.path) ||
      typeof source.sha256 !== "string" || !BARE_SHA256_PATTERN.test(source.sha256)) {
    throw new TypeError("registry_source requires an absolute path and SHA-256 digest");
  }
  // Copy primitive values: later mutation of the decoded request cannot change
  // the witness while an awaited store operation or validation effect runs.
  const path = source.path, digest = source.sha256;
  return async () => {
    if (expectedDigest != null && expectedDigest !== digest) return false;
    try { return createHash("sha256").update(await readFile(path)).digest("hex") === digest; }
    catch (error) {
      if (error instanceof Error && "code" in error && error.code === "ENOENT") return false;
      throw error;
    }
  };
}
