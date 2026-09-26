from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Callable, Optional


NormalizeText = Callable[..., str]
CompactText = Callable[..., Optional[str]]
DEFAULT_PUBLIC_SAFE_LIST_LIMIT = 4
LOCAL_PATH_SURFACE_PATTERN = re.compile(
    r"(?<![:/A-Za-z0-9])(?:"
    r"/(?:Users|home|Volumes|private|tmp|var|etc|opt|srv|mnt|root|data|workspace|workspaces)/"
    r"[^\s`'\"<>]+|"
    r"[A-Za-z]:[\\/][^\s`'\"<>]+|"
    r"\\\\[A-Za-z0-9_.-]+\\[^\s`'\"<>]+"
    r")",
    re.IGNORECASE,
)
SECRET_LIKE_SURFACE_PATTERN = re.compile(
    r"(?i)(?:\bbearer\s+[a-z0-9._~+/=-]{16,}|"
    r"\b(?:access|api|secret)[_-]?key[\"']?\s*[=:]\s*[\"']?[^\s`'\"<>]+|"
    r"\b(?:ak|sk)[\"']?\s*[=:]\s*[\"']?[^\s`'\"<>]+|"
    r"(?<![a-z0-9_])(?:ak|sk)[-_=:][a-z0-9_=-]{10,}|"
    r"\bgh[pousr]_[a-z0-9]{16,}\b|"
    r"\bgithub_pat_[a-z0-9_]{20,}|"
    r"\b(?:akia|asia)[a-z0-9]{16}\b|"
    r"\bxox[baprs]-[a-z0-9-]{10,}|"
    r"\baiza[a-z0-9_-]{20,}|"
    r"\b(?:sk|rk)_(?:live|test)_[a-z0-9]{12,}|"
    r"\bnpm_[a-z0-9]{20,}|"
    r"\bpypi-[a-z0-9_-]{20,}|"
    r"\beyj[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\b|"
    r"\b(?:access|refresh)[_-]?token[\"']?\s*[=:]\s*[\"']?[^\s`'\"<>]{12,}|"
    r"\b(?:password|secret)[\"']?\s*[=:]\s*[\"']?[^\s`'\"<>]{12,}|"
    r"-{3,}\s*BEGIN (?:[A-Z]+ )?PRIVATE KEY|"
    r"\btoken[\"']?\s*[=:]\s*[\"']?[^\s`'\"<>]{12,})"
)
_CREDENTIAL_FIELD_FAMILIES = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "authtoken",
        "authorization",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "password",
        "privatekey",
        "refreshtoken",
        "secret",
        "sessiontoken",
        "token",
    }
)
_UNBOUNDED_PAYLOAD_FIELD_FAMILIES = frozenset({"requestbody", "responsebody"})
LOOPX_COMMAND_RECORD_ALLOWED_SUBCOMMANDS = {
    "start-goal",
    "quota should-run",
    "todo add",
    "todo claim",
    "todo update",
    "todo complete",
    "refresh-state",
    "quota spend-slot",
    "status",
    "diagnose",
}
LOOPX_COMMAND_RECORD_TODO_ID_PATTERN = re.compile(r"^todo_[A-Za-z0-9_-]{6,80}$")
LOOPX_COMMAND_RECORD_GOAL_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,120}$"
)


def compact_text(text: str, *, limit: int) -> str:
    compact = " ".join(str(text or "").strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def normalize_public_safe_field_name(value: object) -> str:
    """Normalize one structured-output key before exact risk classification."""

    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value or "").strip())
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").casefold()


def _is_credential_field(normalized_key: str) -> bool:
    flattened_key = normalized_key.replace("_", "")
    return flattened_key in _CREDENTIAL_FIELD_FAMILIES or normalized_key.endswith(
        ("_token", "_secret", "_password", "_credential", "_credentials")
    )


def validate_public_safe_value(
    value: object,
    *,
    path: str = "public_payload",
) -> None:
    """Fail closed for private material in a typed public-output payload.

    Mapping field names must be strings. Field classification is exact after
    case, separator, and camelCase normalization. Values are then checked
    recursively so nested maps and lists cannot bypass the same credential
    and local-path boundary.
    """

    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(
                    f"{path} contains a non-string field name ({type(key).__name__})"
                )
            key_text = key
            if LOCAL_PATH_SURFACE_PATTERN.search(
                key_text
            ) or SECRET_LIKE_SURFACE_PATTERN.search(key_text):
                raise ValueError(f"{path} contains an unsafe field name")
            normalized_key = normalize_public_safe_field_name(key_text)
            flattened_key = normalized_key.replace("_", "")
            item_path = f"{path}.{key}"
            if _is_credential_field(normalized_key):
                raise ValueError(f"{item_path} is a credential-bearing field")
            if flattened_key in _UNBOUNDED_PAYLOAD_FIELD_FAMILIES:
                raise ValueError(f"{item_path} is an unbounded payload field")
            if normalized_key == "raw" or normalized_key.startswith("raw_"):
                raise ValueError(f"{item_path} is an unbounded raw payload field")
            validate_public_safe_value(item, path=item_path)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_public_safe_value(item, path=f"{path}[{index}]")
        return
    if not isinstance(value, str):
        return
    if LOCAL_PATH_SURFACE_PATTERN.search(value):
        raise ValueError(f"{path} contains an absolute local path")
    if SECRET_LIKE_SURFACE_PATTERN.search(value):
        raise ValueError(f"{path} contains a credential-like value")


def public_safe_compact_text(
    value: Any,
    *,
    limit: int = 220,
    normalize_text: NormalizeText | None = None,
    local_path_surface_pattern: Any = LOCAL_PATH_SURFACE_PATTERN,
    secret_like_surface_pattern: Any = SECRET_LIKE_SURFACE_PATTERN,
) -> str | None:
    normalize = normalize_text or compact_text
    text = normalize(str(value or ""), limit=limit)
    if not text:
        return None
    if local_path_surface_pattern.search(text) or secret_like_surface_pattern.search(
        text
    ):
        return None
    return text


def public_safe_compact_list(
    value: Any,
    *,
    limit: int = DEFAULT_PUBLIC_SAFE_LIST_LIMIT,
    compact_text: CompactText | None = None,
    item_limit: int = 160,
) -> list[str]:
    values = value if isinstance(value, list) else [value] if value else []
    result: list[str] = []
    compact_item = compact_text or public_safe_compact_text
    for item in values:
        text = compact_item(item, limit=item_limit)
        if not text:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def compact_numeric_map(
    value: Any, *, keys: tuple[str, ...] | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    selected_keys = keys or tuple(str(key) for key in value.keys())
    compact: dict[str, Any] = {}
    for key in selected_keys:
        raw = value.get(key)
        if isinstance(raw, bool) or raw is None:
            continue
        if isinstance(raw, (int, float)):
            compact[key] = raw
            continue
        try:
            if isinstance(raw, str) and raw.strip():
                compact[key] = float(raw) if "." in raw else int(raw)
        except ValueError:
            continue
    return compact


def compact_loopx_command_records(
    value: Any, *, limit: int = 128
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    records: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        subcommand = public_safe_compact_text(item.get("subcommand"), limit=80)
        if subcommand not in LOOPX_COMMAND_RECORD_ALLOWED_SUBCOMMANDS:
            continue
        record: dict[str, str] = {"subcommand": subcommand}
        todo_id = public_safe_compact_text(item.get("todo_id"), limit=100)
        if todo_id and LOOPX_COMMAND_RECORD_TODO_ID_PATTERN.match(todo_id):
            record["todo_id"] = todo_id
        goal_id = public_safe_compact_text(item.get("goal_id"), limit=140)
        if goal_id and LOOPX_COMMAND_RECORD_GOAL_ID_PATTERN.match(goal_id):
            record["goal_id"] = goal_id
        records.append(record)
        if len(records) >= limit:
            break
    return records
