"""Owner-scoped local evidence-pack I/O for benchmark-toolkit's typed observer.

The authenticated host supplies runtime/Goal scope. Renderer input is limited to
opaque IDs and citation ranges. This provider verifies captured file bytes; the
existing TS owner still owns episode eligibility, analysis identity and judgments.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from ...control_plane.content_digest import BARE_SHA256_PATTERN
from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ...file_lock import exclusive_file_lock
from .trajectory_assessment import _digest, _write, execute, read_job, request_cancel

DEFAULT_CONFIG = {"model": "gpt-6.1-sol", "effort": "max", "service_tier": "fast"}


class PackUnavailable(ValueError):
    """Local I/O failure code; never disclose filesystem exceptions to clients."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _opaque(value: str) -> str:
    if not isinstance(value, str) or not BARE_SHA256_PATTERN.fullmatch(value):
        raise PackUnavailable("invalid_identifier")
    return value


def _owner(runtime_root: Path, owner_goal_id: str) -> Path:
    if not isinstance(owner_goal_id, str) or not owner_goal_id.strip():
        raise PackUnavailable("missing_goal_scope")
    return (
        runtime_root.resolve()
        / "benchmark"
        / "trajectory-assessments"
        / _digest(owner_goal_id)
    )


def _pack_id(owner_goal_id: str, record: dict) -> str:
    identity = {
        "owner_goal_id": owner_goal_id,
        "analysis_key": record["analysis_key"],
        "title": record["title"],
    }
    if "solver_usage" in record:
        identity["solver_usage"] = record["solver_usage"]
    if "native_outcomes" in record:
        identity["native_outcomes"] = record["native_outcomes"]
    if "base_pack_id" in record:
        identity["base_pack_id"] = record["base_pack_id"]
    return _digest(identity)


def _prepared(record: dict, output_language: str | None = None) -> dict:
    return effect_runtime_result(
        "benchmark.trajectory.prepare",
        {"input": record["input"], "config": {**record["config"],
            **({"output_language": output_language} if output_language is not None else {})}},
    )


def _verify_sources(record: dict) -> None:
    for source in record["input"]["sources"]:
        path = _source_path(Path(record["source_root"]), source["artifact"])
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != source["artifact_sha256"]:
            raise PackUnavailable("source_changed")
        lines = data.decode("utf-8").split("\n")
        start = source["first_line"] - 1
        count = len(source["text"].split("\n"))
        if "\n".join(lines[start : start + count]) != source["text"]:
            raise PackUnavailable("excerpt_mismatch")


def _source_path(source_root: Path, artifact: str) -> Path:
    relative = PurePosixPath(artifact)
    if relative.is_absolute() or ".." in relative.parts or "\\" in artifact:
        raise PackUnavailable("invalid_source_path")
    root = source_root.resolve(strict=True)
    path = (root / str(relative)).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise PackUnavailable("invalid_source_path")
    return path


def _request_usage_record(value: object) -> object:
    """Transport the provider's typed usage only; TS owns reconciliation."""
    if not isinstance(value, dict):
        return None
    result: dict[str, object] = {
        key: value[key] if isinstance(value[key], str) else None
        for key in ("thread_id", "session_id", "turn_id", "root_turn_id", "response_id")
        if key in value
    }
    for key in ("usage", "turn_token_usage", "thread_token_usage"):
        counters = value.get(key)
        result[key] = (
            {
                name: counters[name] if type(counters[name]) is int else None
                for name in (
                    "input_tokens",
                    "cached_input_tokens",
                    "cache_write_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                    "total_tokens",
                )
                if name in counters
            }
            if isinstance(counters, dict)
            else None
        )
    return result

def _usage_capture(
    source_root: Path,
    artifact: str,
    session_id: str,
    *,
    include_incomplete: bool = False,
    loopx_input_json: str | None = None,
    input_goal_id: str | None = None,
) -> dict:
    data = _source_path(source_root, artifact).read_bytes()
    events = []
    # Transport only the provider's counter/lifecycle fields, not model text,
    # account metadata or tool outputs. TS owns all accounting decisions.
    for line_number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("Usage capture contains a non-object event")
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError("Usage capture contains a non-object payload")
        if event.get("type") == "session_meta":
            payload = {k: payload[k] for k in ("id", "forked_from_id") if k in payload}
        elif event.get("type") == "token_usage_record":
            payload = _request_usage_record(payload)
        elif event.get("type") == "loopx_input_token_attribution":
            # Optional host measurement, not a tokenizer or prose classifier.
            # Preserve only binding/counts; raw prompts and packet text stay private.
            payload = {
                key: payload[key] if isinstance(payload[key], str) else None
                for key in (
                    "method", "thread_id", "session_id", "turn_id",
                    "root_turn_id", "response_id",
                ) if key in payload
            } | {
                key: payload[key] if type(payload[key]) is int else None
                for key in (
                    "input_tokens", "loopx_input_tokens", "loopx_cached_input_tokens",
                ) if key in payload
            }
        elif event.get("type") == "compacted":
            payload = {
                "compaction_response_id": payload.get("compaction_response_id")
                if isinstance(payload.get("compaction_response_id"), str)
                else None,
                "latest_token_usage_record": _request_usage_record(
                    payload.get("latest_token_usage_record")
                ),
            }
        elif event.get("type") == "event_msg" and payload.get("type") in {
            "task_started",
            "task_complete",
            "token_count",
        }:
            payload = {
                k: payload[k] for k in ("type", "turn_id", "info") if k in payload
            }
            if isinstance(payload.get("info"), dict):
                payload["info"] = {
                    name: {
                        key: value[key]
                        for key in (
                            "input_tokens",
                            "cached_input_tokens",
                            "output_tokens",
                        )
                        if key in value
                    }
                    if isinstance(value, dict)
                    else value
                    for name in ("total_token_usage", "last_token_usage")
                    if (value := payload["info"].get(name)) is not None
                }
        else:
            continue
        events.append(
            {
                "line": line_number,
                "type": event["type"],
                "timestamp": event.get("timestamp"),
                "payload": payload,
            }
        )
    observation = None
    input_artifacts = []
    if loopx_input_json is not None:
        from .trajectory_input_capture import load_codex_input_observation
        try:
            observation = load_codex_input_observation(source_root, loopx_input_json)
            input_artifacts = observation.pop("artifacts")
        except (ImportError, OSError, ValueError, KeyError, TypeError):
            # A supplementary observation cannot erase independently valid
            # native counters. The typed owner withholds only input attribution.
            observation = {"error": "Input observation or tokenizer is unavailable"}
    projection = effect_runtime_result(
        "benchmark.trajectory.codex-usage",
        {
            "events": events,
            "session_id": session_id,
            "include_incomplete": include_incomplete,
            **({"loopx_input_trace": observation, "input_goal_id": input_goal_id}
               if loopx_input_json is not None else {}),
        },
        large_local_snapshot=True,
    )
    return {
        "artifact": artifact,
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
        "projection": projection,
        **({"input_artifacts": input_artifacts} if input_artifacts else {}),
    }


def _captured_view(record: dict, field: str) -> dict | None:
    captured = record.get(field)
    if captured is None:
        return None
    reason = None
    try:
        data = _source_path(
            Path(record["source_root"]), captured["artifact"]
        ).read_bytes()
        if hashlib.sha256(data).hexdigest() != captured["artifact_sha256"]:
            reason = "source_changed"
        for artifact in captured.get("input_artifacts", []):
            data = _source_path(Path(record["source_root"]), artifact["artifact"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != artifact["artifact_sha256"]:
                reason = "source_changed"
    except (OSError, ValueError):
        reason = "source_unavailable"
    return {
        **captured,
        "applicability": "historical" if reason else "current",
        "reason_code": reason,
    }


def _index(owner: Path) -> dict:
    path = owner / "packs.json"
    if not path.exists():
        return {"default_pack_id": None, "pack_ids": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    for pack_id in value["pack_ids"]:
        _opaque(pack_id)
    if value["default_pack_id"] not in value["pack_ids"]:
        raise PackUnavailable("invalid_registration")
    return value


def _resolve(
    runtime_root: Path, owner_goal_id: str, pack_id: str | None
) -> tuple[Path, dict]:
    owner = _owner(runtime_root, owner_goal_id)
    index = _index(owner)
    pack_id = pack_id or index["default_pack_id"]
    if not pack_id or _opaque(pack_id) not in index["pack_ids"]:
        raise PackUnavailable("pack_not_registered")
    path = owner / "packs" / f"{pack_id}.json"
    if path.is_symlink():
        raise PackUnavailable("invalid_registration")
    record = json.loads(path.read_text(encoding="utf-8"))
    if (
        record["owner_goal_id"] != owner_goal_id
        or _pack_id(owner_goal_id, record) != pack_id
    ):
        raise PackUnavailable("invalid_registration")
    return owner, record


def _availability(record: dict, prepared: dict) -> str | None:
    if prepared["analysis_key"] != record["analysis_key"]:
        return "method_changed"
    try:
        _verify_sources(record)
    except PackUnavailable as exc:
        return exc.reason_code
    except (OSError, UnicodeError):
        return "source_unavailable"
    return None


def _summary(record: dict, reason: str | None, prepared: dict | None = None) -> dict:
    analysis = prepared or record
    return {
        "pack_id": _pack_id(record["owner_goal_id"], record),
        "title": record["title"],
        "scope": record["input"]["scope"],
        "selection": record["input"]["selection"],
        "episode_count": len(record["input"]["episodes"]),
        "source_count": len(record["input"]["sources"]),
        "analysis_key": analysis["analysis_key"],
        "method_version": analysis["method_version"],
        "config": analysis["config"],
        "availability": "unavailable" if reason else "available",
        "reason_code": reason,
        **({field: _captured_view(record, field) for field in ("solver_usage", "native_outcomes") if field in record}),
    }


def register_pack(
    *,
    runtime_root: Path,
    owner_goal_id: str,
    input_path: Path,
    source_root: Path,
    title: str,
    config: dict | None = None,
    usage_jsonl: str | None = None,
    usage_session_id: str | None = None,
    include_incomplete_usage: bool = False,
    loopx_input_json: str | None = None,
    native_outcomes_json: str | None = None,
    base_pack_id: str | None = None,
) -> dict:
    """Trusted operator only; no HTTP/renderer path-registration endpoint."""
    if not title.strip():
        raise ValueError("A case/window title is required")
    prepared = effect_runtime_result(
        "benchmark.trajectory.prepare",
        {
            "input": json.loads(input_path.read_text(encoding="utf-8")),
            "config": config or DEFAULT_CONFIG,
        },
    )
    record = {
        "owner_goal_id": owner_goal_id,
        "title": title,
        "source_root": str(source_root.resolve(strict=True)),
        **{
            k: prepared[k]
            for k in ("input", "config", "analysis_key", "method_version")
        },
    }
    _verify_sources(record)
    if base_pack_id is not None:
        _, previous = _resolve(runtime_root, owner_goal_id, base_pack_id)
        # The typed owner validates Goal/case/run identity before registration.
        # This merely links frozen versions; registration launches no analysis.
        effect_runtime_result("benchmark.trajectory.continuation", {
            **prepared, "previous": _prepared(previous),
        })
        record["base_pack_id"] = _opaque(base_pack_id)
    if include_incomplete_usage and usage_jsonl is None:
        raise ValueError("include-incomplete-usage requires a usage capture")
    if loopx_input_json is not None and usage_jsonl is None:
        raise ValueError("LoopX input observation requires a usage capture")
    if (usage_jsonl is None) != (usage_session_id is None):
        raise ValueError(
            "Usage capture and expected session identity must be supplied together"
        )
    if usage_jsonl is not None:
        record["solver_usage"] = _usage_capture(
            source_root, usage_jsonl, usage_session_id,
            include_incomplete=include_incomplete_usage,
            loopx_input_json=loopx_input_json,
            input_goal_id=record["input"]["scope"]["goal_id"],
        )
    if native_outcomes_json is not None:
        data = _source_path(source_root, native_outcomes_json).read_bytes()
        measured = effect_runtime_result("benchmark.trajectory.prepare", {
            "input": record["input"], "config": record["config"],
            "native_outcomes": json.loads(data),
        })["native_outcomes"]
        record["native_outcomes"] = {
            "artifact": native_outcomes_json,
            "artifact_sha256": hashlib.sha256(data).hexdigest(),
            "projection": measured,
        }
    owner = _owner(runtime_root, owner_goal_id)
    (owner / "packs").mkdir(parents=True, exist_ok=True, mode=0o700)
    pack_id = _pack_id(owner_goal_id, record)
    with exclusive_file_lock(owner / "packs.lock"):
        index = _index(owner)
        path = owner / "packs" / f"{pack_id}.json"
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8")) != record:
                raise PackUnavailable("registration_conflict")
        else:
            _write(path, record)
            path.chmod(0o600)
        if pack_id not in index["pack_ids"]:
            index["pack_ids"].append(pack_id)
        index["default_pack_id"] = pack_id
        _write(owner / "packs.json", index)
    return {"ok": True, **_summary(record, None)}


def discover(*, runtime_root: Path, owner_goal_id: str, output_language: str | None = None) -> dict:
    owner = _owner(runtime_root, owner_goal_id)
    index = _index(owner)
    packs = []
    unreadable = []
    for pack_id in index["pack_ids"]:
        try:
            _, record = _resolve(runtime_root, owner_goal_id, pack_id)
            prepared = _prepared(record)
            packs.append(_summary(record, _availability(record, prepared),
                _prepared(record, output_language) if output_language is not None else None))
        except (OSError, ValueError, KeyError, TypeError, EffectRuntimeRejected):
            unreadable.append(pack_id)
    available = any(p["availability"] == "available" for p in packs)
    return {
        "ok": True,
        "availability": "available" if available else "unavailable",
        "reason_code": None if available else "no_available_pack",
        "default_pack_id": index["default_pack_id"],
        "packs": packs,
        **({"unreadable_pack_ids": unreadable} if unreadable else {}),
    }


def _job(owner: Path, record: dict, analysis_key: str) -> Path:
    if _opaque(analysis_key) != record["analysis_key"]:
        raise PackUnavailable("analysis_key_mismatch")
    directory = owner / "jobs" / analysis_key
    if directory.is_symlink():
        raise PackUnavailable("invalid_job_path")
    return directory


def _public_job(job: dict) -> dict:
    # Provider stderr, machine paths and process IDs are host diagnostics only.
    fields = (
        "schema_version",
        "ok",
        "status",
        "analysis_key",
        "method_version",
        "scope",
        "config",
        "attempt",
        "started_at_unix",
        "analysis_wall_seconds",
        "usage",
        "served_service_tier",
        "reused",
        "cancel_requested",
        "reused_from_analysis_key",
    )
    result = {k: job[k] for k in fields if k in job}
    if job.get("error"):
        result["error"] = (
            "Analysis did not complete; retry or inspect host diagnostics."
        )
    return result


def inspect_pack(
    *, runtime_root: Path, owner_goal_id: str, pack_id: str | None = None,
    output_language: str | None = None,
    include_continuation: bool = True,
) -> dict:
    owner, record = _resolve(runtime_root, owner_goal_id, pack_id)
    prepared = _prepared(record)
    reason = _availability(record, prepared)
    if output_language is not None:
        prepared = _prepared(record, output_language)
    identity = prepared if output_language is not None else record
    directory = _job(owner, identity, identity["analysis_key"])
    job = read_job(directory) if (directory / "receipt.json").exists() else None
    result_job, result_directory = job, directory
    if job and job["status"] != "succeeded" and prepared.get("display_request"):
        # An explicit translation retry does not erase its saved partial
        # result. Read only an exact-key, successful prior attempt; current job
        # state and cost remain separate and visible to the reader.
        for archived in sorted(directory.glob("attempt-*/receipt.json"), key=lambda path: int(path.parent.name.split("-")[1]), reverse=True):
            prior = read_job(archived.parent)
            if prior["status"] == "succeeded":
                result_job, result_directory = prior, archived.parent
                break
    result = None
    if job is not None:
        if (
            job["analysis_key"] != identity["analysis_key"]
            or job["scope"] != record["input"]["scope"]
            or job["config"] != prepared["config"]
        ):
            raise PackUnavailable("receipt_mismatch")
        if result_job and result_job["status"] == "succeeded":
            if (result_job["analysis_key"] != identity["analysis_key"] or result_job["scope"] != record["input"]["scope"]
                    or result_job["config"] != prepared["config"]):
                raise PackUnavailable("receipt_mismatch")
            if reason == "method_changed":
                # Read a previously qualified artifact without applying today's
                # rubric. The old receipt must bind its frozen input and bytes.
                payload = (result_directory / "assessment.json").read_bytes()
                if (
                    result_job.get("input_sha256") != _digest(record["input"])
                    or result_job.get("assessment_sha256")
                    != hashlib.sha256(payload).hexdigest()
                ):
                    raise PackUnavailable("historical_result_unverified")
                result = json.loads(payload)
                if (
                    result.get("schema_version") != "trajectory_assessment_v1"
                    or result.get("analysis_key") != identity["analysis_key"]
                    or result.get("scope") != record["input"]["scope"]
                    or result.get("method_version") != record["method_version"]
                    or result_job.get("method_version") != record["method_version"]
                ):
                    raise PackUnavailable("historical_result_unverified")
            else:
                result = effect_runtime_result(
                    "benchmark.trajectory.qualify",
                    {
                        **prepared,
                        "output": json.loads(
                            (result_directory / "model-output.json").read_text(
                                encoding="utf-8"
                            )
                        ),
                    },
                )
    view = {
        "ok": True,
        **_summary(record, reason, prepared if output_language is not None else None),
        "objective": record["input"]["objective"],
        "episodes": record["input"]["episodes"],
        "job": _public_job(job) if job else None,
        "result": result,
        **({"display_request": prepared["display_request"]} if "display_request" in prepared else {}),
        **({"result_attempt": result_job["attempt"]} if prepared.get("display_request") and result is not None else {}),
        **({"result_job": _public_job(result_job)} if result is not None and result_job is not job else {}),
        "applicability": "historical" if reason else "current",
    }
    if include_continuation and record.get("base_pack_id"):
        continuation, _ = _continuation(
            runtime_root, owner_goal_id, record, prepared, result, output_language
        )
        if continuation is not None:
            view["continuation"] = continuation
        else:
            view["continuation_unavailable"] = {"base_pack_id": record["base_pack_id"]}
    return view


def _continuation(runtime_root, owner_goal_id, record, prepared, result, language):
    """Read one explicitly linked base, without recursively loading its history."""
    base_id = record.get("base_pack_id")
    if base_id is None:
        return None, None
    try:
        _, previous = _resolve(runtime_root, owner_goal_id, base_id)
        base = inspect_pack(runtime_root=runtime_root, owner_goal_id=owner_goal_id,
                            pack_id=base_id, output_language=language, include_continuation=False)
        old = _prepared(previous, language) if language is not None else _prepared(previous)
    except (OSError, ValueError, KeyError, TypeError, EffectRuntimeRejected):
        # A missing/corrupt base cannot disable a valid independently qualified
        # successor. It also cannot certify reuse or an unchanged judgment.
        return None, None
    # Changed bytes can be compared to the frozen original excerpts, but a
    # removed/unreadable source must not feed reuse or continuation context.
    readable = True
    try:
        for source in previous["input"]["sources"]:
            with _source_path(Path(previous["source_root"]), source["artifact"]).open("rb") as stream:
                stream.read(1)
    except (OSError, ValueError):
        readable = False
    usable = readable and base["result"] is not None and base["reason_code"] != "method_changed"
    prior = {**old, **({"output": base["result"]} if usable else {})}
    projection = effect_runtime_result("benchmark.trajectory.continuation", {
        **prepared, "previous": prior, **({"output": result} if result is not None else {}),
    })
    return {
        **projection,
        "base_pack_id": base_id, "base_title": base["title"],
        "base_analysis_key": base["analysis_key"], "base_available": usable,
        "base_episode_count": base["episode_count"],
    }, prior if projection["can_reuse"] else None


def execute_pack(
    *,
    runtime_root: Path,
    owner_goal_id: str,
    pack_id: str,
    analysis_key: str,
    output_language: str | None = None,
    retry: bool = False,
    timeout_seconds: int = 900,
    codex: str = "codex",
) -> dict:
    owner, record = _resolve(runtime_root, owner_goal_id, pack_id)
    base = _prepared(record)
    prepared = _prepared(record, output_language) if output_language is not None else base
    _job(owner, prepared if output_language is not None else record, analysis_key)
    reason = _availability(record, base)
    if reason:
        raise PackUnavailable(reason)
    _, reuse = _continuation(runtime_root, owner_goal_id, record, prepared, None, output_language)
    if retry and prepared.get("display_request"):
        reuse = None  # Explicitly completing a partial translation needs a new attempt.
    return _public_job(
        execute(
            prepared,
            owner / "jobs",
            retry=retry,
            timeout_seconds=timeout_seconds,
            codex=codex,
            **({"reuse_from": reuse} if reuse is not None else {}),
        )
    )


def cancel_pack(
    *, runtime_root: Path, owner_goal_id: str, pack_id: str, analysis_key: str,
    output_language: str | None = None,
) -> dict:
    owner, record = _resolve(runtime_root, owner_goal_id, pack_id)
    identity = _prepared(record, output_language) if output_language is not None else record
    return _public_job(request_cancel(_job(owner, identity, analysis_key)))


def read_citation(
    *,
    runtime_root: Path,
    owner_goal_id: str,
    pack_id: str,
    analysis_key: str,
    source_id: str,
    first_line: int,
    last_line: int,
    output_language: str | None = None,
) -> dict:
    _, record = _resolve(runtime_root, owner_goal_id, pack_id)
    if analysis_key != (_prepared(record, output_language) if output_language is not None else record)["analysis_key"]:
        raise PackUnavailable("analysis_key_mismatch")
    view = inspect_pack(
        runtime_root=runtime_root, owner_goal_id=owner_goal_id, pack_id=pack_id,
        output_language=output_language,
    )
    result = view["result"]
    citations = [c for a in (result or {}).get("assessments", []) for c in a["citations"]]
    native = (view.get("native_outcomes") or {}).get("projection") or {}
    native_citations = [native[key]["citation"] for key in ("initial", "best_valid", "retained", "final_valid") if native.get(key)]
    improvement = native.get("retained_improvement") or {}
    if improvement.get("baseline"):
        native_citations.append(improvement["baseline"]["citation"])
    if improvement.get("retention_citation"):
        native_citations.append(improvement["retention_citation"])
    requested = {"source_id": source_id, "first_line": first_line, "last_line": last_line}
    if not any(
        c["source_id"] == source_id
        and c["first_line"] == first_line
        and c["last_line"] == last_line
        for c in [*citations, *native_citations]
    ):
        raise PackUnavailable("citation_not_in_result")
    source = next(s for s in record["input"]["sources"] if s["id"] == source_id)
    lines = source["text"].split("\n")
    return {
        "source_id": source_id,
        "artifact": source["artifact"],
        "first_line": first_line,
        "last_line": last_line,
        "text": "\n".join(
            lines[
                first_line - source["first_line"] : last_line - source["first_line"] + 1
            ]
        ),
        "applicability": "historical" if requested in native_citations
        and view["native_outcomes"]["applicability"] == "historical" else view["applicability"],
    }
