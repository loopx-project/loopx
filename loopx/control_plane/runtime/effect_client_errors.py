"""Typed error envelopes shared by the Effect runtime transports."""

from __future__ import annotations

from collections.abc import Mapping

from .node_probe import HOST_PERMISSION_RECOMMENDATION


class EffectRuntimeRemoteError(RuntimeError):
    """A typed exception returned by the managed TypeScript runtime."""

    def __init__(
        self,
        message: str,
        *,
        error_kind: str,
        diagnostic_code: str,
    ) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.diagnostic_code = diagnostic_code


class EffectRuntimeRejected(EffectRuntimeRemoteError):
    """A typed request reached the runtime but failed semantic validation."""

    def __init__(
        self,
        message: str,
        *,
        diagnostic_code: str = "invalid_request",
        details: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            message,
            error_kind="request_rejected",
            diagnostic_code=diagnostic_code,
        )
        self.details = details


class EffectRuntimeConflict(EffectRuntimeRemoteError):
    """The request conflicted with newer persisted state."""

    def __init__(self, message: str, *, diagnostic_code: str) -> None:
        super().__init__(
            message,
            error_kind="conflict",
            diagnostic_code=diagnostic_code,
        )


class EffectRuntimeIOError(EffectRuntimeRemoteError):
    """A managed runtime filesystem operation failed."""

    def __init__(
        self,
        message: str,
        *,
        error_kind: str,
        diagnostic_code: str,
        transient: bool,
    ) -> None:
        super().__init__(
            message,
            error_kind=error_kind,
            diagnostic_code=diagnostic_code,
        )
        self.transient = transient


class EffectRuntimeTransientIOError(EffectRuntimeIOError):
    """A retryable managed runtime filesystem operation failed."""

    def __init__(self, message: str, *, diagnostic_code: str) -> None:
        super().__init__(
            message,
            error_kind="io_transient",
            diagnostic_code=diagnostic_code,
            transient=True,
        )


class EffectRuntimePermanentIOError(EffectRuntimeIOError):
    """A non-retryable managed runtime filesystem operation failed."""

    def __init__(self, message: str, *, diagnostic_code: str) -> None:
        super().__init__(
            message,
            error_kind="io_permanent",
            diagnostic_code=diagnostic_code,
            transient=False,
        )


class EffectRuntimeLockTimeout(EffectRuntimeRemoteError):
    """A managed mutation lock could not be acquired before its deadline."""

    def __init__(self, message: str, *, diagnostic_code: str) -> None:
        super().__init__(
            message,
            error_kind="lock_timeout",
            diagnostic_code=diagnostic_code,
        )


class EffectRuntimeInternalError(EffectRuntimeRemoteError):
    """The managed handler failed outside a declared request or I/O error."""

    def __init__(self, message: str, *, diagnostic_code: str) -> None:
        super().__init__(
            message,
            error_kind="internal_failure",
            diagnostic_code=diagnostic_code,
        )


class EffectRuntimeStartupError(RuntimeError):
    """The managed runtime could not reach a request-serving state."""

    def __init__(self, message: str, *, diagnostic_code: str) -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code


class EffectRuntimeHostPermissionError(EffectRuntimeStartupError):
    """The host denied local runtime access before any request was dispatched."""

    recommended_action = HOST_PERMISSION_RECOMMENDATION

    def __init__(self) -> None:
        super().__init__(
            "Host permission denied access to the local TypeScript Effect runtime "
            "before request dispatch; no capability operation was executed",
            diagnostic_code="runtime_host_permission_denied",
        )


class EffectRuntimeNodeProbeError(EffectRuntimeStartupError):
    """A pre-dispatch toolchain observation requires caller recovery."""


class EffectRuntimeResponseAmbiguous(EffectRuntimeStartupError):
    """The request may have executed even though its response was lost."""

    def __init__(self, method: str, *, timeout: float) -> None:
        super().__init__(
            f"TypeScript Effect runtime returned no verifiable response for {method} "
            f"(request budget {timeout:g}s); the operation may have committed. Read its exact "
            "durable receipt before any retry",
            diagnostic_code="runtime_response_ambiguous",
        )


def _remote_runtime_error(value: object) -> EffectRuntimeRemoteError:
    if not isinstance(value, Mapping):
        return EffectRuntimeInternalError(
            "TypeScript Effect runtime returned an invalid error envelope",
            diagnostic_code="invalid_error_envelope",
        )
    kind = value.get("kind")
    code = value.get("code")
    message = value.get("message")
    if (
        not isinstance(kind, str)
        or not kind
        or not isinstance(code, str)
        or not code
    ):
        return EffectRuntimeInternalError(
            "TypeScript Effect runtime returned an invalid error envelope",
            diagnostic_code="invalid_error_envelope",
        )
    rendered = " ".join(
        str(message or "TypeScript Effect runtime request failed").split()
    )
    rendered = rendered[:240] or "TypeScript Effect runtime request failed"
    if kind == "request_rejected":
        details = value.get("details", [])
        if (not isinstance(details, list) or len(details) > 128
                or any(not isinstance(detail, str) or len(detail) > 240 for detail in details)):
            return EffectRuntimeInternalError(
                "TypeScript Effect runtime returned invalid request validation details",
                diagnostic_code="invalid_error_envelope",
            )
        return EffectRuntimeRejected(rendered, diagnostic_code=code, details=tuple(details))
    if kind == "conflict":
        return EffectRuntimeConflict(rendered, diagnostic_code=code)
    if kind == "io_transient":
        return EffectRuntimeTransientIOError(rendered, diagnostic_code=code)
    if kind == "io_permanent":
        return EffectRuntimePermanentIOError(rendered, diagnostic_code=code)
    if kind == "lock_timeout":
        return EffectRuntimeLockTimeout(rendered, diagnostic_code=code)
    if kind == "internal_failure":
        return EffectRuntimeInternalError(rendered, diagnostic_code=code)
    return EffectRuntimeInternalError(
        "TypeScript Effect runtime returned an unsupported error kind",
        diagnostic_code="unsupported_error_kind",
    )
