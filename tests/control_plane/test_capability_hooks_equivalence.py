from __future__ import annotations

import argparse
import inspect
import json
import subprocess
import sys
import types
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest

import loopx.agent_registry as agent_registry
import loopx.cli_commands.quota as quota_module
import loopx.control_plane.capability_hooks as capability_hooks
import loopx.control_plane.projects.registry_codec as registry_codec
from loopx.canary.maintainability_ratchet import (
    collect_oversized_decision_functions,
    module_metrics,
)
from loopx.cli_commands.lark_inbox import dispatch_goal_lark_turn_start_hooks
from loopx.cli_commands.quota_should_run import dispatch_quota_turn_start_hooks
from loopx.control_plane.capability_hooks import (
    PostWritebackHookRegistration,
    TURN_START_HOOK_RESULT_SCHEMA_VERSION,
    TurnContextHookRegistration,
    TurnStartHookRegistration,
    capability_hook_request_scope,
    current_hook_request_scope,
    dispatch_interaction_projection_hooks,
    dispatch_post_writeback_hooks,
    dispatch_turn_context_hooks,
    dispatch_turn_start_hooks,
    invalidate_hook_registry_snapshot,
    load_scoped_registry,
    turn_context_source,
)
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID,
    GOAL_ID,
    _write_fixture,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_EXPORTS = (
    "CAPABILITY_HOOK_REGISTRATION_SCHEMA_VERSION",
    "CapabilityHookRequestScope",
    "EffectRuntimeRejected",
    "EffectRuntimeRemoteError",
    "EffectRuntimeStartupError",
    "INTERACTION_PROJECTION_HOOK_DISPATCH_SCHEMA_VERSION",
    "INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION",
    "InteractionProjectionHookRegistration",
    "InteractionProjectionProducer",
    "LOCK_POLICIES",
    "LockAcquireTimeoutError",
    "LockAcquisitionPolicy",
    "POST_WRITEBACK_HOOK_DISPATCH_SCHEMA_VERSION",
    "POST_WRITEBACK_HOOK_INPUT_SCHEMA_VERSION",
    "POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION",
    "POST_WRITEBACK_HOOK_REGISTRATION_SCHEMA_VERSION",
    "POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION",
    "POST_WRITEBACK_SOURCE_REJECTION_DIAGNOSTIC_CODE",
    "PostWritebackHookRegistration",
    "PostWritebackProducer",
    "PostWritebackSourceRejected",
    "TURN_START_HOOK_DISPATCH_SCHEMA_VERSION",
    "TURN_START_HOOK_REGISTRATION_SCHEMA_VERSION",
    "TURN_START_HOOK_RESULT_SCHEMA_VERSION",
    "TurnContextHookRegistration",
    "TurnStartContextProjector",
    "TurnStartHookRegistration",
    "TurnStartProducer",
    "_LEGACY_LOCK_SAFE_SEGMENT_RE",
    "_POST_WRITEBACK_DISPATCH_ID_RE",
    "_POST_WRITEBACK_RECEIPT_SNAPSHOT_RE",
    "_POST_WRITEBACK_TRANSACTION_PARAMS_MAX_BYTES",
    "_bounded_post_writeback_transport_contracts",
    "_bounded_post_writeback_transport_outcomes",
    "_decode_post_writeback_preflight",
    "_enter_legacy_post_writeback_guard",
    "_hook_failure",
    "_legacy_post_writeback_lock_target",
    "_post_writeback_contract_placeholder",
    "_post_writeback_failure_dispatch",
    "_post_writeback_finalize_params",
    "_post_writeback_provider_outcome",
    "_post_writeback_receipt_snapshot",
    "_post_writeback_runtime_failure",
    "_post_writeback_transport_rejected_outcome",
    "_turn_context_binding_source",
    "capability_hook_request_scope",
    "capability_result_fields",
    "current_hook_registry_snapshots",
    "current_hook_request_scope",
    "dispatch_interaction_projection_hooks",
    "dispatch_post_writeback_hooks",
    "dispatch_turn_context_hooks",
    "dispatch_turn_start_hooks",
    "effect_runtime_projection_result",
    "effect_runtime_result",
    "exclusive_file_lock",
    "host_context_projection",
    "invalidate_hook_registry_snapshot",
    "load_scoped_registry",
    "merge_turn_start_dispatch",
    "project_turn_context",
    "time",
    "turn_context_source",
)


def _load_origin_main_capability_hooks() -> types.ModuleType | None:
    probe = subprocess.run(
        ["git", "show", "origin/main:loopx/control_plane/capability_hooks.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0 or not probe.stdout.strip():
        return None
    module = types.ModuleType("loopx.control_plane._origin_main_capability_hooks")
    module.__package__ = "loopx.control_plane"
    module.__file__ = str(REPO_ROOT / "loopx/control_plane/capability_hooks.py")
    sys.modules[module.__name__] = module
    try:
        exec(compile(probe.stdout, module.__file__, "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(module.__name__, None)
        raise
    return module


def test_exports_signatures_and_zero_any_ratchet() -> None:
    for symbol in EXPECTED_EXPORTS:
        assert hasattr(capability_hooks, symbol), f"missing export {symbol}"

    target_paths = [
        REPO_ROOT / "loopx/control_plane/capability_hooks.py",
        REPO_ROOT / "loopx/control_plane/hook_runtime/__init__.py",
        REPO_ROOT / "loopx/control_plane/hook_runtime/request_scope.py",
        REPO_ROOT / "loopx/control_plane/hook_runtime/contracts.py",
        REPO_ROOT / "loopx/control_plane/hook_runtime/turn_context.py",
        REPO_ROOT / "loopx/control_plane/hook_runtime/dispatch.py",
        REPO_ROOT / "loopx/control_plane/hook_runtime/post_writeback.py",
        Path(__file__).resolve(),
    ]
    forbidden_ignore = "type" + ": ignore"
    forbidden_nofollow = "O_" + "NOFOLLOW"
    for path in target_paths:
        text = path.read_text(encoding="utf-8")
        metrics = module_metrics(path)
        assert metrics["lines"] < 800, f"{path} exceeds 800 LOC: {metrics['lines']}"
        assert metrics["any_count"] == 0, f"{path} contains Any: {metrics['any_count']}"
        assert metrics["dict_any_count"] == 0, f"{path} contains dict[..., Any]"
        assert forbidden_ignore not in text, f"{path} contains {forbidden_ignore}"
        assert forbidden_nofollow not in text, f"{path} contains {forbidden_nofollow}"

    assert collect_oversized_decision_functions(REPO_ROOT) == []

    # Default parameter invariants stay intact when callers pass None or omit kwargs.
    from loopx.control_plane.runtime.runtime_projection_route import (
        resolve_goal_source_runtime_route,
    )

    sig_dispatch = inspect.signature(dispatch_post_writeback_hooks)
    for name in ("source", "hook_input", "runtime_root", "lease_timeout_seconds"):
        assert sig_dispatch.parameters[name].default is None
    sig_route = inspect.signature(resolve_goal_source_runtime_route)
    for name in ("runtime_root_override", "registry", "source_snapshots"):
        assert sig_route.parameters[name].default is None
    assert current_hook_request_scope() is None


def test_paired_origin_main_vs_head_hook_behavior(
    tmp_path: Path,
) -> None:
    baseline = _load_origin_main_capability_hooks()
    if baseline is None:
        pytest.skip("origin/main ref unavailable in checkout")
    try:
        for name in (
            "turn_context_source",
            "dispatch_turn_context_hooks",
            "project_turn_context",
            "host_context_projection",
            "capability_result_fields",
            "dispatch_interaction_projection_hooks",
            "dispatch_turn_start_hooks",
            "dispatch_post_writeback_hooks",
        ):
            baseline_fn = getattr(baseline, name)
            head_fn = getattr(capability_hooks, name)
            assert callable(baseline_fn) and callable(head_fn)
            assert list(inspect.signature(head_fn).parameters.keys()) == list(
                inspect.signature(baseline_fn).parameters.keys()
            ), f"parameter drift on {name}"

        # 1. Turn-context positive, readback-only, duplicate, failure, and conflict checks.
        assert baseline.turn_context_source({"goal_id": "g1", "agent_id": "a1"}) == (
            turn_context_source({"goal_id": "g1", "agent_id": "a1"})
        )
        for impl in (baseline, capability_hooks):
            with pytest.raises(ValueError, match="conflicting Agent identities"):
                impl.turn_context_source(
                    {
                        "goal_id": "g1",
                        "agent_id": "a1",
                        "agent_identity": {"agent_id": "a2"},
                    }
                )

        def _run_turn_context(
            mod: object,
        ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
            reg = getattr(mod, "TurnContextHookRegistration")
            payload: dict[str, object] = {"goal_id": "g1", "agent_id": "a1"}
            hooks = (
                reg(
                    hook_id="fixture.readback",
                    capability_id="fixture",
                    readback_slots=("fixture_slot",),
                    producer=lambda: {
                        "readback": {"fixture_slot": {"ok": True}},
                        "context_fields": {},
                        "instructions": [],
                        "result_fields": {},
                    },
                ),
                reg(
                    hook_id="fixture.readback",
                    capability_id="fixture",
                    readback_slots=("fixture_slot_dup",),
                    producer=lambda: {"readback": {}},
                ),
                reg(
                    hook_id="fixture.broken",
                    capability_id="fixture",
                    readback_slots=("fixture_broken",),
                    producer=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                ),
            )
            getattr(mod, "dispatch_turn_context_hooks")(payload, hooks)
            projected = getattr(mod, "project_turn_context")(payload)
            host_proj = getattr(mod, "host_context_projection")(
                {
                    "capability_context_fields": ("extra_field",),
                    "extra_field": "kept",
                    "capability_instructions": ("do X",),
                    "capability_result_fields": {"r": {"type": "string"}},
                }
            )
            assert getattr(mod, "capability_result_fields")(host_proj) == {
                "r": {"type": "string"}
            }
            return payload, projected, host_proj

        assert _run_turn_context(baseline) == _run_turn_context(capability_hooks)

        # 2. Interaction projection parity (slot conflict + broken producer).
        def _run_interaction(mod: object) -> dict[str, object]:
            reg = getattr(mod, "InteractionProjectionHookRegistration")
            hooks = (
                reg(
                    hook_id="proj.ok",
                    capability_id="fixture",
                    projection_slots=("slot_a",),
                    requested_read_scope=(),
                    producer=lambda: {"status": "not_applicable"},
                ),
                reg(
                    hook_id="proj.fail",
                    capability_id="fixture",
                    projection_slots=("slot_b",),
                    requested_read_scope=(),
                    producer=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                ),
            )
            res = getattr(mod, "dispatch_interaction_projection_hooks")(hooks)
            assert isinstance(res, dict)
            return dict(res)

        assert _run_interaction(baseline) == _run_interaction(capability_hooks)

        # 3. Turn-start dispatch parity (ordering, duplicate id, uncertain write effect, context reader).
        def _turn_start_packet(status: str = "observed") -> dict[str, object]:
            return {
                "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
                "hook_id": "z.fixture",
                "capability_id": "fixture",
                "phase": "turn_start",
                "status": status,
                "observation_count": 1,
                "agent_read_required": True,
                "external_reads_performed": False,
                "external_writes_performed": False,
                "local_private_state_mutated": False,
                "private_content_returned": False,
                "provider_payload_returned": False,
                "error_code": None,
            }

        def _run_turn_start(mod: object) -> dict[str, object]:
            reg = getattr(mod, "TurnStartHookRegistration")
            hooks = (
                reg(
                    hook_id="z.fixture",
                    capability_id="fixture",
                    requested_read_scope=("goal",),
                    requested_write_scope=(),
                    producer=lambda: _turn_start_packet(),
                    required_read={
                        "kind": "fixture",
                        "command": "loopx fixture read",
                        "reason": "verify parity",
                        "ordering": "before_work",
                    },
                    context_reader=lambda: (_ for _ in ()).throw(OSError("offline")),
                ),
                reg(
                    hook_id="a.broken_write",
                    capability_id="fixture",
                    requested_read_scope=(),
                    requested_write_scope=("local_state",),
                    producer=lambda: (_ for _ in ()).throw(ValueError("bad producer")),
                ),
                reg(
                    hook_id="z.fixture",
                    capability_id="fixture",
                    requested_read_scope=(),
                    requested_write_scope=(),
                    producer=lambda: _turn_start_packet(),
                ),
            )
            res = getattr(mod, "dispatch_turn_start_hooks")(hooks)
            assert isinstance(res, dict)
            return dict(res)

        assert _run_turn_start(baseline) == _run_turn_start(capability_hooks)

        # 4. Post-writeback error & lock-guard parity.
        empty_hook = PostWritebackHookRegistration(
            hook_id="pw.fixture",
            capability_id="fixture",
            event_kinds=("todo_complete",),
            intent_kinds=(),
            requested_read_scope=(),
            producer=lambda _inp: {"status": "empty"},
        )
        assert baseline.dispatch_post_writeback_hooks([]) == (
            dispatch_post_writeback_hooks([])
        )
        assert baseline.dispatch_post_writeback_hooks(
            [empty_hook], source={}, hook_input={}
        ) == dispatch_post_writeback_hooks([empty_hook], source={}, hook_input={})

        for impl in (baseline, capability_hooks):
            with pytest.raises(impl.PostWritebackSourceRejected):
                impl.dispatch_post_writeback_hooks([empty_hook], source={"goal_id": ""})

        # Legacy receipt guard check with synthetic lock directories.
        raw_plan: dict[str, object] = {
            "dispatch_id": "pwh_" + ("a" * 64),
            "receipt_snapshot": "missing",
            "hook_input": {"identity": {"goal_id": "goal-1"}},
        }
        with ExitStack() as s_old:
            old_err = baseline._enter_legacy_post_writeback_guard(
                s_old,
                runtime_root=tmp_path / "old",
                raw_plan=raw_plan,
                lease_timeout_seconds=0.1,
            )
        with ExitStack() as s_new:
            new_err = capability_hooks._enter_legacy_post_writeback_guard(
                s_new,
                runtime_root=tmp_path / "new",
                raw_plan=raw_plan,
                lease_timeout_seconds=0.1,
            )
        assert old_err == new_err
        assert new_err is None
    finally:
        sys.modules.pop(baseline.__name__, None)


def test_turn_start_hook_monkeypatch_seams_remain_wired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    real_rpc = capability_hooks.effect_runtime_result
    real_source = capability_hooks.turn_context_source

    def tracked_rpc(method: str, params: Mapping[str, object]) -> object:
        calls.append(method)
        return real_rpc(method, params)

    monkeypatch.setattr(capability_hooks, "effect_runtime_result", tracked_rpc)
    monkeypatch.setattr(
        capability_hooks,
        "turn_context_source",
        lambda payload: real_source(payload),
    )
    monkeypatch.setattr(
        capability_hooks,
        "time",
        SimpleNamespace(monotonic=lambda: 42.0),
    )

    payload: dict[str, object] = {"goal_id": "g-hook", "agent_id": "a-hook"}
    dispatch_turn_context_hooks(
        payload,
        (
            TurnContextHookRegistration(
                hook_id="hook.seam",
                capability_id="fixture",
                readback_slots=("slot_x",),
                producer=lambda: {
                    "readback": {"slot_x": {}},
                    "context_fields": {},
                    "instructions": [],
                    "result_fields": {},
                },
            ),
        ),
    )
    assert calls == [
        "capability_hook.turn_context.validate_registration",
        "capability_hook.turn_context.validate",
    ]
    assert (
        dispatch_interaction_projection_hooks(())["registered_count"] == 0
    )
    assert dispatch_turn_start_hooks(())["registered_count"] == 0
    assert (
        dispatch_post_writeback_hooks(
            (),
            source=None,
            hook_input=None,
        )["failures"]
        == []
    )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_quota_turn_start_hooks_avoid_redundant_registry_reads_and_stale_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    _, runtime, registry = _write_fixture(
        tmp_path,
        required_capability="network",
        provider=provider,
    )

    counts = {"load_registry": 0, "reg_read_bytes": 0}
    orig_load_reg = registry_codec.load_registry
    orig_read_bytes = Path.read_bytes

    def counted_load_reg(
        path: Path,
        *,
        source_snapshots: dict[Path, bytes | None] | None = None,
    ) -> dict[str, object]:
        counts["load_registry"] += 1
        return orig_load_reg(path, source_snapshots=source_snapshots)

    def counted_read_bytes(self: Path) -> bytes:
        if self.resolve() == registry.resolve():
            counts["reg_read_bytes"] += 1
        return orig_read_bytes(self)

    monkeypatch.setattr(registry_codec, "load_registry", counted_load_reg)
    import loopx.agent_registry as ar
    import loopx.cli_commands.quota_should_run as qsr
    import loopx.control_plane.runtime.goal_project_route as gpr
    import loopx.control_plane.runtime.runtime_projection_route as rpr
    import loopx.control_plane.todos.list_readback as lr
    import loopx.history as lh

    for mod in (qsr, gpr, rpr, lr, ar, lh):
        if hasattr(mod, "load_registry"):
            monkeypatch.setattr(mod, "load_registry", counted_load_reg)
    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)

    # Exercise agent-preferences and external_evidence_poll scopes so all hook paths stay wired.
    (runtime / "agent-preferences").mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("LOOPX_DISABLE_EXTERNAL_NETWORK", raising=False)
    args = argparse.Namespace(
        quota_command="should-run",
        dry_run=False,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_observation=False,
        available_capabilities=["network", "external_evidence_poll"],
    )
    dispatch, mutated = dispatch_quota_turn_start_hooks(
        args,
        quota_module=quota_module,
        registry_path=registry,
        runtime_root_arg=str(runtime),
        lark_turn_start_hook_dispatcher=dispatch_goal_lark_turn_start_hooks,
    )
    assert mutated is True
    assert dispatch is not None
    assert int(str(dispatch["registered_count"])) >= 3
    assert int(str(dispatch["invoked_count"])) == int(str(dispatch["registered_count"]))
    assert current_hook_request_scope() is None
    # Single-pass request scope eliminates per-hook registry re-reads (baseline was 11 load_registry / 12 read_bytes).
    assert counts["load_registry"] == 1
    assert counts["reg_read_bytes"] <= 2

    # Subsequent request must observe disk mutations immediately (no cross-request stale cache).
    updated = json.loads(registry.read_text(encoding="utf-8"))
    updated["goals"][0]["coordination"]["registered_agents"].append("agent-fresh")
    registry.write_text(json.dumps(updated), encoding="utf-8")

    observed_agents_across_hooks: list[list[str]] = []

    def _capture_goal_hook() -> dict[str, object]:
        agents = agent_registry.registered_agent_ids_from_registry(registry, GOAL_ID)
        observed_agents_across_hooks.append(agents)
        return {
            "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": f"probe.{len(observed_agents_across_hooks)}",
            "capability_id": "fixture",
            "phase": "turn_start",
            "status": "empty",
            "observation_count": 0,
            "agent_read_required": False,
            "external_reads_performed": False,
            "external_writes_performed": False,
            "local_private_state_mutated": False,
            "private_content_returned": False,
            "provider_payload_returned": False,
            "error_code": None,
        }

    counts["load_registry"] = 0
    counts["reg_read_bytes"] = 0
    probe_hooks = tuple(
        TurnStartHookRegistration(
            hook_id=f"probe.{idx}",
            capability_id="fixture",
            requested_read_scope=("goal",),
            requested_write_scope=(),
            producer=_capture_goal_hook,
        )
        for idx in range(1, 5)
    )
    res = dispatch_turn_start_hooks(probe_hooks)
    assert res["invoked_count"] == 4
    assert all("agent-fresh" in agents for agents in observed_agents_across_hooks)
    assert counts["load_registry"] == 1
    assert counts["reg_read_bytes"] == 1
    assert current_hook_request_scope() is None

    # Direct standalone calls outside any dispatch scope never retain stale state across writes.
    updated2 = json.loads(registry.read_text(encoding="utf-8"))
    updated2["goals"][0]["coordination"]["registered_agents"].append("agent-direct")
    registry.write_text(json.dumps(updated2), encoding="utf-8")
    assert "agent-direct" in agent_registry.registered_agent_ids_from_registry(
        registry, GOAL_ID
    )

    # Explicit invalidation clears cached snapshot inside an open scope too.
    with capability_hook_request_scope():
        first_agents = agent_registry.registered_agent_ids_from_registry(
            registry, GOAL_ID
        )
        assert "agent-invalidated" not in first_agents
        updated3 = json.loads(registry.read_text(encoding="utf-8"))
        updated3["goals"][0]["coordination"]["registered_agents"].append(
            "agent-invalidated"
        )
        registry.write_text(json.dumps(updated3), encoding="utf-8")
        invalidate_hook_registry_snapshot(registry)
        second_agents = agent_registry.registered_agent_ids_from_registry(
            registry, GOAL_ID
        )
        assert "agent-invalidated" in second_agents
        refreshed = load_scoped_registry(registry, loader=orig_load_reg)
        assert isinstance(refreshed.get("goals"), list)


def _goal_ids(payload: Mapping[str, object]) -> list[object]:
    goals = payload.get("goals")
    assert isinstance(goals, list)
    return [g["id"] for g in goals if isinstance(g, Mapping)]


def test_request_scope_preserves_path_format_semantics_across_symlinks(
    tmp_path: Path,
) -> None:
    project_reg = tmp_path / "registry.json"
    envelope_bytes = registry_codec._encode_document(
        {"goals": [{"id": "g1", "title": "Project Goal"}]},
        format=registry_codec._ProjectRegistryFormat.STRICT_ENVELOPE_V1,
        minimum_writer_protocol=registry_codec.CURRENT_WRITER_PROTOCOL,
    )
    project_reg.write_bytes(envelope_bytes)
    global_alias = tmp_path / "registry.global.json"
    global_alias.symlink_to(project_reg)

    with pytest.raises(
        registry_codec.ProjectRegistryError,
        match="global registry root must be a JSON object",
    ):
        registry_codec.load_registry(global_alias)

    with capability_hook_request_scope():
        project_loaded = load_scoped_registry(
            project_reg, loader=registry_codec.load_registry
        )
        assert _goal_ids(project_loaded) == ["g1"]
        project_goal = agent_registry.load_goal_from_registry(project_reg, "g1")
        assert project_goal is not None and project_goal["id"] == "g1"

        with pytest.raises(
            registry_codec.ProjectRegistryError,
            match="global registry root must be a JSON object",
        ):
            load_scoped_registry(global_alias, loader=registry_codec.load_registry)

        with pytest.raises(
            registry_codec.ProjectRegistryError,
            match="global registry root must be a JSON object",
        ):
            agent_registry.load_goal_from_registry(global_alias, "g1")

        still_project_goal = agent_registry.load_goal_from_registry(project_reg, "g1")
        assert still_project_goal is not None and still_project_goal["id"] == "g1"


def test_request_scope_atomic_write_invalidates_alias_snapshots_and_goal_index(
    tmp_path: Path,
) -> None:
    real_reg = tmp_path / "registry.json"
    alias_reg = tmp_path / "alias.json"
    alias_direct = tmp_path / "alias_direct.json"
    v1_bytes = json.dumps(
        {"goals": [{"id": "g-old", "title": "Old Goal"}]}
    ).encode("utf-8")
    v2_bytes = json.dumps(
        {"goals": [{"id": "g-new", "title": "New Goal"}]}
    ).encode("utf-8")
    registry_codec._atomic_write_bytes(real_reg, v1_bytes, mode=None)
    alias_reg.symlink_to(real_reg)
    alias_direct.symlink_to(real_reg)

    with capability_hook_request_scope() as scope:
        first = load_scoped_registry(alias_reg, loader=registry_codec.load_registry)
        assert _goal_ids(first) == ["g-old"]
        old_goal = agent_registry.load_goal_from_registry(alias_reg, "g-old")
        assert old_goal is not None and old_goal["id"] == "g-old"
        direct_v1 = registry_codec.load_registry(
            alias_direct, source_snapshots=scope.registry_snapshots
        )
        assert _goal_ids(direct_v1) == ["g-old"]

        registry_codec._atomic_write_bytes(real_reg, v2_bytes, mode=None)

        alias_v2 = load_scoped_registry(alias_reg, loader=registry_codec.load_registry)
        real_v2 = load_scoped_registry(real_reg, loader=registry_codec.load_registry)
        direct_v2 = registry_codec.load_registry(
            alias_direct, source_snapshots=scope.registry_snapshots
        )
        assert _goal_ids(alias_v2) == ["g-new"]
        assert _goal_ids(real_v2) == ["g-new"]
        assert _goal_ids(direct_v2) == ["g-new"]
        assert agent_registry.load_goal_from_registry(alias_reg, "g-old") is None
        assert agent_registry.load_goal_from_registry(real_reg, "g-old") is None
        new_alias_goal = agent_registry.load_goal_from_registry(alias_reg, "g-new")
        new_real_goal = agent_registry.load_goal_from_registry(real_reg, "g-new")
        assert new_alias_goal is not None and new_alias_goal["id"] == "g-new"
        assert new_real_goal is not None and new_real_goal["id"] == "g-new"
