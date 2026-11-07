import assert from "node:assert/strict";
import test from "node:test";

import {
  EffectRuntimeConflictError,
  EffectRuntimeLockTimeoutError,
  EffectRuntimeRequestError,
  effectRuntimeErrorPayload,
} from "../../loopx/control_plane/effect_runtime_errors.ts";

test("runtime failures reduce to stable public-safe taxonomy", () => {
  assert.deepEqual(
    effectRuntimeErrorPayload(new EffectRuntimeRequestError("schema mismatch")),
    {
      kind: "request_rejected",
      code: "invalid_request",
      message: "schema mismatch",
    },
  );
  assert.deepEqual(
    effectRuntimeErrorPayload(
      new EffectRuntimeConflictError("compare-and-swap failed"),
    ),
    {
      kind: "conflict",
      code: "state_conflict",
      message: "compare-and-swap failed",
    },
  );
  assert.equal(
    effectRuntimeErrorPayload(new EffectRuntimeLockTimeoutError()).kind,
    "lock_timeout",
  );
});

test("filesystem and unexpected failures do not expose private diagnostics", () => {
  const permanent = new Error("ENOTDIR: private path");
  Object.assign(permanent, { code: "ENOTDIR" });
  assert.deepEqual(effectRuntimeErrorPayload(permanent), {
    kind: "io_permanent",
    code: "io_not_directory",
    message: "Effect runtime filesystem operation failed",
  });

  const transient = new Error("EAGAIN: private path");
  Object.assign(transient, { code: "EAGAIN" });
  assert.equal(effectRuntimeErrorPayload(transient).kind, "io_transient");

  const genericIo = new Error("EIO: private path");
  Object.assign(genericIo, { code: "EIO", syscall: "write" });
  assert.deepEqual(effectRuntimeErrorPayload(genericIo), {
    kind: "io_permanent",
    code: "io_failure",
    message: "Effect runtime filesystem operation failed",
  });

  assert.deepEqual(effectRuntimeErrorPayload(new TypeError("private stack")), {
    kind: "internal_failure",
    code: "unexpected_handler_error",
    message: "Effect runtime handler failed unexpectedly",
  });
});

test("explicit request details preserve the legacy message within separate transport bounds", () => {
  const payload = effectRuntimeErrorPayload(new EffectRuntimeRequestError("first failure", "invalid_request",
    ["second\n failure", "x".repeat(300)]));
  assert.deepEqual(payload, {kind: "request_rejected", code: "invalid_request", message: "first failure",
    details: ["second failure", "x".repeat(240)]});
  const crowded = effectRuntimeErrorPayload(new EffectRuntimeRequestError("first", "invalid_request", Array(129).fill("detail")));
  assert.equal(crowded.details?.length, 128);
  assert.equal(crowded.details?.at(-1), "2 additional validation details omitted");
  // Only an explicitly typed request error can carry these details.
  const unexpected = Object.assign(new Error("hidden"), {details: ["hidden detail"]});
  assert.equal(effectRuntimeErrorPayload(unexpected).details, undefined);
});
