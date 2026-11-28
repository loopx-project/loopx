"""Antigravity endpoint discovery, HTTP loopback RPC, and model/quota helpers."""

from __future__ import annotations

from datetime import datetime
from email.message import Message
from functools import lru_cache
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Callable, IO, Mapping, NoReturn, Protocol, TypeAlias, TypeGuard
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from ...chat_agent import CodexChatAgentError
from .antigravity_contracts import (
    AntigravityEndpointDict,
    AntigravityModelOptionDict,
    AntigravityRpcResponseDict,
    JsonDict,
    JsonMap,
    JsonSeq,
    as_endpoint_dict,
    as_rpc_response,
)

EventSink: TypeAlias = Callable[[str, JsonDict], None]


class RpcPostFn(Protocol):
    def __call__(
        self,
        base_url: str,
        csrf_token: str,
        method: str,
        payload: JsonMap,
        *,
        timeout: float = 15.0,
    ) -> AntigravityRpcResponseDict: ...


class DiscoverFn(Protocol):
    def __call__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        deadline: float | None = None,
        include_model_config: bool = False,
    ) -> AntigravityEndpointDict | None: ...


class CommandRunner(Protocol):
    def __call__(self, cmd: list[str], *, timeout: float) -> str: ...


ANTIGRAVITY_DEFAULT_MODEL = "gemini-4-argon-high"
ANTIGRAVITY_DEFAULT_REASONING_EFFORT = "high"

ANTIGRAVITY_MODEL_PLACEHOLDERS: dict[str, str] = {
    "gemini-4-argon-high": "MODEL_PLACEHOLDER_M417",
    "gemini-4-argon-medium": "MODEL_PLACEHOLDER_M418",
    "gemini-4-argon-low": "MODEL_PLACEHOLDER_M419",
    "gemini-4-carbon-high": "MODEL_PLACEHOLDER_M427",
    "gemini-4-carbon-medium": "MODEL_PLACEHOLDER_M437",
    "gemini-4-carbon-low": "MODEL_PLACEHOLDER_M438",
}

ANTIGRAVITY_MODEL_OPTIONS: tuple[AntigravityModelOptionDict, ...] = tuple(
    {
        "id": f"gemini-4-{family}-{effort}",
        "display_name": f"Gemini 4 {family.title()} ({effort.title()})",
        "is_default": family == "argon" and effort == "high",
        "default_reasoning_effort": effort,
        "supported_reasoning_efforts": [{"reasoning_effort": effort}],
    }
    for family in ("argon", "carbon")
    for effort in ("high", "medium", "low")
)


def _is_dict(value: object) -> TypeGuard[JsonDict]:
    return isinstance(value, dict)


def _is_strict_int(value: object) -> TypeGuard[int]:
    return type(value) is int


def _is_real_number(value: object) -> TypeGuard[int | float]:
    return type(value) in (int, float)


def _as_int(value: object) -> int:
    if not value:
        return 0
    if isinstance(value, (int, float, str)):
        return int(value)
    raise TypeError("expected integer")


def _opt_dict(value: object) -> JsonDict:
    if not value:
        return {}
    if _is_dict(value):
        return value
    raise TypeError("expected JSON object")


def _subdict(mapping: JsonMap, key: str) -> JsonDict:
    return _opt_dict(mapping.get(key))


class _LocalRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        # A local endpoint's CSRF token must never follow an HTTP redirect.
        return None


@lru_cache(maxsize=1)
def _loopback_rpc_opener() -> urllib.request.OpenerDirector:
    # Only direct HTTP is needed for the validated loopback endpoint.
    opener = urllib.request.OpenerDirector()
    opener.add_handler(urllib.request.HTTPHandler())
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    opener.add_handler(_LocalRedirectHandler())
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    return opener


def _local_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if (
        parsed.scheme != "http"
        or not loopback
        or not parsed.port
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Antigravity requires an HTTP loopback endpoint with a port")
    return value.rstrip("/")


def _reject_nonstandard_rpc_number(_token: str) -> NoReturn:
    raise ValueError("Antigravity RPC response contains a nonstandard JSON number")


def _rpc_post(
    base_url: str,
    csrf_token: str,
    method: str,
    payload: JsonMap,
    *,
    timeout: float = 15.0,
) -> AntigravityRpcResponseDict:
    url = f"{_local_url(base_url)}/exa.language_server_pb.LanguageServerService/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(dict(payload)).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "x-codeium-csrf-token": csrf_token,
        },
        method="POST",
    )
    try:
        with _loopback_rpc_opener().open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            data: object = json.loads(raw, parse_constant=_reject_nonstandard_rpc_number) if raw.strip() else {}
            if not _is_dict(data):
                raise ValueError("Antigravity RPC response must be a JSON object")
            return as_rpc_response(data)
    except urllib.error.HTTPError as exc:
        raise CodexChatAgentError(
            f"Antigravity LanguageServerService/{method} failed with HTTP {exc.code}",
            gate=None,
            error_code="antigravity_rpc_error",
        ) from exc
    except Exception as exc:
        raise CodexChatAgentError(
            f"Antigravity LanguageServerService/{method} transport failed ({type(exc).__name__})",
            gate=None,
            error_code="antigravity_transport_error",
        ) from exc


def _daemon_discovery_candidates(
    daemon_pids: Mapping[int, str],
    source_env: Mapping[str, str],
) -> list[tuple[int, int, str]]:
    """Read verified standalone LS hub/daemon metadata files for live PIDs."""
    raw_home = str(source_env.get("HOME") or "").strip()
    home = Path(raw_home).expanduser() if raw_home else Path.home()
    files: list[tuple[float, str, Path]] = []
    for app_dir in dict.fromkeys(daemon_pids.values()):
        if app_dir in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", app_dir):
            continue
        daemon_dir = home / ".gemini" / app_dir / "daemon"
        if daemon_dir.is_symlink() or not daemon_dir.is_dir():
            continue
        for pat in ("hub_*.json", "ls_*.json"):
            for file_path in daemon_dir.glob(pat):
                try:
                    if not file_path.is_symlink() and file_path.is_file():
                        files.append((file_path.stat().st_mtime, app_dir, file_path))
                except OSError:
                    continue
    seen: set[tuple[int, int]] = set()
    resolved: list[tuple[int, int, str]] = []
    for _, app_dir, file_path in sorted(files, key=lambda item: item[0], reverse=True):
        try:
            data: object = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not _is_dict(data):
            continue
        pid, port, token = data.get("pid"), data.get("httpPort"), data.get("csrfToken")
        if (
            not _is_strict_int(pid)
            or daemon_pids.get(pid) != app_dir
            or not _is_strict_int(port)
            or not (1 <= port <= 65535)
            or not isinstance(token, str)
            or not token.strip()
            or (pid, port) in seen
        ):
            continue
        seen.add((pid, port))
        resolved.append((pid, port, token.strip()))
    return resolved


def _probe_model_config(
    url: str,
    token: str,
    *,
    pid: int | None,
    source: str,
    timeout: float,
    include_model_config: bool,
    rpc_post_fn: RpcPostFn = _rpc_post,
) -> AntigravityEndpointDict:
    model_data = rpc_post_fn(url, token, "GetCascadeModelConfigData", {}, timeout=timeout)
    return as_endpoint_dict({
        "url": url,
        "csrf_token": token,
        "pid": pid,
        "source": source,
        **({"model_config_data": model_data} if include_model_config else {}),
    })


def _cascade_summary_entry(resp: object, cascade_id: str) -> JsonDict | None:
    summaries = resp.get("trajectorySummaries") if _is_dict(resp) else None
    entry = summaries.get(cascade_id) if _is_dict(summaries) else None
    return entry if _is_dict(entry) else None


def probe_cascade_idle(
    url: str,
    csrf_token: str,
    cascade_id: str,
    *,
    timeout: float = 2.5,
    rpc_post_fn: RpcPostFn = _rpc_post,
) -> bool:
    """Return whether `cascade_id` is idle using summary polling with trajectory fallback."""
    try:
        resp = rpc_post_fn(url, csrf_token, "GetAllCascadeTrajectories", {}, timeout=timeout)
        entry = _cascade_summary_entry(resp, cascade_id)
        if entry is not None:
            return entry.get("status") == "CASCADE_RUN_STATUS_IDLE"
    except CodexChatAgentError as exc:
        if exc.error_code != "antigravity_rpc_error":
            return False
    try:
        traj = rpc_post_fn(
            url,
            csrf_token,
            "GetCascadeTrajectory",
            {"cascadeId": cascade_id},
            timeout=max(timeout, 5.0),
        )
    except CodexChatAgentError:
        return False
    return _is_dict(traj) and traj.get("status") == "CASCADE_RUN_STATUS_IDLE"


def _default_run_command(cmd: list[str], *, timeout: float) -> str:
    return str(
        subprocess.check_output(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    )


def _default_uid() -> int | None:
    uid = os.getuid() if hasattr(os, "getuid") else None
    return uid if isinstance(uid, int) else None


def discover_antigravity_language_server(
    env: Mapping[str, str] | None = None,
    *,
    deadline: float | None = None,
    include_model_config: bool = False,
    rpc_post_fn: RpcPostFn = _rpc_post,
    run_command_fn: CommandRunner = _default_run_command,
    getuid_fn: Callable[[], int | None] = _default_uid,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> AntigravityEndpointDict | None:
    """Discover a running local endpoint without reusing an availability verdict.

    Readiness can bound the whole observation, including process listing. An
    execution caller without that deadline retains the existing phase limits.
    """
    source_env = os.environ if env is None else env
    explicit_token = str(source_env.get("ANTIGRAVITY_CSRF_TOKEN") or "").strip()
    raw_url = str(
        source_env.get("ANTIGRAVITY_LS_URL")
        or (source_env.get("ANTIGRAVITY_LS_ADDRESS") if explicit_token else "")
        or ""
    ).strip()
    explicit_url = f"http://{raw_url}" if raw_url and "://" not in raw_url else raw_url
    if explicit_url or explicit_token:
        if not explicit_url or not explicit_token:
            return None
        try:
            explicit_url = _local_url(explicit_url)
            timeout = 2.5 if deadline is None else min(2.5, deadline - monotonic_fn())
            if timeout <= 0:
                return None
            return _probe_model_config(
                explicit_url.rstrip("/"),
                explicit_token,
                pid=None,
                source="environment",
                timeout=timeout,
                include_model_config=include_model_config,
                rpc_post_fn=rpc_post_fn,
            )
        except (CodexChatAgentError, ValueError):
            return None

    process_timeout = 3.0 if deadline is None else min(3.0, deadline - monotonic_fn())
    if process_timeout <= 0:
        return None
    try:
        uid = getuid_fn()
        if uid is None:
            return None
        ps_out = run_command_fn(
            ["ps", "-u", str(uid), "-o", "pid=,command="],
            timeout=process_timeout,
        )
    except Exception:
        return None

    candidates: list[tuple[int, str]] = []
    daemon_pids: dict[int, str] = {}
    for line in ps_out.splitlines():
        if "language_server" not in line or "antigravity" not in line.lower():
            continue
        match = re.match(r"^\s*(\d+)\s+(.*)$", line)
        if not match:
            continue
        pid, cmd = int(match.group(1)), match.group(2)
        token_match = re.search(r"--csrf_token(?:=|\s+)([A-Za-z0-9._-]+)", cmd)
        if token_match:
            candidates.append((pid, token_match.group(1)))
            continue
        dir_match = re.search(r"--app_data_dir(?:=|\s+)([A-Za-z0-9._-]+)", cmd)
        daemon_pids[pid] = dir_match.group(1) if dir_match else "antigravity"

    discovery_deadline = monotonic_fn() + 4.0
    deadline = min(discovery_deadline, deadline) if deadline is not None else discovery_deadline
    for pid, token in candidates:
        if monotonic_fn() >= deadline:
            break
        try:
            lsof_out = run_command_fn(
                ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
                timeout=max(0.1, deadline - monotonic_fn()),
            )
        except Exception:
            continue
        for port_match in re.finditer(r":(\d+)\s+\(LISTEN\)", lsof_out):
            remaining = deadline - monotonic_fn()
            if remaining <= 0:
                return None
            try:
                return _probe_model_config(
                    f"http://127.0.0.1:{int(port_match.group(1))}",
                    token,
                    pid=pid,
                    source="process_discovery",
                    timeout=min(1.5, remaining),
                    include_model_config=include_model_config,
                    rpc_post_fn=rpc_post_fn,
                )
            except CodexChatAgentError:
                continue
    if daemon_pids:
        for pid, port, token in _daemon_discovery_candidates(daemon_pids, source_env):
            remaining = deadline - monotonic_fn()
            if remaining <= 0:
                break
            try:
                return _probe_model_config(
                    f"http://127.0.0.1:{port}",
                    token,
                    pid=pid,
                    source="daemon_discovery",
                    timeout=min(1.5, remaining),
                    include_model_config=include_model_config,
                    rpc_post_fn=rpc_post_fn,
                )
            except CodexChatAgentError:
                continue
    return None


def probe_antigravity_endpoint(
    env: Mapping[str, str] | None = None,
    *,
    discover_fn: DiscoverFn = discover_antigravity_language_server,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> tuple[bool, str, str]:
    """Return `(available, command_display, detail)` for `/api/chat/capabilities`."""
    # The four-second discovery allowance covers this whole advisory read,
    # rather than beginning again after a slow process-listing phase.
    discovered = discover_fn(env=env, deadline=monotonic_fn() + 4.0)
    if discovered is not None:
        return (
            True,
            "antigravity-ls",
            f"Antigravity Language Server ({discovered['url']}, default model {ANTIGRAVITY_DEFAULT_MODEL})",
        )
    app_ls = Path("/Applications/Antigravity.app/Contents/Resources/bin/language_server")
    agy_bin = shutil.which("agy")
    if app_ls.exists() or agy_bin:
        return (
            False,
            str(agy_bin or app_ls),
            "Antigravity is installed, but no active local Antigravity language_server process was found",
        )
    return (
        False,
        "antigravity",
        "Antigravity Language Server not found",
    )


def _matching_antigravity_model_config(
    rows: JsonSeq,
    model_id: str,
    *,
    require_dict_alias: bool = False,
) -> list[JsonDict]:
    """Match clientModelConfigs entries by modelId or placeholder when LS omits modelId."""
    expected_placeholder = ANTIGRAVITY_MODEL_PLACEHOLDERS.get(model_id)
    return [
        row
        for row in rows
        if _is_dict(row)
        and (
            not require_dict_alias
            or not row.get("modelOrAlias")
            or _is_dict(row.get("modelOrAlias"))
        )
        and (
            row.get("modelId") == model_id
            or (
                not row.get("modelId")
                and expected_placeholder is not None
                and _is_dict(row.get("modelOrAlias"))
                and _subdict(row, "modelOrAlias").get("model") == expected_placeholder
            )
        )
    ]


def resolve_antigravity_model_placeholder(
    model_id: object,
    reasoning_effort: object = None,
    *,
    dynamic_configs: JsonSeq | None = None,
) -> tuple[str, str]:
    """Return `(canonical_model_id, model_placeholder)` for Antigravity."""
    raw_model = str(model_id or "").strip().lower()
    effort = str(reasoning_effort or "").strip().lower()
    if effort and effort not in {"low", "medium", "high"}:
        raise CodexChatAgentError(
            "Choose low, medium, or high for Antigravity.",
            gate=None,
            error_code="unsupported_configuration",
        )
    if not raw_model or raw_model in {"argon", "gemini-4-argon"}:
        canonical = f"gemini-4-argon-{effort or 'high'}"
    elif raw_model in {"carbon", "gemini-4-carbon"}:
        canonical = f"gemini-4-carbon-{effort or 'high'}"
    else:
        canonical = raw_model
    if (
        canonical not in ANTIGRAVITY_MODEL_PLACEHOLDERS
        or effort and canonical.rsplit("-", 1)[-1] != effort
    ):
        raise CodexChatAgentError(
            "Choose a supported Antigravity model and its matching reasoning effort.",
            gate=None,
            error_code="unsupported_configuration",
        )
    placeholder = ANTIGRAVITY_MODEL_PLACEHOLDERS[canonical]
    if dynamic_configs is not None:
        matches = _matching_antigravity_model_config(
            list(dynamic_configs), canonical, require_dict_alias=True,
        )
        if not matches:
            raise CodexChatAgentError(
                "The local Antigravity account did not offer this model.",
                gate=None,
                error_code="unsupported_configuration",
            )
        placeholder = str(_subdict(matches[0], "modelOrAlias").get("model") or placeholder)
    return canonical, placeholder


def _discovered_model_configs(
    discovered: JsonMap,
    *,
    rpc_post_fn: RpcPostFn = _rpc_post,
) -> list[JsonDict]:
    """Reuse verified metadata, or read it for an endpoint-only record."""
    data = discovered.get("model_config_data")
    if not isinstance(data, Mapping):
        data = rpc_post_fn(
            str(discovered["url"]),
            str(discovered["csrf_token"]),
            "GetCascadeModelConfigData",
            {},
            timeout=3.0,
        )
    raw = data.get("clientModelConfigs")
    return [row for row in raw if _is_dict(row)] if isinstance(raw, list) else []


def offered_antigravity_model_options(
    *,
    discovered: JsonMap | None = None,
    discover_fn: DiscoverFn = discover_antigravity_language_server,
    rpc_post_fn: RpcPostFn = _rpc_post,
) -> list[AntigravityModelOptionDict]:
    """Read registered choices from the local account without opening a cascade."""
    if discovered is None:
        discovered = discover_fn(include_model_config=True)
    if discovered is None:
        raise CodexChatAgentError(
            "Antigravity local language_server is not running or reachable",
            gate=None,
            error_code="antigravity_unavailable",
        )
    configs = _discovered_model_configs(discovered, rpc_post_fn=rpc_post_fn)
    options: list[AntigravityModelOptionDict] = []
    for item in ANTIGRAVITY_MODEL_OPTIONS:
        try:
            resolve_antigravity_model_placeholder(item.get("id"), dynamic_configs=configs)
            options.append(item)
        except CodexChatAgentError:
            continue
    if not options:
        raise CodexChatAgentError(
            "The local Antigravity account did not offer a supported model. Retry model discovery.",
            gate=None,
            error_code="unsupported_configuration",
        )
    return options


def _extract_user_model_quota(
    user: object,
    model_id: str,
    *,
    fallback_rows: JsonSeq | None = None,
) -> tuple[str, float, str] | None:
    """Validate and extract `(account_ref, remaining_fraction, reset_time)` for one canonical model."""
    if not isinstance(user, Mapping):
        return None
    account = user.get("email")
    if not isinstance(account, str) or not account.strip():
        return None
    rows = _subdict(user, "cascadeModelConfigData").get("clientModelConfigs")
    if not isinstance(rows, list):
        if fallback_rows is None:
            return None
        rows = fallback_rows
    matching = _matching_antigravity_model_config(rows, model_id)
    if len(matching) != 1:
        return None
    quota = matching[0]["quotaInfo"]
    if not _is_dict(quota):
        raise TypeError("quotaInfo must be an object")
    fraction, reset = quota.get("remainingFraction", 0), quota.get("resetTime")
    if (
        not _is_real_number(fraction)
        or not math.isfinite(fraction)
        or not 0 <= fraction <= 1
        or not isinstance(reset, str)
        or datetime.fromisoformat(reset).tzinfo is None
    ):
        return None
    return (
        hashlib.sha256(account.strip().casefold().encode()).hexdigest(),
        float(fraction) if isinstance(fraction, int) else fraction,
        reset,
    )
