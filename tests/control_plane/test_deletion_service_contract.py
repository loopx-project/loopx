from __future__ import annotations

import ast
import io
import json
from pathlib import Path
import tokenize

import pytest

from loopx.chat import goal_lifecycle_actions
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.control_plane.goals import deletion_basis, deletion_recovery, deletion_service
from loopx.control_plane.goals.activation import build_goal_activation
from loopx.control_plane.goals.activation_service import (
    GoalActivationAuthorityRouteMode,
    set_goal_activation_state,
)
from loopx.control_plane.goals.deletion_basis import (
    _MAX_RECOVERY_RECEIPT_BYTES,
    DeletionReadback,
    DeletionResult,
    DeletionRoute,
    Payload,
    PayloadView,
)
from loopx.control_plane.goals.deletion_recovery import (
    RecoveryRecord,
    RecoveryRuntimeHooks,
    RecoverySnapshot,
    _as_recovery_record,
    _load_recovery_record,
)
from loopx.control_plane.goals.deletion_service import (
    GOAL_DELETION_RECOVERY_SCHEMA_VERSION,
    GOAL_DELETION_SCHEMA_VERSION,
    GOAL_DELETION_SOURCE_BASIS_SCHEMA_VERSION,
    GOAL_DELETION_STATE_FINGERPRINT_SCHEMA_VERSION,
    _normalize_source_basis,
    _recovery_hooks,
    _recovery_receipt_path,
    delete_stopped_goal,
    deletion_readback,
)
from loopx.global_registry import sync_project_registry_to_global
from loopx.history import load_registry
from loopx.public_safe_text import OPAQUE_ID_PATTERN
from loopx.registry import registry_goals
from loopx.semantics.inventory import SourceFile
from loopx.semantics.project_registry_io import scan_python_registry_io

_SHA256_A = "a" * 64
_SHA256_B = "b" * 64
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _stopped_connected_registries(tmp_path: Path) -> tuple[Path, Path]:
    runtime_root = tmp_path / "runtime"
    source_registry = tmp_path / "project" / ".loopx" / "registry.json"
    _write_json(
        source_registry,
        {
            "schema_version": "0.1",
            "common_runtime_root": str(runtime_root),
            "goals": [
                {
                    "id": "goal-one",
                    "display_name": "Contract Goal",
                    "repo": str(tmp_path / "project"),
                    "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
                }
            ],
        },
    )
    synced = sync_project_registry_to_global(
        registry_path=source_registry,
        runtime_root_override=str(runtime_root),
        goal_id="goal-one",
        dry_run=False,
    )
    assert synced["ok"] is True
    global_registry = runtime_root / "registry.global.json"
    stopped = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    assert stopped["ok"] is True
    return source_registry, global_registry


def test_deletion_modules_strict_zero_any_size_and_boundaries() -> None:
    targets = [
        _REPO_ROOT / "loopx" / "control_plane" / "goals" / "deletion_basis.py",
        _REPO_ROOT / "loopx" / "control_plane" / "goals" / "deletion_recovery.py",
        _REPO_ROOT / "loopx" / "control_plane" / "goals" / "deletion_service.py",
        Path(__file__).resolve(),
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert len(lines) < 700, f"{path} exceeded 700 LOC ({len(lines)})"
        comments = [
            tok.string
            for tok in tokenize.generate_tokens(io.StringIO(text).readline)
            if tok.type == tokenize.COMMENT
        ]
        assert not any(
            "type: ignore" in comment or "noqa" in comment for comment in comments
        ), f"{path} contains suppression comments"
        tree = ast.parse(text)
        any_names = [
            node
            for node in ast.walk(tree)
            if (isinstance(node, ast.Name) and node.id == "Any")
            or (isinstance(node, ast.Attribute) and node.attr == "Any")
        ]
        assert any_names == [], f"{path} must remain 0-Any"

    for helper_path in targets[:2]:
        rel_path = helper_path.relative_to(_REPO_ROOT).as_posix()
        text = helper_path.read_text(encoding="utf-8")
        assert "content_digest" not in text
        observations = scan_python_registry_io(
            SourceFile(path=rel_path, suffix=".py", text=text)
        )
        direct_io = [
            obs for obs in observations if obs.kind.startswith("direct_json_")
        ]
        assert direct_io == []


def test_deletion_service_reexports_and_runtime_seams() -> None:
    assert deletion_service._OPAQUE_ID is OPAQUE_ID_PATTERN
    assert deletion_service.GOAL_DELETION_SCHEMA_VERSION == GOAL_DELETION_SCHEMA_VERSION
    assert (
        deletion_service.GOAL_DELETION_SOURCE_BASIS_SCHEMA_VERSION
        == GOAL_DELETION_SOURCE_BASIS_SCHEMA_VERSION
    )
    assert (
        deletion_service.GOAL_DELETION_STATE_FINGERPRINT_SCHEMA_VERSION
        == GOAL_DELETION_STATE_FINGERPRINT_SCHEMA_VERSION
    )
    assert (
        deletion_service.GOAL_DELETION_RECOVERY_SCHEMA_VERSION
        == GOAL_DELETION_RECOVERY_SCHEMA_VERSION
    )
    assert deletion_service.DeletionResult is DeletionResult
    assert deletion_service.DeletionReadback is DeletionReadback
    assert deletion_service.DeletionRoute is DeletionRoute
    assert deletion_service.Payload is Payload
    assert deletion_service.PayloadView is PayloadView
    assert deletion_service.RecoveryRecord is RecoveryRecord
    assert deletion_service.RecoverySnapshot is RecoverySnapshot
    assert deletion_service.RecoveryRuntimeHooks is RecoveryRuntimeHooks
    assert deletion_basis._MAX_RECOVERY_RECEIPT_BYTES == _MAX_RECOVERY_RECEIPT_BYTES
    assert deletion_recovery._execute_recovery is not None

    hooks = _recovery_hooks()
    assert hooks.atomic_write_json_fn is deletion_service.atomic_write_json
    assert (
        hooks.project_registry_transaction_fn
        is deletion_service.project_registry_transaction
    )
    assert hooks.write_locked_registry_fn is deletion_service._write_locked_registry
    assert hooks.restore_locked_registry_fn is deletion_service._restore_locked_registry
    assert hooks.now_local_iso_fn is deletion_service.now_local_iso


def test_normalize_source_basis_and_input_validation_fail_closed(
    tmp_path: Path,
) -> None:
    valid_basis = {
        "schema_version": GOAL_DELETION_SOURCE_BASIS_SCHEMA_VERSION,
        "source_identity": _SHA256_A,
        "source_content_sha256": _SHA256_B,
        "route_mode": GoalActivationAuthorityRouteMode.SOURCE_TO_GLOBAL.value,
    }
    assert _normalize_source_basis(valid_basis) == valid_basis
    assert _normalize_source_basis(None) is None

    for bad_basis in (
        {**valid_basis, "schema_version": "wrong"},
        {**valid_basis, "source_identity": "short"},
        {**valid_basis, "source_content_sha256": "short"},
        {**valid_basis, "route_mode": "unsupported"},
    ):
        with pytest.raises(ValueError):
            _normalize_source_basis(bad_basis)

    _source_registry, global_registry = _stopped_connected_registries(tmp_path)
    with pytest.raises(ValueError, match="compact opaque id"):
        delete_stopped_goal(registry_path=global_registry, goal_id="../escape")
    with pytest.raises(ValueError, match="SHA-256 digest"):
        delete_stopped_goal(
            registry_path=global_registry,
            goal_id="goal-one",
            expected_state_fingerprint="not-a-digest",
        )
    with pytest.raises(ValueError, match="expected source route mode is unsupported"):
        delete_stopped_goal(
            registry_path=global_registry,
            goal_id="goal-one",
            expected_source_basis={**valid_basis, "route_mode": "invalid"},
        )


def test_load_and_as_recovery_record_reject_malformed_and_oversized_inputs(
    tmp_path: Path,
) -> None:
    pattern = _recovery_hooks().sha256_pattern
    target_registry = tmp_path / "runtime" / "registry.global.json"
    _write_json(target_registry, {"schema_version": "0.1", "goals": []})
    receipt_path = _recovery_receipt_path(target_registry, "goal-one")

    with pytest.raises(ValueError, match="unavailable"):
        _load_recovery_record(receipt_path, "goal-one", sha256_pattern=pattern)

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("x" * (_MAX_RECOVERY_RECEIPT_BYTES + 1), encoding="utf-8")
    with pytest.raises(ValueError, match="size limit"):
        _load_recovery_record(receipt_path, "goal-one", sha256_pattern=pattern)

    symlink_target = tmp_path / "outside.json"
    symlink_target.write_text("{}", encoding="utf-8")
    receipt_path.unlink()
    receipt_path.symlink_to(symlink_target)
    with pytest.raises(ValueError, match="unavailable"):
        _load_recovery_record(receipt_path, "goal-one", sha256_pattern=pattern)

    with pytest.raises(ValueError, match="goal identity"):
        _as_recovery_record({"target_registry": str(target_registry)}, sha256_pattern=pattern)

    _write_json(
        symlink_target,
        {
            "schema_version": "wrong",
            "goal_id": "goal-one",
        },
    )
    receipt_path.unlink()
    _write_json(
        receipt_path,
        {
            "schema_version": GOAL_DELETION_RECOVERY_SCHEMA_VERSION,
            "goal_id": "goal-one",
            "state_fingerprint": "bad",
        },
    )
    with pytest.raises(ValueError, match="state fingerprint"):
        _load_recovery_record(receipt_path, "goal-one", sha256_pattern=pattern)


def test_delete_stopped_goal_missing_stale_lock_failure_and_replay_contract(
    tmp_path: Path,
) -> None:
    source_registry, global_registry = _stopped_connected_registries(tmp_path)

    with pytest.raises(ValueError, match="goal id not found in registry"):
        delete_stopped_goal(registry_path=global_registry, goal_id="missing-goal")

    missing_stale = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="missing-goal",
        execute=True,
        expected_state_fingerprint=_SHA256_A,
    )
    assert isinstance(missing_stale, DeletionResult)
    assert isinstance(missing_stale, dict)
    assert missing_stale["ok"] is False
    assert missing_stale["stale"] is True
    assert missing_stale["error_kind"] == "goal_registry_changed"
    assert missing_stale["readback"]["verified"] is False

    preview = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="goal-one",
        execute=False,
    )
    assert preview["ok"] is True
    assert preview["dry_run"] is True

    deleted = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="goal-one",
        execute=True,
        expected_state_fingerprint=preview["observed_state_fingerprint"],
        expected_source_basis=preview["source_basis"],
    )
    assert deleted["ok"] is True
    assert deleted["written"] is True
    assert deleted["readback"]["verified"] is True
    assert registry_goals(load_registry(source_registry)) == []
    assert registry_goals(load_registry(global_registry)) == []

    replayed = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="goal-one",
        execute=True,
        expected_state_fingerprint=preview["observed_state_fingerprint"],
        expected_source_basis=preview["source_basis"],
    )
    assert replayed["ok"] is True
    assert replayed["written"] is False
    assert replayed["recovered"] is True
    assert replayed["replayed"] is True
    assert replayed["readback"]["verified"] is True

    orphan_source = tmp_path / "orphan-project" / ".loopx" / "registry.json"
    orphan_source.parent.mkdir(parents=True, exist_ok=True)
    lock_target = tmp_path / "lock-target"
    lock_target.write_text("", encoding="utf-8")
    orphan_source.with_name(f"{orphan_source.name}.lock").symlink_to(lock_target)
    orphan_global = tmp_path / "orphan-runtime" / "registry.global.json"
    _write_json(
        orphan_global,
        {
            "schema_version": "0.1",
            "registry_role": "global-local",
            "goals": [
                {
                    "id": "orphaned-goal",
                    "display_name": "Orphaned Goal",
                    "source_registry": str(orphan_source),
                    "activation": build_goal_activation(
                        state="stopped",
                        updated_at="2026-08-20T00:00:00+00:00",
                        reason="Fixture setup",
                    ),
                }
            ],
        },
    )
    blocked = delete_stopped_goal(
        registry_path=orphan_global,
        goal_id="orphaned-goal",
        execute=True,
    )
    assert blocked["ok"] is False
    assert blocked["error_kind"] == "goal_source_lock_unavailable"


def test_deletion_readback_helper_and_chat_caller_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert deletion_service.deletion_readback is deletion_readback
    assert deletion_readback({}) is None
    assert deletion_readback({"readback": "not-a-mapping"}) is None
    assert deletion_readback(
        {
            "readback": {
                "source_missing": True,
                "global_missing": True,
                "verified": True,
            }
        }
    ) == {
        "source_missing": True,
        "global_missing": True,
        "verified": True,
    }

    _source_registry, global_registry = _stopped_connected_registries(tmp_path)
    service = ChatActionService(
        store=ChatActionStore(tmp_path / "actions"),
        registry_path=global_registry,
    )
    proposal = service.preview(
        {
            "action_kind": "goal.lifecycle",
            "summary": "Delete stopped contract Goal",
            "normalized_parameters": {
                "goal_id": "goal-one",
                "operation": "delete",
                "reason": "Contract test",
            },
            "context": {"kind": "goal_directory"},
            "idempotency_key": "contract-delete-readback-missing",
        }
    )

    def delete_without_readback(
        *,
        registry_path: Path,
        goal_id: str,
        execute: bool = False,
        expected_state_fingerprint: str | None = None,
        expected_source_basis: PayloadView | None = None,
    ) -> dict[str, object]:
        if execute:
            return {"ok": True}
        return delete_stopped_goal(
            registry_path=registry_path,
            goal_id=goal_id,
            execute=False,
            expected_state_fingerprint=expected_state_fingerprint,
            expected_source_basis=expected_source_basis,
        )

    monkeypatch.setattr(
        goal_lifecycle_actions,
        "delete_stopped_goal",
        delete_without_readback,
    )
    with pytest.raises(ValueError, match="Goal deletion did not verify"):
        service.apply(str(proposal["proposal_id"]))
