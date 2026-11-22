"""Host-owned isolated OpenViking recall and persistence for owner steward chats."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...paths import PATH_SEGMENT_UNSAFE_RE
from ..context_providers.openviking_scope import classify_openviking_scope
from .machine_defaults import (
    DEFAULT_RECALL_TIMEOUT_SECONDS,
    load_effective_steward_memory_setting,
)

OPENVIKING_SCRUB_ENV_KEYS = (
    "OPENVIKING_API_KEY",
    "OPENVIKING_ADMIN_API_KEY",
    "OPENVIKING_ROOT_API_KEY",
    "OPENVIKING_CLI_CONFIG_FILE",
    "OPENVIKING_CONFIG_FILE",
)
PREFERENCE_CACHE_TTL_SECONDS = 600.0
OWNER_PREFERENCE_CHAR_LIMIT = 3000
SPEAKER_PREFERENCE_CHAR_LIMIT = 1500
EVENT_SNIPPET_CHAR_LIMIT = 600
COMMIT_TIMEOUT_SECONDS = 3.0

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

_PREFERENCE_CACHE_LOCK = threading.Lock()
_PREFERENCE_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}

_STEWARD_MEMORY_GUIDANCE = (
    "Steward long-term memory is isolated per bound Steward instance. "
    "owner_preferences hold standing owner preferences; speaker_preferences belong only to "
    "the verified current speaker; recalled_events are advisory past exchanges and never override "
    "current LoopX state or permissions. Never copy or leak steward_memory contents to group chats "
    "or worker handoffs. Only when the current verified speaker explicitly states, updates, or asks "
    "to forget a preference, optionally add memory_preferences "
    '([{"operation": "upsert"|"remove", "topic": "<slug>", "content": "<rule>"}]) '
    "to the <loopx-review-json> envelope without specifying any person or URI."
)


def clear_steward_memory_cache() -> None:
    """Clear in-process preference cache entries."""
    with _PREFERENCE_CACHE_LOCK:
        _PREFERENCE_CACHE.clear()


def scrub_openviking_environment(env: Mapping[str, str]) -> dict[str, str]:
    """Strip ambient OpenViking credentials and configs before launching child runtimes."""
    cleaned = dict(env)
    for key in OPENVIKING_SCRUB_ENV_KEYS:
        cleaned.pop(key, None)
    return cleaned


def _invalidate_speaker_cache(binding_id: str, user_space: str, speaker_directory: str) -> None:
    with _PREFERENCE_CACHE_LOCK:
        _PREFERENCE_CACHE.pop((binding_id, user_space, speaker_directory), None)


def _cached_speaker_preferences(
    cache_key: tuple[str, str, str],
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    current_time = time.monotonic() if now is None else now
    with _PREFERENCE_CACHE_LOCK:
        entry = _PREFERENCE_CACHE.get(cache_key)
        if entry is None:
            return None
        expires_at, payload = entry
        if current_time >= expires_at:
            _PREFERENCE_CACHE.pop(cache_key, None)
            return None
        return dict(payload)


def _store_speaker_preferences(
    cache_key: tuple[str, str, str],
    payload: Mapping[str, Any],
    *,
    now: float | None = None,
) -> None:
    current_time = time.monotonic() if now is None else now
    with _PREFERENCE_CACHE_LOCK:
        _PREFERENCE_CACHE[cache_key] = (
            current_time + PREFERENCE_CACHE_TTL_SECONDS,
            dict(payload),
        )


def _connection_candidate_paths(runtime_root: Path) -> tuple[Path, ...]:
    return (
        runtime_root / "machine" / "credentials" / "steward-openviking.json",
        runtime_root / "memory" / "steward" / "ovcli.json",
    )


def _load_connection_profile(runtime_root: Path) -> dict[str, Any] | None:
    for path in _connection_candidate_paths(runtime_root):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(raw, Mapping):
            continue
        service_url = str(raw.get("service_url") or raw.get("url") or "").strip()
        api_key = str(raw.get("api_key") or "").strip()
        if not service_url or not api_key:
            continue
        ov_bin = str(raw.get("ov_bin") or "").strip() or shutil.which("ov") or ""
        if not ov_bin:
            return None
        pass_actor = raw.get("pass_actor_peer_id")
        if type(pass_actor) is not bool:
            pass_actor = True
        return {
            "service_url": service_url,
            "api_key": api_key,
            "account_id": str(raw.get("account_id") or raw.get("account") or "").strip() or None,
            "user_id": str(raw.get("user_id") or raw.get("user") or "").strip() or None,
            "ov_bin": ov_bin,
            "pass_actor_peer_id": pass_actor,
        }
    return None


@contextmanager
def _ephemeral_ov_env(
    profile: Mapping[str, Any],
    *,
    timeout_seconds: float,
    scratch_root: Path | None = None,
):
    base_dir = scratch_root if scratch_root and scratch_root.is_dir() else None
    config = {
        "url": profile["service_url"],
        "api_key": profile["api_key"],
        "timeout": max(2, int(timeout_seconds)),
        "output": "json",
        "verbose": False,
        "echo_command": False,
    }
    with tempfile.TemporaryDirectory(prefix="steward-ov-", dir=base_dir) as tmp:
        conf_path = Path(tmp) / "ovcli.conf"
        fd = os.open(conf_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream)
        env = scrub_openviking_environment(os.environ)
        for key in list(env):
            if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
                env.pop(key)
        env["NO_PROXY"] = "127.0.0.1,localhost"
        env["OPENVIKING_CLI_CONFIG_FILE"] = str(conf_path)
        yield env, Path(tmp)


def _extract_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty output")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char not in "[{":
            continue
        try:
            value, _end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        return value
    raise ValueError("no JSON payload")


def _extract_entries(payload: Any) -> list[Mapping[str, Any]]:
    target = payload.get("result") if isinstance(payload, Mapping) and "result" in payload else payload
    if isinstance(target, list):
        return [row for row in target if isinstance(row, Mapping)]
    if isinstance(target, Mapping):
        for key in ("items", "entries", "memories", "resources", "results"):
            rows = target.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, Mapping)]
    return []


def _extract_content(payload: Any) -> str:
    target = payload.get("result") if isinstance(payload, Mapping) and "result" in payload else payload
    if isinstance(target, str):
        return target
    if isinstance(target, Mapping):
        for key in ("content", "text"):
            val = target.get(key)
            if isinstance(val, str):
                return val
    return ""


def _run_ov_command(
    profile: Mapping[str, Any],
    args: Sequence[str],
    *,
    actor_peer_id: str,
    env: Mapping[str, str],
    deadline: float,
    runner: CommandRunner,
) -> subprocess.CompletedProcess[str]:
    remaining = deadline - time.monotonic()
    if remaining <= 0.05:
        raise subprocess.TimeoutExpired([str(profile["ov_bin"]), *args], remaining)
    command = [str(profile["ov_bin"])]
    if profile.get("pass_actor_peer_id", True):
        command.extend(["--actor-peer-id", actor_peer_id])
    if profile.get("account_id"):
        command.extend(["--account", str(profile["account_id"])])
    if profile.get("user_id"):
        command.extend(["--user", str(profile["user_id"])])
    command.extend(args)
    return runner(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=remaining,
        env=dict(env),
        check=False,
    )


def _validate_peer_scope(uri: str, actor_peer_id: str) -> None:
    scope = classify_openviking_scope(uri)
    if scope.actor_scope_id != actor_peer_id:
        raise ValueError("OpenViking URI does not match isolated Steward peer")


def _read_preferences_directory(
    profile: Mapping[str, Any],
    *,
    preferences_uri: str,
    actor_peer_id: str,
    char_limit: int,
    env: Mapping[str, str],
    deadline: float,
    runner: CommandRunner,
) -> dict[str, Any]:
    _validate_peer_scope(preferences_uri, actor_peer_id)
    ls_res = _run_ov_command(
        profile,
        ["ls", preferences_uri, "-n", "100", "-o", "json", "-c", "true"],
        actor_peer_id=actor_peer_id,
        env=env,
        deadline=deadline,
        runner=runner,
    )
    if ls_res.returncode != 0:
        if "NOT_FOUND" in ls_res.stdout or "404" in ls_res.stdout:
            return {"items": [], "omitted_topics": []}
        raise RuntimeError("preference_ls_failed")
    try:
        entries = _extract_entries(_extract_json(ls_res.stdout))
    except ValueError:
        return {"items": [], "omitted_topics": []}

    md_entries = []
    for entry in entries:
        if entry.get("isDir") is True or entry.get("type") == "dir":
            continue
        uri = str(entry.get("uri") or "").strip()
        name = uri.rstrip("/").rsplit("/", 1)[-1] if uri else str(entry.get("name") or "")
        if not name.endswith(".md") or name.startswith("."):
            continue
        target_uri = uri if uri.startswith("viking://") else f"{preferences_uri.rstrip('/')}/{name}"
        if not target_uri.startswith(f"{preferences_uri.rstrip('/')}/"):
            continue
        md_entries.append((str(entry.get("mtime") or ""), name, target_uri))

    md_entries.sort(key=lambda row: (row[0], row[1]), reverse=True)
    items: list[dict[str, str]] = []
    omitted_topics: list[str] = []
    used_chars = 0
    for _mtime, name, target_uri in md_entries:
        topic = name[:-3]
        if used_chars >= char_limit:
            omitted_topics.append(topic)
            continue
        read_res = _run_ov_command(
            profile,
            ["read", target_uri, "-o", "json", "-c", "true"],
            actor_peer_id=actor_peer_id,
            env=env,
            deadline=deadline,
            runner=runner,
        )
        if read_res.returncode != 0:
            continue
        try:
            content = _extract_content(_extract_json(read_res.stdout)).strip()
        except ValueError:
            continue
        if not content:
            continue
        remaining_budget = char_limit - used_chars
        if len(content) > remaining_budget and items:
            omitted_topics.append(topic)
            continue
        bounded = content[:remaining_budget]
        used_chars += len(bounded)
        items.append({"topic": topic, "topic_file": name, "content": bounded})
    return {"items": items, "omitted_topics": omitted_topics}


def _is_substantive_message(message: str) -> bool:
    text = str(message or "").strip()
    if len(text) <= 3:
        return False
    if text.startswith("/"):
        return False
    return True


def _recall_events(
    profile: Mapping[str, Any],
    *,
    events_uri: str,
    actor_peer_id: str,
    query: str,
    env: Mapping[str, str],
    deadline: float,
    runner: CommandRunner,
) -> list[dict[str, str]]:
    _validate_peer_scope(events_uri, actor_peer_id)
    compact_query = public_safe_compact_text(query, limit=240)
    if not compact_query:
        return []
    find_res = _run_ov_command(
        profile,
        ["find", compact_query, "-u", events_uri, "-n", "3", "-L", "2", "-o", "json", "-c", "true"],
        actor_peer_id=actor_peer_id,
        env=env,
        deadline=deadline,
        runner=runner,
    )
    if find_res.returncode != 0:
        return []
    try:
        rows = _extract_entries(_extract_json(find_res.stdout))
    except ValueError:
        return []

    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    prefix = f"{events_uri.rstrip('/')}/"
    for row in rows:
        uri = str(row.get("uri") or row.get("resource_uri") or "").strip()
        if not uri.startswith(prefix) or uri in seen:
            continue
        if uri.rsplit("/", 1)[-1] in {".overview.md", ".abstract.md"}:
            continue
        seen.add(uri)
        body = _extract_content(row).strip() or str(
            row.get("abstract") or row.get("summary") or row.get("overview") or ""
        ).strip()
        if not body and time.monotonic() < deadline - 0.15:
            read_res = _run_ov_command(
                profile,
                ["read", uri, "-o", "json", "-c", "true"],
                actor_peer_id=actor_peer_id,
                env=env,
                deadline=deadline,
                runner=runner,
            )
            if read_res.returncode == 0:
                try:
                    body = _extract_content(_extract_json(read_res.stdout)).strip()
                except ValueError:
                    body = ""
        if body:
            hits.append(
                {
                    "event_ref": uri.removeprefix(prefix),
                    "excerpt": body[:EVENT_SNIPPET_CHAR_LIMIT],
                }
            )
        if len(hits) >= 3:
            break
    return hits


def _plan_turn(
    runtime_root: Path,
    session: Mapping[str, Any],
    scope: Mapping[str, Any],
    turn: Mapping[str, Any] | None,
    *,
    sender_ref: str | None = None,
    sender_type: str | None = None,
    sender_label: str | None = None,
) -> dict[str, Any]:
    try:
        setting = load_effective_steward_memory_setting(runtime_root)
    except (OSError, ValueError, TypeError):
        return {"eligible": False, "reason": "setting_unavailable"}
    if not setting.get("enabled"):
        return {"eligible": False, "reason": "disabled"}
    try:
        return effect_runtime_result(
            "collaboration.steward.memory_policy",
            {
                "setting": setting,
                "session": dict(session),
                "scope": dict(scope),
                "turn": dict(turn) if isinstance(turn, Mapping) else None,
                **({"sender_ref": sender_ref} if sender_ref is not None else {}),
                **({"sender_type": sender_type} if sender_type is not None else {}),
                **({"sender_label": sender_label} if sender_label is not None else {}),
            },
        )
    except (EffectRuntimeRejected, ValueError, TypeError):
        return {"eligible": False, "reason": "policy_rejected"}


def recall_steward_memory_for_turn(
    *,
    runtime_root: Path,
    session: Mapping[str, Any],
    scope: Mapping[str, Any],
    turn: Mapping[str, Any] | None,
    message: str,
    sender_ref: str | None = None,
    sender_type: str | None = None,
    sender_label: str | None = None,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any] | None:
    """Recall isolated OpenViking preferences and bounded events on ingress.

    Returns ``None`` immediately when disabled or outside an eligible owner private
    steward Turn. Fails open on timeout or unavailable OpenViking service so normal
    Steward replies are never blocked.
    """
    plan = _plan_turn(
        runtime_root,
        session,
        scope,
        turn,
        sender_ref=sender_ref,
        sender_type=sender_type,
        sender_label=sender_label,
    )
    if not plan.get("eligible"):
        return None

    started = time.monotonic()
    timeout_seconds = float(plan.get("recall_timeout_seconds") or DEFAULT_RECALL_TIMEOUT_SECONDS)
    deadline = started + max(1.0, timeout_seconds)
    base_payload: dict[str, Any] = {
        "schema_version": "steward_memory_recall_v0",
        "actor_peer_id": plan["actor_peer_id"],
        "speaker_directory": plan["speaker_directory"],
        "is_owner": bool(plan["is_owner"]),
        "owner_preferences": [],
        "omitted_owner_topics": [],
        "speaker_preferences": [],
        "omitted_speaker_topics": [],
        "recalled_events": [],
        "guidance": _STEWARD_MEMORY_GUIDANCE,
    }
    profile = _load_connection_profile(runtime_root)
    if profile is None:
        return {
            **base_payload,
            "status": "not_configured",
            "latency_ms": int((time.monotonic() - started) * 1000),
        }

    binding_id = str(plan["binding_id"])
    user_space = str(plan["user_space"])
    actor_peer_id = str(plan["actor_peer_id"])
    owner_key = (binding_id, user_space, "owner")
    speaker_dir = str(plan["speaker_directory"])
    speaker_key = (binding_id, user_space, speaker_dir)

    owner_data = _cached_speaker_preferences(owner_key)
    speaker_data = (
        owner_data
        if plan["is_owner"]
        else _cached_speaker_preferences(speaker_key)
    )
    recalled_events: list[dict[str, str]] = []
    status = "completed"

    try:
        with _ephemeral_ov_env(profile, timeout_seconds=timeout_seconds) as (env, _tmp):
            if owner_data is None:
                owner_data = _read_preferences_directory(
                    profile,
                    preferences_uri=str(plan["owner_preferences_uri"]),
                    actor_peer_id=actor_peer_id,
                    char_limit=OWNER_PREFERENCE_CHAR_LIMIT,
                    env=env,
                    deadline=deadline,
                    runner=runner,
                )
                _store_speaker_preferences(owner_key, owner_data)
                if plan["is_owner"]:
                    speaker_data = owner_data
            if not plan["is_owner"] and speaker_data is None:
                speaker_data = _read_preferences_directory(
                    profile,
                    preferences_uri=str(plan["speaker_preferences_uri"]),
                    actor_peer_id=actor_peer_id,
                    char_limit=SPEAKER_PREFERENCE_CHAR_LIMIT,
                    env=env,
                    deadline=deadline,
                    runner=runner,
                )
                _store_speaker_preferences(speaker_key, speaker_data)
            if (
                plan.get("auto_recall") is True
                and _is_substantive_message(message)
                and time.monotonic() < deadline - 0.15
            ):
                recalled_events = _recall_events(
                    profile,
                    events_uri=str(plan["events_uri"]),
                    actor_peer_id=actor_peer_id,
                    query=message,
                    env=env,
                    deadline=deadline,
                    runner=runner,
                )
    except subprocess.TimeoutExpired:
        status = "timeout"
    except (OSError, RuntimeError, ValueError):
        status = "unavailable"

    owner_items = list((owner_data or {}).get("items") or [])
    owner_omitted = list((owner_data or {}).get("omitted_topics") or [])
    speaker_items = [] if plan["is_owner"] else list((speaker_data or {}).get("items") or [])
    speaker_omitted = [] if plan["is_owner"] else list((speaker_data or {}).get("omitted_topics") or [])
    return {
        **base_payload,
        "status": status,
        "owner_preferences": owner_items,
        "omitted_owner_topics": owner_omitted,
        "speaker_preferences": speaker_items,
        "omitted_speaker_topics": speaker_omitted,
        "recalled_events": recalled_events,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


def _safe_event_filename(turn_id: str) -> str:
    cleaned = PATH_SEGMENT_UNSAFE_RE.sub("-", str(turn_id or "").strip()).strip("-.")[:64]
    if not cleaned:
        cleaned = f"turn-{int(time.time())}"
    return f"{cleaned}.md"


def _write_ov_file(
    profile: Mapping[str, Any],
    *,
    target_uri: str,
    content: str,
    actor_peer_id: str,
    env: Mapping[str, str],
    tmp_dir: Path,
    deadline: float,
    runner: CommandRunner,
    index: int,
) -> bool:
    _validate_peer_scope(target_uri, actor_peer_id)
    local_file = tmp_dir / f"payload-{index}.md"
    local_file.write_text(content, encoding="utf-8")
    res = _run_ov_command(
        profile,
        [
            "write",
            target_uri,
            "--from-file",
            str(local_file),
            "--mode",
            "replace",
            "--wait",
            "--processing-mode",
            "vectors_only",
            "-o",
            "json",
            "-c",
            "true",
        ],
        actor_peer_id=actor_peer_id,
        env=env,
        deadline=deadline,
        runner=runner,
    )
    return res.returncode == 0


def commit_steward_memory_after_turn(
    *,
    runtime_root: Path,
    session: Mapping[str, Any],
    scope: Mapping[str, Any],
    turn: Mapping[str, Any] | None,
    turn_id: str,
    user_message: str,
    response: Mapping[str, Any],
    sender_ref: str | None = None,
    sender_type: str | None = None,
    sender_label: str | None = None,
    timeout_seconds: float = COMMIT_TIMEOUT_SECONDS,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any] | None:
    """Persist speaker-scoped preference mutations and compact turn event after completion."""
    plan = _plan_turn(
        runtime_root,
        session,
        scope,
        turn,
        sender_ref=sender_ref,
        sender_type=sender_type,
        sender_label=sender_label,
    )
    if not plan.get("eligible"):
        return None

    started = time.monotonic()
    deadline = started + max(1.0, float(timeout_seconds))
    profile = _load_connection_profile(runtime_root)
    if profile is None:
        return {
            "schema_version": "steward_memory_commit_v0",
            "status": "not_configured",
            "preferences_applied": 0,
            "event_written": False,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }

    binding_id = str(plan["binding_id"])
    user_space = str(plan["user_space"])
    actor_peer_id = str(plan["actor_peer_id"])
    speaker_dir = str(plan["speaker_directory"])
    speaker_prefs_uri = str(plan["speaker_preferences_uri"]).rstrip("/")
    events_uri = str(plan["events_uri"]).rstrip("/")

    raw_prefs = response.get("memory_preferences")
    prefs: list[dict[str, Any]] = []
    if isinstance(raw_prefs, list) and raw_prefs:
        try:
            prefs = list(
                effect_runtime_result(
                    "collaboration.steward.memory_preferences",
                    {"memory_preferences": raw_prefs},
                ).get("preferences")
                or []
            )
        except (EffectRuntimeRejected, ValueError, TypeError):
            prefs = []

    if prefs:
        _invalidate_speaker_cache(binding_id, user_space, speaker_dir)

    applied = 0
    event_written = False
    status = "completed"
    try:
        with _ephemeral_ov_env(profile, timeout_seconds=timeout_seconds) as (env, tmp_dir):
            for idx, pref in enumerate(prefs):
                target_uri = f"{speaker_prefs_uri}/{pref['topic_file']}"
                if pref["operation"] == "upsert":
                    if _write_ov_file(
                        profile,
                        target_uri=target_uri,
                        content=str(pref["content"]),
                        actor_peer_id=actor_peer_id,
                        env=env,
                        tmp_dir=tmp_dir,
                        deadline=deadline,
                        runner=runner,
                        index=idx,
                    ):
                        applied += 1
                elif pref["operation"] == "remove":
                    _validate_peer_scope(target_uri, actor_peer_id)
                    rm_res = _run_ov_command(
                        profile,
                        ["rm", target_uri, "-o", "json", "-c", "true"],
                        actor_peer_id=actor_peer_id,
                        env=env,
                        deadline=deadline,
                        runner=runner,
                    )
                    if rm_res.returncode == 0:
                        applied += 1

            compact_user = public_safe_compact_text(user_message, limit=800)
            compact_assistant = public_safe_compact_text(response.get("message") or "", limit=800)
            if compact_user or compact_assistant:
                timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                event_doc = (
                    f"speaker={speaker_dir}\n"
                    f"timestamp={timestamp}\n"
                    f"user: {compact_user or '[empty]'}\n"
                    f"steward: {compact_assistant or '[empty]'}\n"
                )
                event_uri = f"{events_uri}/{_safe_event_filename(turn_id)}"
                event_written = _write_ov_file(
                    profile,
                    target_uri=event_uri,
                    content=event_doc,
                    actor_peer_id=actor_peer_id,
                    env=env,
                    tmp_dir=tmp_dir,
                    deadline=deadline,
                    runner=runner,
                    index=999,
                )
            if (prefs and applied < len(prefs)) or not event_written:
                status = "partial"
    except subprocess.TimeoutExpired:
        status = "timeout"
    except (OSError, RuntimeError, ValueError):
        status = "unavailable"

    return {
        "schema_version": "steward_memory_commit_v0",
        "status": status,
        "preferences_applied": applied,
        "event_written": event_written,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }
