export const EFFECT_RUNTIME_ERROR_KINDS = [
  "request_rejected",
  "conflict",
  "io_transient",
  "io_permanent",
  "lock_timeout",
  "internal_failure",
] as const;

export type EffectRuntimeErrorKind =
  (typeof EFFECT_RUNTIME_ERROR_KINDS)[number];

export interface EffectRuntimeErrorPayload {
  kind: EffectRuntimeErrorKind;
  code: string;
  message: string;
}

abstract class EffectRuntimeBoundaryError extends Error {
  abstract readonly kind: EffectRuntimeErrorKind;
  readonly code: string;

  constructor(message: string, code: string) {
    super(message);
    this.name = new.target.name;
    this.code = code;
  }
}

export class EffectRuntimeRequestError extends EffectRuntimeBoundaryError {
  readonly kind = "request_rejected" as const;

  constructor(message: string, code = "invalid_request") {
    super(message, code);
  }
}

export class EffectRuntimeConflictError extends EffectRuntimeBoundaryError {
  readonly kind = "conflict" as const;

  constructor(message: string, code = "state_conflict") {
    super(message, code);
  }
}

export class EffectRuntimeLockTimeoutError extends EffectRuntimeBoundaryError {
  readonly kind = "lock_timeout" as const;

  constructor(message = "Effect runtime mutation lock timed out") {
    super(message, "mutation_lock_timeout");
  }
}

const TRANSIENT_IO_CODES: Readonly<Record<string, string>> = {
  EAGAIN: "io_temporarily_unavailable",
  EBUSY: "io_busy",
  EMFILE: "io_process_descriptor_limit",
  ENFILE: "io_system_descriptor_limit",
  ETIMEDOUT: "io_timed_out",
};

const PERMANENT_IO_CODES: Readonly<Record<string, string>> = {
  EACCES: "io_permission_denied",
  EDQUOT: "io_quota_exhausted",
  EINVAL: "io_invalid_operation",
  EISDIR: "io_is_directory",
  ENAMETOOLONG: "io_name_too_long",
  ENOENT: "io_not_found",
  ENOSPC: "io_space_exhausted",
  ENOTDIR: "io_not_directory",
  EPERM: "io_permission_denied",
  EROFS: "io_read_only",
};

function boundedMessage(value: string, fallback: string): string {
  const singleLine = value.replace(/\s+/g, " ").trim();
  return (singleLine || fallback).slice(0, 240);
}

function nodeErrorCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null) return null;
  const code = (error as {code?: unknown}).code;
  return typeof code === "string" ? code : null;
}

function isNodeSystemError(error: unknown): boolean {
  return typeof error === "object" &&
    error !== null &&
    typeof (error as {syscall?: unknown}).syscall === "string";
}

export function effectRuntimeErrorPayload(
  error: unknown,
): EffectRuntimeErrorPayload {
  if (error instanceof EffectRuntimeBoundaryError) {
    return {
      kind: error.kind,
      code: error.code,
      message: boundedMessage(error.message, "Effect runtime request failed"),
    };
  }
  const ioCode = nodeErrorCode(error);
  if (ioCode && TRANSIENT_IO_CODES[ioCode]) {
    return {
      kind: "io_transient",
      code: TRANSIENT_IO_CODES[ioCode],
      message: "Effect runtime filesystem operation is temporarily unavailable",
    };
  }
  if (ioCode && PERMANENT_IO_CODES[ioCode]) {
    return {
      kind: "io_permanent",
      code: PERMANENT_IO_CODES[ioCode],
      message: "Effect runtime filesystem operation failed",
    };
  }
  if (ioCode && isNodeSystemError(error)) {
    return {
      kind: "io_permanent",
      code: "io_failure",
      message: "Effect runtime filesystem operation failed",
    };
  }
  return {
    kind: "internal_failure",
    code: "unexpected_handler_error",
    message: "Effect runtime handler failed unexpectedly",
  };
}
