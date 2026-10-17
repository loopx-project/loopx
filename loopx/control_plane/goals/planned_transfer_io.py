"""Codec-owned destination registry IO for the typed prepared-transfer stage.

This is the internal composition seam for a verified transfer input, not a
portable-artifact verifier or an activation API. It never invokes fresh Goal
bootstrap on populated history. The source context is retained as private review
input; the typed owner installs only a stopped, unbound destination read model.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ... import __version__
from ..effect_runtime import effect_runtime_result
from ..projects.registry_codec import load_registry, project_registry_transaction


def require_goal_transfer_registry_write(
    *, registry_path: Path, before: dict[str, Any], after: dict[str, Any],
    registry_witness: dict[str, Any],
) -> None:
    """Adapt an existing codec transaction; never parse or decide fence state."""
    from ..coordination.legacy_writer_fence import legacy_coordination_writer_fence_path

    # This is an IO-only fast path. Source preparation takes the same registry
    # lock, so it cannot install a new fence between this observation and save.
    roots = {value["common_runtime_root"] for value in (before, after)
             if isinstance(value.get("common_runtime_root"), str) and Path(value["common_runtime_root"]).is_absolute()}
    goals = [goal for value in (before, after) for goal in
             (value["goals"] if isinstance(value.get("goals"), list) else [])]
    if not any(isinstance(goal, dict) and isinstance(goal.get("id"), str)
               and legacy_coordination_writer_fence_path(runtime_root=Path(root), goal_id=goal["id"]).exists()
               for root in roots for goal in goals):
        return
    if registry_witness["path"] != str(registry_path.expanduser().resolve()):
        raise ValueError("Registry transfer witness route changed")
    result = effect_runtime_result("coordination.planned_transfer.registry_write_check", {
        "before": before, "after": after, "registry_witness": registry_witness,
    })
    if result != {"allowed": True}:
        raise ValueError("Registry transfer guard did not admit this mutation")


def _read_goal_transfer_snapshot(*, registry_path: Path, goal_id: str) -> dict[str, Any]:
    """Codec/byte IO only; the native Goal owner decides supported admission."""
    from ...registry import find_registry_goal, resolve_state_file
    from ..coordination.authority_source_capture import authority_registry_source
    from .active_state_metadata import active_state_section_text
    from .activation import goal_activation_state

    registry_path = registry_path.expanduser().resolve()
    with authority_registry_source(registry_path) as witness:
        registry = load_registry(registry_path)
        goal = find_registry_goal(registry, goal_id)
        if goal is None:
            raise ValueError("Original Goal is not registered")
        state = resolve_state_file(Path(goal["repo"]), goal["state_file"])
        if state is None:
            raise ValueError("Original Goal has no state route")
        state_text = state.read_bytes().decode("utf-8")
        return {"source_context": {"goal": goal, "state_text": state_text,
            "runtime_root": registry["common_runtime_root"], "registry_path": str(registry_path)},
            "registry_witness": witness, "registry_profile": registry.get("profile_id"),
            "registry_role": registry.get("registry_role"),
            "activation_state": goal_activation_state(goal).value,
            "state_objective": active_state_section_text(state_text, "Objective")}


def read_goal_transfer_context(*, registry_path: Path, goal_id: str) -> dict[str, Any]:
    """Read codec-validated source facts; only the native owner can pin them."""
    from ..runtime.runtime_projection_route import resolve_goal_source_runtime_route

    route = resolve_goal_source_runtime_route(registry_path=registry_path, goal_id=goal_id)
    snapshot = _read_goal_transfer_snapshot(registry_path=Path(route["source_registry"]), goal_id=goal_id)
    return {key: snapshot[key] for key in ("source_context", "registry_witness")}


def preview_registered_goal_transfer(*, registry_path: Path, goal_id: str, operation_id: str) -> dict[str, Any]:
    """Resolve the normal App/CLI source and let the native owner review it.

    Do not use a runtime override to turn a global projection into source
    authority. The exact original registration and creation receipt, current
    provider revision, context and retained leases are checked by TypeScript.
    """
    context = read_goal_transfer_context(registry_path=registry_path, goal_id=goal_id)
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.preview_source", {
        "runtime_root": context["source_context"]["runtime_root"], "goal_id": goal_id,
        "operation_id": operation_id, **context,
    }, timeout=45.0, retry_safe=True)
    return result


def _read_registered_goal_transfer(*, registry_path: Path, goal_id: str) -> dict[str, Any]:
    """Private native view; resolved control routes never enter public packets."""
    context = read_goal_transfer_context(registry_path=registry_path, goal_id=goal_id)
    return effect_runtime_result("coordination.planned_transfer.inspect_operation", {
        "runtime_root": context["source_context"]["runtime_root"], "goal_id": goal_id, **context,
    }, timeout=60.0, retry_safe=True)


def _destination_activation(target: dict[str, Any], *, state: str,
    expected_state_fingerprint: str | None = None, execute: bool = False) -> dict[str, Any]:
    from .activation_service import set_goal_activation_state

    return set_goal_activation_state(registry_path=Path(target["registry_path"]),
        goal_id=target["goal_id"], state=state, runtime_root_override=target["runtime_root"],
        expected_state_fingerprint=expected_state_fingerprint,
        actor_kind="owner" if execute else None, execute=execute)


def read_goal_transfer_workspace_target(*, registry_path: Path, goal_id: str) -> dict[str, Any] | None:
    """Private first-party workspace route, never an executor/CLI redirection.

    Existing orphan handling still owns an unavailable original registry. A
    present transfer whose target cannot be verified must never fall back.
    The typed transfer owner alone decides whether a destination was admitted.
    """
    from ..runtime.runtime_projection_route import resolve_goal_source_runtime_route

    try:
        route = resolve_goal_source_runtime_route(registry_path=registry_path, goal_id=goal_id)
    except (OSError, ValueError):
        # Source lookup has not established any transfer. Preserve the existing
        # lifecycle owner's explicit orphan-stop refusal/recovery rules. Native
        # cutover/target proof below is deliberately outside this fallback.
        return None
    return effect_runtime_result("coordination.planned_transfer.workspace_target", {
        "registry_path": route["source_registry"], "runtime_root": route["source_runtime_root"],
        "goal_id": goal_id,
    }, timeout=60.0, retry_safe=True)["target"]


def inspect_registered_goal_transfer(*, registry_path: Path, goal_id: str) -> dict[str, Any]:
    """Shared Settings/CLI transport; the native owner discovers preparation."""
    result = _read_registered_goal_transfer(registry_path=registry_path, goal_id=goal_id)
    target = result.pop("_local_activation_target", None)
    if target is not None:
        from .activation import GoalActivationState

        state = GoalActivationState(result["local_cutover"]["target_activation_state"])
        operation = "resume" if state is GoalActivationState.STOPPED else "stop"
        try:
            preview = _destination_activation(target,
                state=GoalActivationState.ACTIVE.value if operation == "resume" else GoalActivationState.STOPPED.value)
            if preview.get("ok") is not True:
                raise ValueError("Current destination lifecycle is unavailable")
            result["destination_control"] = {"operation": operation,
                "expected_state_fingerprint": preview["observed_state_fingerprint"]}
        except (OSError, ValueError):
            result["destination_control_unavailable"] = True
    review = result.pop("_local_cutover_review", None)
    if review is not None:
        try:
            preview = local_goal_transfer_cutover(registry_path=registry_path, goal_id=goal_id,
                operation_id=review["operation_id"], destination_registry=Path(review["target_registry_path"]),
                continuation="reviewed_context", reviewed_preview_sha256=review["preview_sha256"],
                continue_todo_id=review.get("continue_todo_id"))
        except Exception:
            # Only this read-only destination observation failed. Preserve the
            # native source view/cancellation, including after a browser reload;
            # no cached review becomes a confirmation or an execution grant.
            result["prepared_cutover_unavailable"] = True
        else:
            result["prepared_cutover"] = preview["confirmation"]
    return result


def control_transferred_goal(*, registry_path: Path, goal_id: str, operation_id: str,
    operation: str, expected_state_fingerprint: str) -> dict[str, Any]:
    """Owner-only UI transport to the original destination's lifecycle owner.

    This does not redirect callers or old executors. Both the native cutover
    binding and the existing activation fingerprint are checked per request.
    Resuming permits future admission; it never replays a Turn or spends quota.
    """
    from .activation import GoalActivationState

    if operation not in ("stop", "resume"):
        raise ValueError("Use the existing Goal stop or resume operation")
    original = _read_registered_goal_transfer(registry_path=registry_path, goal_id=goal_id)
    target = original.get("_local_activation_target")
    if not isinstance(target, dict) or target["operation_id"] != operation_id or target["goal_id"] != goal_id:
        raise ValueError("Original admitted destination is unavailable; preserve its source")
    state = GoalActivationState.STOPPED if operation == "stop" else GoalActivationState.ACTIVE
    preview = _destination_activation(target, state=state.value)
    if preview.get("ok") is not True:
        raise ValueError("Destination lifecycle preview is unavailable")
    # A response-loss retry may observe the requested state, but it cannot
    # perform another write on stale authority or claim a new execution.
    result = preview if preview.get("changed") is False else _destination_activation(target,
        state=state.value, expected_state_fingerprint=expected_state_fingerprint, execute=True)
    if result.get("ok") is not True or result.get("readback", {}).get("verified") is not True:
        raise ValueError("Destination changed; read its current state before retrying")
    try:
        readback = inspect_registered_goal_transfer(registry_path=registry_path, goal_id=goal_id)
    except (OSError, ValueError):
        readback = None
    return {"ok": True, "goal_id": goal_id, "operation_id": operation_id,
        "after_state": state.value, "changed": result["changed"], "readback": readback}


def cancel_registered_goal_transfer(*, registry_path: Path, goal_id: str, operation_id: str) -> dict[str, Any]:
    """An address is not a grant; cancellation checks the retained native owner."""
    context = read_goal_transfer_context(registry_path=registry_path, goal_id=goal_id)
    return cancel_prepared_goal_transfer(runtime_root=Path(context["source_context"]["runtime_root"]),
        goal_id=goal_id, operation_id=operation_id)


def local_goal_transfer_cutover(
    *, registry_path: Path, goal_id: str, operation_id: str, destination_registry: Path,
    expected_preview_sha256: str | None = None, continuation: str | None = None,
    reviewed_preview_sha256: str | None = None, continue_todo_id: str | None = None,
) -> dict[str, Any]:
    """One source-online local cutover; native owners decide every transition.

    No copied receipt authorizes this call. Both local registrations must be
    readable through their actual codec. A final source decision has no reverse
    compensation; same-operation recovery preserves the stopped destination.
    """
    request, snapshot, before = _local_cutover_request(registry_path, goal_id, operation_id, destination_registry, continuation)
    if continue_todo_id is not None:
        request["continue_todo_id"] = continue_todo_id
    destination_registry = destination_registry.expanduser().absolute()
    if reviewed_preview_sha256 is not None:
        request["reviewed_preview_sha256"] = reviewed_preview_sha256
    if expected_preview_sha256 is not None:
        request.update(execute=True, expected_preview_sha256=expected_preview_sha256)
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.plan_local_cutover", request,
        timeout=300.0, retry_safe=False)
    proposed = result.pop("destination_registry")
    if expected_preview_sha256 is None:
        return {"ok": True, **result}
    if result.pop("publish_registry"):
        with project_registry_transaction(destination_registry, operation="planned_transfer_admission_publish") as transaction:
            if transaction.payload_copy() != before:
                raise ValueError("Destination registry changed after source-final; retry the original cutover without reverting later work")
            transaction.commit(proposed)
            if load_registry(destination_registry) != proposed:
                raise ValueError("Target admission registration readback failed; recover the same cutover")
    admitted: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.finish_local_cutover", {
        "runtime_root": snapshot["source_context"]["runtime_root"], "goal_id": goal_id, "operation_id": operation_id,
    }, timeout=300.0, retry_safe=False)
    return {"ok": True, **result, **admitted, "registry_readback_verified": True}


def _local_cutover_request(registry_path: Path, goal_id: str, operation_id: str,
    destination_registry: Path, continuation: str | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    destination_registry = destination_registry.expanduser().absolute()
    context = read_goal_transfer_context(registry_path=registry_path, goal_id=goal_id)
    snapshot = _read_goal_transfer_snapshot(registry_path=destination_registry, goal_id=goal_id)
    before = load_registry(destination_registry)
    request: dict[str, Any] = {"source": {**context, "runtime_root": context["source_context"]["runtime_root"],
        "goal_id": goal_id, "operation_id": operation_id}, "target_snapshot": snapshot, "target_registry": before}
    if continuation is not None:
        request["continuation"] = continuation
    return request, snapshot, before


def prepare_local_goal_transfer(*, registry_path: Path, goal_id: str, operation_id: str,
    destination_root: Path, materials: list[Path], continue_todo_id: str | None = None) -> dict[str, Any]:
    """Fixed IO composition of existing Goal, custody and registry owners.

    The Agent supplies an explicit reviewed file selection. TypeScript chooses
    paths/provider and every business transition; this adds no Python policy.
    Preparation stops the source but never performs the final confirmation.
    """
    context = read_goal_transfer_context(registry_path=registry_path, goal_id=goal_id)
    plan = effect_runtime_result("coordination.planned_transfer.plan_local_preparation", {
        **context, "runtime_root": context["source_context"]["runtime_root"], "goal_id": goal_id,
        "operation_id": operation_id, "destination_root": str(destination_root.expanduser().absolute()),
        "materials": [str(path.expanduser().absolute()) for path in materials],
        **({"continue_todo_id": continue_todo_id} if continue_todo_id is not None else {}),
    }, timeout=60.0, retry_safe=True)
    recipient = create_planned_transfer_recipient(destination=Path(plan["recipient_directory"]))
    exported = export_registered_goal_transfer(registry_path=registry_path, goal_id=goal_id, operation_id=operation_id,
        expected_preview_sha256=plan["source_preview_sha256"], recipient=recipient, destination=Path(plan["artifact_directory"]),
        materials=[(Path(row["path"]), row["sha256"]) for row in plan["materials"]])
    target = dict(directory=Path(plan["artifact_directory"]), private_key=Path(plan["recipient_directory"]) / "private-key.pem",
        expected_artifact_sha256=exported["protected_artifact"]["artifact_sha256"],
        project=Path(plan["project"]), runtime_root=Path(plan["runtime_root"]), provider=plan["provider"])
    workspace = preview_protected_goal_transfer_target(**target)
    stage_protected_goal_transfer(**target, expected_workspace_preview_sha256=workspace["reviewed_preview_sha256"])
    request, _, _ = _local_cutover_request(registry_path, goal_id, operation_id,
        Path(plan["project"]) / ".loopx/registry.json", "reviewed_context")
    request["execution_repository"] = plan["repository"]
    if "continue_todo_id" in plan:
        request["continue_todo_id"] = plan["continue_todo_id"]
    effect_runtime_result("coordination.planned_transfer.retain_local_cutover_review", request,
        timeout=300.0, retry_safe=False)
    return inspect_registered_goal_transfer(registry_path=registry_path, goal_id=goal_id)


def confirm_registered_goal_transfer(*, registry_path: Path, goal_id: str, operation_id: str,
    expected_preview_sha256: str, codex_bin: str | None = None, codex_home: Path | None = None) -> dict[str, Any]:
    """Confirm/recover the retained original review, never a browser path."""
    context = read_goal_transfer_context(registry_path=registry_path, goal_id=goal_id)
    review = effect_runtime_result("coordination.planned_transfer.read_local_cutover_review", {
        **context, "runtime_root": context["source_context"]["runtime_root"], "goal_id": goal_id, "operation_id": operation_id,
    }, timeout=60.0, retry_safe=True)
    local_goal_transfer_cutover(registry_path=registry_path, goal_id=goal_id, operation_id=operation_id,
        destination_registry=Path(review["target_registry_path"]), continuation="reviewed_context",
        expected_preview_sha256=expected_preview_sha256, reviewed_preview_sha256=review["preview_sha256"],
        continue_todo_id=review.get("continue_todo_id"))
    execution = None
    if review.get("continue_todo_id"):
        # The accepted cutover remains recorded even if the current Host cannot
        # finish. The same native Turn journal owns recovery, never a new job.
        try:
            execution = _continue_transferred_work(registry_path=Path(review["target_registry_path"]),
                goal_id=goal_id, operation_id=operation_id, codex_bin=codex_bin, codex_home=codex_home)
        except Exception:
            execution = {"ok": False, "reason_code": "original_work_continuation_unavailable"}
    # A lost later read cannot turn the already accepted cutover into failure.
    try:
        readback = inspect_registered_goal_transfer(registry_path=registry_path, goal_id=goal_id)
    except Exception:
        readback = None
    return {"ok": True, "goal_id": goal_id, "recorded": True, "operation_id": operation_id, "readback": readback,
        **({"execution": execution} if execution is not None else {})}


def _continue_transferred_work(*, registry_path: Path, goal_id: str, operation_id: str,
    codex_bin: str | None, codex_home: Path | None) -> dict[str, Any]:
    """IO-only composition of the native plan, lease, Turn and validator owners.

    The mutex coordinates callers, not completion state. Exact replay belongs
    exclusively to the existing Turn journal; no Chat Session or job ledger is
    synthesized and no historic session/credential binding becomes a grant.
    """
    import os
    import subprocess
    import sys
    from ...file_lock import exclusive_cross_runtime_file_lock
    from ..turn_driver.journal_store import find_loopx_turn_key_by_settlement_identity

    context = read_goal_transfer_context(registry_path=registry_path, goal_id=goal_id)
    runtime = Path(context["source_context"]["runtime_root"])
    plan = effect_runtime_result("coordination.planned_transfer.execution_plan", {
        "runtime_root": str(runtime), "goal_id": goal_id, "operation_id": operation_id,
    }, retry_safe=True)["plan"]
    if plan is None:
        return {"ok": False, "reason_code": "original_work_not_prepared"}
    project = Path(plan["project"])
    env = dict(os.environ)
    if codex_home is not None:
        env["CODEX_HOME"] = str(codex_home)
    prefix = [sys.executable, "-I", "-m", "loopx.cli", "--format", "json",
        "--registry", str(registry_path), "--runtime-root", str(runtime)]

    def invoke(*arguments: str) -> dict[str, Any]:
        run = subprocess.run([*prefix, *arguments], cwd=project, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="strict", check=False)
        return json.loads(run.stdout)

    identity = {key: plan[key] for key in ("goal_id", "agent_id", "todo_id", "turn_instance_id")}
    key = plan["turn_instance_id"]
    with exclusive_cross_runtime_file_lock(runtime / "goals" / goal_id / "turns" / f"{key}.dispatch",
            operation="planned_transfer_original_work"):
        original = find_loopx_turn_key_by_settlement_identity(runtime, **identity)
        lease = None
        if original is None:
            args = ["task-lease", "acquire", "--goal-id", goal_id, "--todo-id", plan["todo_id"],
                "--owner", plan["agent_id"], "--idempotency-key", key, "--write-worktree", str(project),
                "--ttl-seconds", "600"]
            for scope in plan["write_scopes"]:
                args += ["--write-scope", scope]
            acquired = invoke(*args)
            if not acquired.get("ok"):
                return {"ok": False, **identity, "reason_code": "original_task_lease_unavailable"}
            lease = acquired["lease"]
        args = ["turn", "run-once", "--goal-id", goal_id, "--agent-id", plan["agent_id"],
            "--todo-id", plan["todo_id"], "--turn-instance-id", key, "--project", str(project),
            "--host", plan["host"], "--codex-bin", codex_bin or env.get("CODEX_BIN", "codex"),
            "--codex-sandbox", plan["sandbox"], "--session-scope", "agent",
            "--validation-command-json", json.dumps(plan["validation_argv"]),
            "--timeout-seconds", "90", "--no-global-sync", "--execute"]
        if lease is not None:
            args += ["--task-lease-idempotency-key", lease["idempotency_key"],
                "--task-lease-expected-version", str(lease["version"])]
        if plan["validation_timeout_seconds"] is not None:
            args += ["--validation-timeout-seconds", str(plan["validation_timeout_seconds"])]
        for capability in plan["available_capabilities"]:
            args += ["--available-capability", capability]
        result = invoke(*args)
        # A pending settlement keeps its proof for original-Turn recovery.
        # Only a successful settled Turn may release still-active work; normal
        # canonical completion has already retired its lease atomically.
        if lease is not None and result.get("ok") is True:
            current = invoke("task-lease", "inspect", "--goal-id", goal_id, "--todo-id", plan["todo_id"])
            held = current.get("lease")
            if isinstance(held, dict) and held.get("status") == "active" and held.get("idempotency_key") == key:
                released = invoke("task-lease", "release", "--goal-id", goal_id, "--todo-id", plan["todo_id"],
                    "--owner", plan["agent_id"], "--idempotency-key", key, "--expected-version", str(held["version"]))
                if not released.get("ok"):
                    raise ValueError("Original execution lease requires its owning recovery")
        return {"ok": result.get("ok") is True, **identity,
            "reason_code": None if result.get("ok") else str(result.get("error_code") or "original_work_turn_requires_recovery")}


def read_goal_transfer_host_context(*, registry_path: Path, goal_id: str,
    agent_id: str, todo_id: str) -> dict[str, Any]:
    """Read the current Goal owner's context; this IO adapter grants nothing."""
    from ..runtime.runtime_projection_route import resolve_goal_source_runtime_route

    route = resolve_goal_source_runtime_route(registry_path=registry_path, goal_id=goal_id)
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.host_context", {
        "runtime_root": route["source_runtime_root"], "goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id,
    }, timeout=60.0, retry_safe=True)
    return result


def prepare_registered_goal_transfer(
    *, registry_path: Path, goal_id: str, operation_id: str, expected_preview_sha256: str,
) -> dict[str, Any]:
    """Compose one reviewed source authority preparation, without user plumbing.

    This is still pre-cutover: Host/effect/material custody and destination
    admission are not established by a source authority fence. The complete
    product coordinator must keep those qualifications separate.
    """
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.prepare_authority",
        _registered_preparation_request(registry_path, goal_id, operation_id, expected_preview_sha256),
        timeout=300.0, retry_safe=False)
    return result


def cancel_prepared_goal_transfer(*, runtime_root: Path, goal_id: str, operation_id: str) -> dict[str, Any]:
    """Recover the retained pre-cutover operation, without rediscovering a route.

    These fields are lookup addresses, not source proof. The native Goal owner
    loads the original preparation, registry context and fence. Retried readback
    must preserve later registration changes and never issue a target grant.
    """
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.cancel_source_preparation", {
        "runtime_root": str(runtime_root.expanduser().absolute()), "goal_id": goal_id,
        "operation_id": operation_id, "execute": True,
    }, timeout=60.0, retry_safe=False)
    return result


def _registered_preparation_request(
    registry_path: Path, goal_id: str, operation_id: str, expected_preview_sha256: str,
) -> dict[str, Any]:
    context = read_goal_transfer_context(registry_path=registry_path, goal_id=goal_id)
    preview = preview_registered_goal_transfer(registry_path=registry_path, goal_id=goal_id, operation_id=operation_id)
    # The native execution compares this reviewed digest again under its actual
    # maintenance/source locks; a preview read here cannot waive stale inputs.
    return {
        "runtime_root": context["source_context"]["runtime_root"], "goal_id": goal_id,
        "operation_id": operation_id, "expected_provider_revision": preview["provider_revision"],
        "expected_writer_fence_sha256": preview["original_writer_fence_sha256"],
        "registered_source_preview_sha256": expected_preview_sha256, "execute": True, **context,
    }


def create_planned_transfer_recipient(*, destination: Path) -> dict[str, Any]:
    """Keep the private key at the recipient; only its public descriptor returns."""
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.create_recipient", {
        "destination": str(destination.expanduser().absolute()), "execute": True,
    }, timeout=60.0, retry_safe=False)
    return result


def export_registered_goal_transfer(
    *, registry_path: Path, goal_id: str, operation_id: str, expected_preview_sha256: str,
    recipient: dict[str, Any], destination: Path, materials: list[tuple[Path, str]],
) -> dict[str, Any]:
    """Pin original authority and selected bytes into a recipient-bound draft.

    This internal composition still does not qualify source Host/effect drain,
    material completeness, security review or destination admission. It avoids
    exporting a raw runtime tree or treating source config as a current grant.
    """
    request = _registered_preparation_request(registry_path, goal_id, operation_id, expected_preview_sha256)
    request.update(source_package_version=__version__, protected_transport={
        "destination": str(destination.expanduser().absolute()), "recipient": recipient,
        "materials": [{"path": str(path.expanduser().absolute()), "sha256": digest} for path, digest in materials],
    })
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.prepare_authority", request,
        timeout=300.0, retry_safe=False)
    return result


def _protected_input(directory: Path, private_key: Path, expected_artifact_sha256: str) -> dict[str, Any]:
    return {"directory": str(directory.expanduser().absolute()),
        "private_key_path": str(private_key.expanduser().absolute()),
        "expected_artifact_sha256": expected_artifact_sha256, "target_package_version": __version__}


def preview_protected_goal_transfer(
    *, directory: Path, private_key: Path, expected_artifact_sha256: str,
) -> dict[str, Any]:
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.preview_protected",
        _protected_input(directory, private_key, expected_artifact_sha256), timeout=300.0, retry_safe=True)
    return result


def stage_prepared_goal_transfer(
    *,
    source_preparation: dict[str, Any],
    source_context: dict[str, Any],
    archive: Path,
    project: Path,
    runtime_root: Path,
    provider: str,
) -> dict[str, Any]:
    """Stage the original stopped Goal and read back its exact registration.

    Native staging takes maintenance before its source/registry locks. Registry
    publication follows in a separate codec CAS, avoiding a registry/maintenance
    lock inversion with canonical writers. Interruption leaves the destination
    fenced; retry the same operation. No preimage is restored over new work.
    """
    return _stage_goal_transfer("coordination.planned_transfer.stage_target", {
        "source_preparation": source_preparation, "source_context": source_context,
        "archive": str(archive.expanduser().absolute()),
    }, project, runtime_root, provider)


def stage_protected_goal_transfer(
    *, directory: Path, private_key: Path, expected_artifact_sha256: str,
    project: Path, runtime_root: Path, provider: str,
    expected_workspace_preview_sha256: str | None = None,
    material_destinations: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Consume only destination-local protected input, remaining stopped."""
    payload: dict[str, Any] = {"protected_input": _protected_input(directory, private_key, expected_artifact_sha256)}
    if expected_workspace_preview_sha256 is not None or material_destinations is not None:
        payload.update(workspace_mapping=material_destinations,
            expected_workspace_preview_sha256=expected_workspace_preview_sha256)
    return _stage_goal_transfer("coordination.planned_transfer.stage_protected", payload, project, runtime_root, provider)


def inspect_staged_goal_transfer_history(
    *, runtime_root: Path, goal_id: str, operation_id: str,
) -> dict[str, Any]:
    """Read the original settlement content; this grants no target execution.

    The historical debit index stays in its immutable carrier until the target
    accounting owner adopts it. A missing live index must not imply zero usage.
    """
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.inspect_target_history", {
        "runtime_root": str(runtime_root.expanduser().absolute()),
        "goal_id": goal_id, "operation_id": operation_id,
    }, timeout=300.0, retry_safe=True)
    return result


def read_staged_goal_transfer_runs(
    *, runtime_root: Path, goal_id: str, operation_id: str,
) -> dict[str, Any]:
    """Transport the Goal/accounting owners' verified historical projection."""
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.read_target_runs", {
        "runtime_root": str(runtime_root.expanduser().absolute()),
        "goal_id": goal_id, "operation_id": operation_id,
    }, timeout=300.0, retry_safe=True)
    return result


def preview_protected_goal_transfer_target(
    *, directory: Path, private_key: Path, expected_artifact_sha256: str,
    project: Path, runtime_root: Path, provider: str,
    material_destinations: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Review actual target material paths; no authority or execution is admitted."""
    _, target, registry, witness = _read_transfer_destination(project, runtime_root, provider)
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.preview_target", {
        "protected_input": _protected_input(directory, private_key, expected_artifact_sha256),
        "target": target, "destination_registry": registry, "registry_witness": witness,
        "workspace_mapping": material_destinations,
    }, timeout=300.0, retry_safe=True)
    return result


def _stage_goal_transfer(
    method: str, payload: dict[str, Any], project: Path, runtime_root: Path, provider: str,
) -> dict[str, Any]:
    registry_path, target, registry, witness = _read_transfer_destination(project, runtime_root, provider)
    result: dict[str, Any] = effect_runtime_result(method, {
        **payload, "target": target,
        "destination_registry": registry, "registry_witness": witness, "execute": True,
    }, timeout=300.0, retry_safe=False)
    # The typed Goal owner owns construction and conflict policy. The codec
    # alone owns serialization, locking, compensation and independent readback.
    proposed = result.pop("destination_registry")
    with project_registry_transaction(registry_path, operation="planned_transfer_stage_publish", create=dict) as transaction:
        if transaction.payload_copy() != registry:
            raise ValueError("Destination registry changed before publication; retry the original transfer without reverting later work")
        transaction.commit(proposed)
        if load_registry(registry_path) != proposed:
            raise ValueError("Planned transfer registry readback changed; recover the original operation")
        return {**result, "registry_readback_verified": True}


def _read_transfer_destination(
    project: Path, runtime_root: Path, provider: str,
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    project, runtime_root = project.expanduser().absolute(), runtime_root.expanduser().absolute()
    registry_path = project / ".loopx" / "registry.json"
    # Mechanical filesystem validation precedes even the registry lock's IO.
    # Do not follow a caller's linked destination into another project.
    for path in (registry_path, runtime_root):
        if path.resolve() != path:
            raise ValueError("Planned transfer destination must use a physical normalized path")
    target = {"runtime_root": str(runtime_root), "project": str(project),
              "registry_path": str(registry_path), "provider": provider}
    with project_registry_transaction(registry_path, operation="planned_transfer_stage_read", create=dict) as transaction:
        registry = transaction.payload_copy()
        witness = transaction.source_read_witness() if registry_path.exists() else {"path": str(registry_path), "sha256": None}
    return registry_path, target, registry, witness


def _validation_store_io(action: str, runtime_root: Path, goal_id: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fixed private file IO through the existing declaration store codec."""
    import stat
    from ..todos.completion_validation_store import (
        read_completion_validation_declaration, prepare_completion_validation_declaration,
        completion_validation_declaration_path,
    )

    restored = []
    for entry in entries:
        path = completion_validation_declaration_path(runtime_root=runtime_root, goal_id=goal_id, todo_id=entry["todo_id"])
        blob = path.parent / "blobs" / f"{entry['declaration_sha256']}.json"
        for candidate in (path, blob):
            if candidate.resolve() != candidate:
                raise ValueError("Original validation store path is redirected")
            try:
                observed = candidate.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                raise ValueError("Original validation store requires a physical private file")
        if action == "restore":
            digest = prepare_completion_validation_declaration(runtime_root=runtime_root,
                goal_id=goal_id, declaration=entry["declaration"])
            if digest != entry["declaration_sha256"]:
                raise ValueError("Original validation declaration changed during restore")
        elif action != "read":
            raise ValueError("Unsupported private validation IO")
        declaration = read_completion_validation_declaration(runtime_root=runtime_root, goal_id=goal_id,
            todo_id=entry["todo_id"], expected_digest=entry["declaration_sha256"])
        if declaration is None:
            raise ValueError("Original private validation declaration is unavailable")
        restored.append({"todo_id": entry["todo_id"], "declaration_sha256": entry["declaration_sha256"], "declaration": declaration})
    return restored


if __name__ == "__main__":
    # Fixed native codec IO; restoring private validator content grants no execution.
    # The invoking native owner holds the maintenance/state/registry locks.
    import sys

    if len(sys.argv) == 5 and sys.argv[1] == "--validation-io":
        print(json.dumps(_validation_store_io(sys.argv[2], Path(sys.argv[3]), sys.argv[4], json.load(sys.stdin)), ensure_ascii=False))
    elif len(sys.argv) != 3:
        raise ValueError("Registered Goal context reader requires a registry and Goal id")
    else:
        print(json.dumps(_read_goal_transfer_snapshot(registry_path=Path(sys.argv[1]), goal_id=sys.argv[2]), ensure_ascii=False))
