/**
 * Typed errors the adapter surfaces instead of guessing.
 *
 * Calli's web layer renders thrown errors via String(e); each class here keeps
 * a stable `code` so the M1 workbench can branch on it (e.g. show the
 * "LoopX service needs updating" banner on SchemaVersionError).
 *
 * NOTE: no constructor parameter properties — the suite runs on Node's
 * strip-only TypeScript mode.
 */

/** The payload's contract version is below the minimum the service/adapter requires. */
export class SchemaVersionError extends Error {
  readonly code = 'loopx_schema_version';
  readonly supported: number;
  readonly observed: number;
  constructor(message: string, supported: number, observed: number) {
    super(message);
    this.name = 'SchemaVersionError';
    this.supported = supported;
    this.observed = observed;
  }
}

/**
 * The payload failed its zod contract: the SHAPE does not match. This is a
 * format problem, never reported as a version incompatibility (see
 * SchemaVersionError for that). `issues` is zod's flattened list.
 */
export class ContractFormatError extends Error {
  readonly code = 'loopx_format';
  readonly endpoint: string;
  readonly issues: string[];
  constructor(message: string, endpoint: string, issues: string[]) {
    super(message);
    this.name = 'ContractFormatError';
    this.endpoint = endpoint;
    this.issues = issues;
  }
}

/** A LoopX service could not be reached in time. */
export class SourceUnavailableError extends Error {
  readonly code = 'loopx_unreachable';
  readonly endpoint: string;
  constructor(message: string, endpoint: string) {
    super(message);
    this.name = 'SourceUnavailableError';
    this.endpoint = endpoint;
  }
}

/**
 * A Calli write path was invoked. The M1 adapter is strictly read-only:
 * every mutating Api method is stubbed to throw this. It is never swallowed
 * silently anywhere in the adapter.
 */
export class ReadOnlyError extends Error {
  readonly code = 'loopx_read_only';
  readonly method: string;
  constructor(method: string) {
    super(`LoopX adapter is read-only in M1: ${method}() has no LoopX write endpoint`);
    this.name = 'ReadOnlyError';
    this.method = method;
  }
}
