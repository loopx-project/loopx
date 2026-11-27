"""Map DeepSeek Harness execution failures to typed LoopX host failure kinds.

Classification follows the merged ``loopx-turn-v0`` precedence: a known
provider ``error.code`` wins over HTTP status and prose; an unknown non-empty
code becomes ``unknown`` and blocks every lower tier; HTTP status decides only
when no more-specific code exists; exception class names and bounded message
matching apply only when no structured signal exists at all. Within one tier,
signals that disagree fold to ``unknown`` regardless of their order.
"""

from __future__ import annotations

from collections.abc import Mapping

_MAX_CAUSE_DEPTH = 4
_MAX_REASON_DEPTH = 3

# Tier 1: structured provider codes. The dsh provider-neutral taxonomy comes
# first; common provider strings keep working. Stable codes decide without
# requiring a duplicated HTTP status on every serialization boundary.
_CODE_KINDS: dict[str, str] = {
    "aborted": "unknown",
    "auth": "auth_failed",
    "authentication_error": "auth_failed",
    "at_capacity": "provider_capacity",
    "context_window_exceeded": "contract_rejected",
    "empty_response": "transport_lost",
    "insufficient_balance": "quota_exhausted",
    "insufficient_capacity": "provider_capacity",
    "insufficient_quota": "quota_exhausted",
    "invalid_api_key": "auth_failed",
    "invalid_credential": "auth_failed",
    "invalid_request": "contract_rejected",
    "llm_stream_idle_timeout": "executor_timeout",
    "malformed_response": "contract_rejected",
    "missing_credential": "auth_failed",
    "model_at_capacity": "provider_capacity",
    "overloaded": "provider_overloaded",
    "overloaded_error": "provider_overloaded",
    "permission_denied": "auth_failed",
    "quota": "quota_exhausted",
    "quota_exceeded": "quota_exhausted",
    "rate_limit": "rate_limited",
    "rate_limit_exceeded": "rate_limited",
    "rate_limited": "rate_limited",
    "request_timeout": "executor_timeout",
    "request_extension": "contract_rejected",
    "server": "provider_overloaded",
    "server_overloaded": "provider_overloaded",
    "stream_closed": "contract_rejected",
    "timeout": "executor_timeout",
    "transport": "transport_lost",
    "unsupported_content": "contract_rejected",
    "unsupported_reasoning_effort": "contract_rejected",
}

# Tier 2: HTTP status, consulted only when no code decided the failure.
_STATUS_KINDS = {
    401: "auth_failed",
    402: "quota_exhausted",
    403: "auth_failed",
    408: "executor_timeout",
    429: "rate_limited",
    503: "provider_overloaded",
}

# Tier 3: exception class names; more specific patterns first.
_TYPE_NAME_KINDS = (
    ("sdkprotocol", "contract_rejected"),
    ("transportclosed", "transport_lost"),
    ("timeout", "executor_timeout"),
    ("connection", "transport_lost"),
    ("brokenpipe", "transport_lost"),
    ("protocolerror", "transport_lost"),
)

# Tier 4: bounded prose fallback, reachable only without structured signals.
_MESSAGE_KINDS = (
    ("insufficient balance", "quota_exhausted"),
    ("at capacity", "provider_capacity"),
    ("insufficient capacity", "provider_capacity"),
    ("no capacity", "provider_capacity"),
    ("rate limit", "rate_limited"),
    ("too many requests", "rate_limited"),
    ("overloaded", "provider_overloaded"),
    ("timed out", "executor_timeout"),
    ("timeout", "executor_timeout"),
    ("connection reset", "transport_lost"),
    ("connection refused", "transport_lost"),
    ("connection aborted", "transport_lost"),
    ("connection error", "transport_lost"),
    ("unauthorized", "auth_failed"),
    ("invalid api key", "auth_failed"),
    ("authentication failed", "auth_failed"),
)

_STATUS_FIELDS = ("status_code", "status", "http_status", "http_status_code")
_CODE_FIELDS = ("code", "error_code", "errorCode")


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while (
        current is not None
        and id(current) not in seen
        and len(chain) < _MAX_CAUSE_DEPTH
    ):
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def _signal_containers(err: BaseException) -> list[object]:
    containers: list[object] = [err]
    for attribute in ("response", "body", "error"):
        value = getattr(err, attribute, None)
        if value is not None and not isinstance(value, BaseException):
            containers.append(value)
    return containers


def _field_value(container: object, field: str) -> object:
    if isinstance(container, Mapping):
        return container.get(field)
    return getattr(container, field, None)


def _meaningful(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, (int, float))


def _status_kind(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return "unknown"
    try:
        status = int(value)
    except (TypeError, ValueError):
        return "unknown"
    return _STATUS_KINDS.get(status, "unknown")


def _normalized_code(value: object) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _reduce(kinds: list[str]) -> str | None:
    if not kinds:
        return None
    distinct = set(kinds)
    if len(distinct) == 1:
        return next(iter(distinct))
    return "unknown"


def _classify_signals(
    *,
    codes: list[object],
    statuses: list[object],
    type_names: list[str],
    messages: list[str],
) -> str:
    # Tier 1: a known code decides; an unknown non-empty code fails the whole
    # classification closed. Codes never defer to exception names or prose.
    code_kinds: list[str] = []
    for value in codes:
        mapped = _CODE_KINDS.get(_normalized_code(value), "unknown")
        code_kinds.append(mapped)
    reduced = _reduce(code_kinds)
    if reduced is not None:
        return reduced

    # Tier 2: HTTP status decides only when no code did.
    reduced = _reduce([_status_kind(value) for value in statuses])
    if reduced is not None:
        return reduced

    # Any remaining structured status signal must not become a different
    # category through provider-controlled prose or a coincidental Python
    # exception name.
    if codes or statuses:
        return "unknown"

    # Tier 3: exception class names.
    type_kinds: list[str] = []
    for name in type_names:
        lowered = name.lower()
        for pattern, kind in _TYPE_NAME_KINDS:
            if pattern in lowered:
                type_kinds.append(kind)
                break
    reduced = _reduce(type_kinds)
    if reduced is not None:
        return reduced

    # Tier 4: bounded prose fallback.
    message_kinds: list[str] = []
    for message in messages:
        lowered = message.lower()
        for pattern, kind in _MESSAGE_KINDS:
            if pattern in lowered:
                message_kinds.append(kind)
    return _reduce(message_kinds) or "unknown"


def classify_dsh_failure(exc: BaseException) -> str:
    """Classify one adapter execution exception into a host failure kind."""

    chain = _exception_chain(exc)
    codes: list[object] = []
    statuses: list[object] = []
    for err in chain:
        for container in _signal_containers(err):
            for field in _CODE_FIELDS:
                value = _field_value(container, field)
                if _meaningful(value):
                    codes.append(value)
            for field in _STATUS_FIELDS:
                value = _field_value(container, field)
                if _meaningful(value):
                    statuses.append(value)
    return _classify_signals(
        codes=codes,
        statuses=statuses,
        type_names=[type(err).__name__ for err in chain],
        messages=[str(err) for err in chain],
    )


def _reason_containers(reason: Mapping[str, object]) -> list[Mapping[str, object]]:
    containers: list[Mapping[str, object]] = [reason]
    frontier: list[Mapping[str, object]] = [reason]
    for _ in range(_MAX_REASON_DEPTH):
        next_frontier: list[Mapping[str, object]] = []
        for container in frontier:
            for value in container.values():
                if isinstance(value, Mapping):
                    next_frontier.append(value)
                    containers.append(value)
        frontier = next_frontier
        if not frontier:
            break
    return containers


def classify_dsh_terminal_reason(reason: Mapping[str, object]) -> str:
    """Classify one structured ``turn/end`` failure reason.

    The DeepSeek Harness SDK reports provider failures as a terminal
    ``RunResult`` (``finish_reason == "error"`` with a structured reason on
    the last ``turn/end`` event) rather than a Python exception, so this
    entry point classifies the reason mapping directly.
    """

    codes: list[object] = []
    statuses: list[object] = []
    messages: list[str] = []
    for container in _reason_containers(reason):
        for field in _CODE_FIELDS:
            value = container.get(field)
            if _meaningful(value):
                codes.append(value)
        for field in _STATUS_FIELDS:
            value = container.get(field)
            if _meaningful(value):
                statuses.append(value)
        message = container.get("message")
        if isinstance(message, str) and message.strip():
            messages.append(message)
    return _classify_signals(
        codes=codes,
        statuses=statuses,
        type_names=[],
        messages=messages,
    )
