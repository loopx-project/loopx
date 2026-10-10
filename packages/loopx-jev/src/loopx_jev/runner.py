"""One bounded D1 assessment outside all core transactions."""

from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import time
from typing import Any, Callable
from .config import Config, read_json
from .protocol import request_bytes
from .progress import build_request, decode_assessment, QUESTION_VERSION
from .store import RunStore
from .transport import TransportFailure, send

SECRET = re.compile(
    r"apikey_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9_-]{24,}"
)


def read_basis(
    path: Path, workspace: Path
) -> tuple[dict[str, Any], Callable[[], bool]]:
    """Read operator-supplied criterion plus exact local evidence; no remote dereference."""
    manifest, manifest_hash = read_json(path, 32768)
    allowed = {
        "goal_id",
        "objective",
        "acceptance",
        "non_goals",
        "horizon",
        "evidence",
        "already_known",
        "acceptance_scope",
    }
    if not isinstance(manifest, dict) or set(manifest) - allowed:
        raise ValueError("unknown basis fields")
    if (
        not isinstance(manifest.get("objective"), str)
        or not manifest["objective"].strip()
    ):
        raise ValueError("an explicit operator objective is required")
    if (
        not isinstance(manifest.get("acceptance"), list)
        or not manifest["acceptance"]
        or any(not isinstance(x, str) or not x.strip() for x in manifest["acceptance"])
    ):
        raise ValueError("explicit acceptance criteria are required")
    references = manifest.get("evidence", [])
    if not isinstance(references, list) or len(references) > 8:
        raise ValueError("at most eight local evidence references")
    observations, checks = [], []
    total = 0
    for item in references:
        if (
            not isinstance(item, dict)
            or set(item) - {"ref", "description"}
            or not isinstance(item.get("ref"), str)
        ):
            raise ValueError(
                "evidence requires a relative ref, not a claimed observation"
            )
        relative = Path(item["ref"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("evidence escapes the selected workspace")
        target = workspace / relative
        if (
            target.is_symlink()
            or not target.resolve().is_relative_to(workspace.resolve())
            or not target.is_file()
        ):
            raise ValueError("evidence must be a regular workspace file")
        with target.open("rb") as stream:
            raw = stream.read(32769)
        total += len(raw)
        if total > 32768:
            raise ValueError("evidence byte budget exceeded")
        digest = hashlib.sha256(raw).hexdigest()
        observations.append(
            {
                "ref": item["ref"],
                "sha256": digest,
                "text": raw.decode("utf-8"),
                "origin": "host_file_read",
                "description": item.get("description", ""),
            }
        )
        checks.append((target, digest))
    from loopx.control_plane.effect_runtime import effect_runtime_result, EffectRuntimeRejected

    scope = manifest.get("acceptance_scope")
    def criterion_basis():
        requirements = None
        requested = None
        if scope is not None:
            from loopx.control_plane.goals.acceptance import inspect_goal_acceptance
            if not isinstance(scope, dict) or set(scope) != {
                "registry_ref", "runtime_ref", "agent_id", "todo_id", "criterion_ids"
            }:
                raise ValueError("explicit current task acceptance scope required")
            def local_reference(key):
                value = scope[key]
                if not isinstance(value, str) or not value:
                    raise ValueError("local acceptance reference required")
                relative = Path(value)
                target = workspace / relative
                if relative.is_absolute() or ".." in relative.parts or target.is_symlink() or not target.resolve().is_relative_to(workspace.resolve()):
                    raise ValueError("acceptance reference escapes the selected workspace")
                return target
            current = inspect_goal_acceptance(registry_path=local_reference("registry_ref"),
                runtime_root=str(local_reference("runtime_ref")), goal_id=manifest.get("goal_id"),
                agent_id=scope["agent_id"], todo_id=scope["todo_id"])
            requirements = current.get("completion_requirements")
            if requirements is None:
                raise ValueError("current task has no canonical acceptance criteria")
            requested = scope["criterion_ids"]
        try:
            return effect_runtime_result("progress_review.criterion_basis", {
                "acceptance": manifest["acceptance"], "requirements": requirements, "criterion_ids": requested,
                "goal_id": manifest.get("goal_id"), "agent_id": scope.get("agent_id") if scope else None,
            })
        except EffectRuntimeRejected as exc:
            # Older independently installed cores still support manual studies.
            # Keep their legacy shape; never fabricate a canonical binding or
            # bypass a current core's validation/IO failure.
            if scope is None and exc.diagnostic_code == "unsupported_method":
                return {"acceptance": manifest["acceptance"]}
            raise
    selected = criterion_basis()
    basis = {
        **manifest,
        "evidence": observations,
        "basis_origin": "explicit_operator_study_basis_not_completion_authority",
        "acceptance": selected["acceptance"],
        **({"criterion_binding": selected["binding"]} if "binding" in selected else {}),
    }

    def current() -> bool:
        try:
            if read_json(path, 32768)[1] != manifest_hash:
                return False
            if scope is not None and criterion_basis() != selected:
                return False
            for target, digest in checks:
                if target.is_symlink() or not target.resolve().is_relative_to(
                    workspace.resolve()
                ):
                    return False
                with target.open("rb") as stream:
                    raw = stream.read(32769)
                if len(raw) > 32768 or hashlib.sha256(raw).hexdigest() != digest:
                    return False
            return True
        except (OSError, ValueError, RuntimeError):
            return False

    return basis, current


def assess_one(
    snapshot: dict[str, Any],
    basis: dict[str, Any],
    config: Config,
    store: RunStore,
    guard: Callable[[], bool],
    transport: Callable[..., dict[str, Any]] = send,
    credential: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    started = previous = time.perf_counter_ns()
    timings: dict[str, int] = {}

    def mark(name: str) -> None:
        nonlocal previous
        now = time.perf_counter_ns()
        timings[name] = now - previous
        previous = now

    result: dict[str, Any] = {
        "status": "not_evaluated",
        "dispatch": "not_sent",
        "usage": None,
        "cost_usd": None,
        "assessment_timing_ns": timings,
    }
    if config.mode == "off":
        return {**result, "reason": "disabled"}
    if not config.allow_egress:
        return {**result, "reason": "egress_denied"}
    if not guard():
        return {**result, "reason": "revoked_or_stale"}
    mark("eligibility_guard")
    try:
        request = build_request(snapshot, basis, config.model)
        raw = request_bytes(request)
    except (ValueError, KeyError, TypeError, AttributeError):
        return {**result, "reason": "invalid_progress_evidence"}
    if len(raw) > config.max_request_bytes:
        return {**result, "reason": "request_too_large"}
    if SECRET.search(raw.decode("utf-8")):
        return {**result, "reason": "credential_like_material_rejected"}
    key = credential() if credential is not None else os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return {**result, "reason": "missing_key"}
    request_id = hashlib.sha256(
        request_bytes(
            {
                "request": request,
                "version": QUESTION_VERSION,
                "configuration": config.generation,
            }
        )
    ).hexdigest()
    mark("request_preparation")
    try:
        previous_result = store.reserve(request_id, config.max_requests_per_run)
    except (OSError, ValueError):
        return {**result, "reason": "attempt_store_unavailable"}
    mark("reservation")
    if previous_result is not None:
        if previous_result.get("request_id") == request_id and previous_result.get(
            "response"
        ):
            try:
                assessment = decode_assessment(
                    previous_result["response"],
                    snapshot,
                    config.model,
                    config.minimum_label_probability,
                )
            except (ValueError, KeyError, TypeError):
                return {**result, "reason": "invalid_cached_result"}
            if not guard():
                return {
                    **result,
                    "status": "stale",
                    "reason": "revoked_or_stale_on_replay",
                }
            mark("replay_decode_and_guard")
            return {
                **previous_result,
                "assessment": assessment,
                "replayed": True,
                "cached_provider_measurements": True,
                "assessment_timing_ns": timings,
                "assessment_total_ns": time.perf_counter_ns() - started,
            }
        return {
            **result,
            "reason": previous_result.get("status", "prior_attempt_unresolved"),
            "dispatch": previous_result.get("dispatch", "may_have_been_sent"),
            "replayed": True,
        }
    result.update(
        request_id=request_id,
        question_version=QUESTION_VERSION,
        requested_model=config.model,
        config_generation=config.generation,
        input_bytes=len(raw),
        execution_kind=(
            "live_provider"
            if transport is send
            else str(getattr(transport, "execution_kind", "") or "fixture_injected")
        ),
    )
    try:
        if not guard():
            result["reason"] = "revoked_before_send"
        else:
            mark("pre_dispatch_guard")
            try:
                envelope = transport(request, config, key)
            finally:
                mark("transport_inclusive")
            for field in ("transport_timing_ns", "worker_timing_ns"):
                measured = envelope.get(field)
                if isinstance(measured, dict) and all(
                    isinstance(v, int) and not isinstance(v, bool) and v >= 0
                    for v in measured.values()
                ):
                    result[field] = measured
            result["dispatch"] = "response_received"
            response = envelope["response"]
            assessment = decode_assessment(
                response, snapshot, config.model, config.minimum_label_probability
            )
            result["actual_model"] = config.model
            result["response"] = {
                "model": config.model,
                "answers": {
                    name: {
                        k: answer[k]
                        for k in ("type", "choice", "probabilities", "confidence", "noul")
                        if k in answer
                    }
                    for name, answer in response["answers"].items()
                },
            }
            usage = response.get("usage")
            if isinstance(usage, dict):
                result["usage"] = {
                    k: v
                    for k, v in usage.items()
                    if k in {"input_tokens", "output_tokens"}
                    and isinstance(v, int)
                    and not isinstance(v, bool)
                    and v >= 0
                }
            mark("response_validation")
            if not guard():
                result.update(status="stale", reason="revoked_or_stale_after_response")
            else:
                decided = assessment["coverage"]["decided"]
                result.update(
                    status="completed" if decided else "abstained",
                    assessment=assessment,
                    reason=None if decided else "insufficient_evidence_or_uncertain",
                )
    except TransportFailure as exc:
        result.update(status="failed", reason=exc.code, dispatch=exc.dispatch)
    except (ValueError, TypeError, KeyError, OSError):
        result.update(
            status="failed",
            reason="invalid_response_or_local_io",
            dispatch="may_have_been_sent"
            if result["dispatch"] == "not_sent"
            else result["dispatch"],
        )
    mark("completion_or_failure")
    try:
        store.finish(request_id, result)
    except (OSError, ValueError):
        result.update(status="failed", reason="attempt_result_unavailable")
        result.pop("assessment", None)
    mark("result_write")
    result["assessment_total_ns"] = time.perf_counter_ns() - started
    return result
