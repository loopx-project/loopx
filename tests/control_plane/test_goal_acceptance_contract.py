"""Contract, Zero-Any, and verification-edge tests for Goal acceptance host adapters."""

from __future__ import annotations

import ast
from copy import deepcopy as real_deepcopy
import hashlib
import json
from pathlib import Path
import tokenize
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from loopx.control_plane.effect_runtime import CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS
from loopx.control_plane.goals import acceptance, acceptance_verification

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ast_any_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "Any"]


def _suppression_lines(path: Path) -> list[int]:
    lines: list[int] = []
    with path.open("r", encoding="utf-8") as handle:
        for tok in tokenize.generate_tokens(handle.readline):
            if tok.type == tokenize.COMMENT and ("type: ignore" in tok.string or "noqa" in tok.string):
                lines.append(tok.start[0])
    return lines


def test_goal_acceptance_modules_meet_ceiling_zero_any_and_pin_invariants() -> None:
    acceptance_path = REPO_ROOT / "loopx/control_plane/goals/acceptance.py"
    verification_path = REPO_ROOT / "loopx/control_plane/goals/acceptance_verification.py"
    contract_test_path = Path(__file__).resolve()

    assert len(acceptance_path.read_text(encoding="utf-8").splitlines()) <= 550
    assert len(verification_path.read_text(encoding="utf-8").splitlines()) < 750
    for path in (acceptance_path, verification_path, contract_test_path):
        assert _ast_any_lines(path) == [], str(path)
        assert _suppression_lines(path) == [], str(path)

    pins = json.loads(
        (REPO_ROOT / "tests/architecture/package_kernel_import_pins.json").read_text(encoding="utf-8")
    )
    assert "loopx.control_plane.goals.acceptance" in pins["imports"]["loopx-jev"]

    expected_exports = (
        "GoalAcceptanceRejected",
        "GoalAcceptanceRoute",
        "goal_acceptance_route",
        "transition_goal_acceptance_lifecycle",
        "refine_goal_outcome",
        "require_active_goal_for_objective_update",
        "handoff_goal_outcomes",
        "update_goal_objective",
        "inspect_goal_acceptance",
        "observe_goal_acceptance_oracles",
        "observe_canonical_goal_acceptance",
        "_criterion_effects",
        "validation_effect_files_current",
        "configure_goal_acceptance",
        "run_goal_acceptance_effects",
        "_acceptance_pins_match",
        "_stale_acceptance_receipt",
        "run_goal_acceptance_validation_effect",
        "public_goal_acceptance",
        "verify_goal_acceptance",
    )
    for symbol in expected_exports:
        assert hasattr(acceptance, symbol), symbol

    for shared_name in (
        "observe_goal_acceptance_oracles",
        "observe_canonical_goal_acceptance",
        "_criterion_effects",
        "validation_effect_files_current",
        "run_goal_acceptance_effects",
        "_acceptance_pins_match",
        "_stale_acceptance_receipt",
        "run_goal_acceptance_validation_effect",
    ):
        assert getattr(acceptance, shared_name) is getattr(acceptance_verification, shared_name)


def test_routing_and_result_error_boundaries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    source_root = tmp_path / "source-runtime"
    monkeypatch.setattr(
        acceptance,
        "resolve_goal_source_runtime_route",
        lambda **_kw: {
            "source_registry": str(registry),
            "source_runtime_root": str(source_root),
            "routed_to_source_registry": False,
        },
    )
    route = acceptance.goal_acceptance_route(registry, "g-1", runtime_root="/override/root")
    assert route == {"registry_path": registry, "runtime_root": "/override/root", "goal_id": "g-1"}

    monkeypatch.setattr(
        acceptance,
        "resolve_goal_source_runtime_route",
        lambda **_kw: {
            "source_registry": str(registry),
            "source_runtime_root": str(source_root),
            "routed_to_source_registry": True,
        },
    )
    pinned_route = acceptance.goal_acceptance_route(registry, "g-1", runtime_root="/override/root")
    assert pinned_route["runtime_root"] == str(source_root)

    monkeypatch.setattr(acceptance, "load_goal_from_registry", lambda *_a: None)
    with pytest.raises(ValueError, match="registered Goal"):
        acceptance._routing(registry, "g-1", None)

    monkeypatch.setattr(acceptance, "load_goal_from_registry", lambda *_a: {"id": "g-1"})
    monkeypatch.setattr(acceptance, "registered_agent_ids_for_goal", lambda _g: ["agent-a"])
    with pytest.raises(ValueError, match="registered Agent"):
        acceptance._routing(registry, "g-1", None, agent_id="agent-b")

    monkeypatch.setattr(acceptance, "local_authority_is_promoted", lambda **_kw: False)
    with pytest.raises(ValueError, match="activation never promotes"):
        acceptance._routing(registry, "g-1", None, agent_id="agent-a")

    instance_id = "ginst_" + "a" * 32
    monkeypatch.setattr(acceptance, "local_authority_is_promoted", lambda **_kw: True)
    monkeypatch.setattr(
        acceptance,
        "load_goal_from_registry",
        lambda *_a: {"id": "g-1", "goal_instance_id": instance_id},
    )
    resolved_reg, _, built_route = acceptance._routing(registry, "g-1", None, agent_id="agent-a")
    assert resolved_reg == registry
    assert built_route["goal_ref"] == {"goal_id": "g-1", "goal_instance_id": instance_id}

    monkeypatch.setattr(acceptance, "effect_runtime_result", lambda *_a, **_kw: ["bad"])
    with pytest.raises(TypeError, match="invalid result"):
        acceptance._result("goal.acceptance.inspect", {})

    monkeypatch.setattr(
        acceptance,
        "effect_runtime_result",
        lambda *_a, **_kw: {"status": "rejected", "reason_code": "bad_req", "error_detail": "bad doc"},
    )
    with pytest.raises(acceptance.GoalAcceptanceRejected) as rejected:
        acceptance._result("goal.acceptance.configure", {})
    assert rejected.value.code == "bad_req"
    assert rejected.value.payload == {"error_detail": "bad doc"}


def test_lifecycle_refine_handoff_and_objective_guards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    monkeypatch.setattr(acceptance, "local_authority_is_promoted", lambda **_kw: False)
    assert (
        acceptance.transition_goal_acceptance_lifecycle(
            runtime_root=runtime, goal_id="g-1", transition={"kind": "retire"}, operation_id="op-1"
        )
        is None
    )

    recorded: list[tuple[str, dict[str, object], float | None]] = []
    monkeypatch.setattr(acceptance, "local_authority_is_promoted", lambda **_kw: True)

    def _record_effect(
        method: str, payload: dict[str, object], timeout: float | None = None
    ) -> dict[str, object]:
        recorded.append((method, payload, timeout))
        return {"status": "applied"}

    monkeypatch.setattr(acceptance, "effect_runtime_result", _record_effect)
    res = acceptance.transition_goal_acceptance_lifecycle(
        runtime_root=runtime, goal_id="g-1", transition={"kind": "retire"}, operation_id="op-1"
    )
    assert res == {"status": "applied"}
    assert recorded[0][0] == "goal.acceptance.lifecycle.transition"
    assert recorded[0][2] == CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS

    monkeypatch.setattr(
        acceptance,
        "_routing",
        lambda *_a: (registry, {"id": "g-1", "status": "active"}, {"runtime_root": str(runtime), "goal_id": "g-1"}),
    )
    monkeypatch.setattr(acceptance, "registered_agent_ids_for_goal", lambda _g: ["agent-a"])
    monkeypatch.setattr(acceptance, "goal_is_stopped", lambda _g: False)
    with pytest.raises(ValueError, match="registered Agent"):
        acceptance.refine_goal_outcome(
            registry_path=registry,
            goal_id="g-1",
            refinement={"owner_agent_id": "unknown"},
            expected_provider_revision="r1",
            operation_id="op-2",
        )

    monkeypatch.setattr(
        acceptance,
        "_routing",
        lambda *_a: (registry, {"id": "g-1", "status": "completed"}, {"runtime_root": str(runtime), "goal_id": "g-1"}),
    )
    with pytest.raises(ValueError, match="active Goal"):
        acceptance.refine_goal_outcome(
            registry_path=registry,
            goal_id="g-1",
            refinement={"objective": "x"},
            expected_provider_revision="r1",
            operation_id="op-3",
        )

    with pytest.raises(ValueError, match="local Goal owner"):
        acceptance.handoff_goal_outcomes(
            registry_path=registry,
            goal_id="g-1",
            document={"outcomes": []},
            expected_provider_revision="r1",
            operation_id="op-4",
            agent_id="agent-a",
        )

    monkeypatch.setattr(
        acceptance,
        "resolve_goal_source_runtime_route",
        lambda **_kw: {"source_registry": str(registry), "source_runtime_root": str(runtime)},
    )
    monkeypatch.setattr(
        "loopx.control_plane.collaboration.goal_instance_scope.collaboration_goal_scope",
        lambda *_a, **_kw: _ScopeCtx(SimpleNamespace(registry_path=(tmp_path / "other.json").resolve())),
    )
    with pytest.raises(ValueError, match="Goal source changed"):
        acceptance.handoff_goal_outcomes(
            registry_path=registry,
            goal_id="g-1",
            document={"outcomes": []},
            expected_provider_revision="r1",
            operation_id="op-5",
        )


class _ScopeCtx:
    def __init__(self, scope: object) -> None:
        self._scope = scope

    def __enter__(self) -> object:
        return self._scope

    def __exit__(self, *_args: object) -> None:
        return None


def test_inspect_skips_deepcopy_without_oracles_and_captures_when_observed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = tmp_path / "registry.json"
    monkeypatch.setattr(
        acceptance,
        "_routing",
        lambda *_a: (registry, {"id": "g-1"}, {"runtime_root": str(tmp_path), "goal_id": "g-1"}),
    )
    monkeypatch.setattr(acceptance, "registered_agent_ids_for_goal", lambda _g: ["agent-a"])

    deepcopy_calls = 0

    def counted_deepcopy(value: object) -> object:
        nonlocal deepcopy_calls
        deepcopy_calls += 1
        return real_deepcopy(value)

    monkeypatch.setattr(acceptance, "deepcopy", counted_deepcopy)
    monkeypatch.setattr(
        acceptance,
        "_result",
        lambda *_a, **_kw: {
            "status": "loaded",
            "contract": {"criteria": [{"id": "c1", "validation_argv": ["true"]}]},
            "goal_acceptance_contract": {"status": "configured", "revision": 1},
            "outcome_context": {"suggested_outcome_ids": ["agent-a"]},
        },
    )
    no_oracle = acceptance.inspect_goal_acceptance(registry_path=registry, goal_id="g-1")
    assert deepcopy_calls == 0
    assert "outcome_context" in no_oracle

    monkeypatch.setattr(
        acceptance,
        "observe_goal_acceptance_oracles",
        lambda **_kw: {"status": "stale", "revision": 1},
    )
    hooked = acceptance.inspect_goal_acceptance(registry_path=registry, goal_id="g-1")
    assert deepcopy_calls == 1
    assert "outcome_context" not in hooked
    assert hooked["goal_acceptance_contract"] == {"status": "stale", "revision": 1}


def test_verifier_pins_and_oracle_stability_edges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "ws"
    (workspace / "nested").mkdir(parents=True)
    verifier = workspace / "nested/check.py"
    verifier.write_bytes(b"assert True\n")
    digest = hashlib.sha256(b"assert True\n").hexdigest()

    assert acceptance_verification._acceptance_pins_match([], None) is True
    assert acceptance_verification._acceptance_pins_match([{"path": "nested/check.py", "sha256": digest}], workspace) is True
    assert acceptance_verification._acceptance_pins_match([{"path": "nested/check.py", "sha256": digest}], None) is False
    assert acceptance_verification._acceptance_pins_match("bad-type", workspace) is False
    assert acceptance_verification._acceptance_pins_match([{"path": r"nested\check.py", "sha256": digest}], workspace) is False
    assert acceptance_verification._acceptance_pins_match([{"path": "../escape.py", "sha256": digest}], workspace) is False
    assert acceptance_verification._acceptance_pins_match([{"path": str(verifier), "sha256": digest}], workspace) is False
    assert acceptance_verification._acceptance_pins_match([{"path": "nested/check.py", "sha256": "0" * 64}], workspace) is False

    link_path = workspace / "nested/link.py"
    link_path.symlink_to(verifier)
    assert acceptance_verification._acceptance_pins_match([{"path": "nested/link.py", "sha256": digest}], workspace) is False

    registry = tmp_path / "registry.json"
    with pytest.raises(TypeError, match="validation_files must be an array"):
        acceptance_verification.run_goal_acceptance_validation_effect(
            effect={"validation_files": "not-a-list"},
            registry_path=registry,
            goal_id="g-1",
        )

    monkeypatch.setattr(
        acceptance_verification,
        "_resolve_completion_validation_workspace",
        lambda **_kw: (workspace, None),
    )
    monkeypatch.setattr(
        acceptance_verification,
        "effect_runtime_result",
        lambda method, _payload: {"source_id": "src-1", "source_digest": "sha256:abc"}
        if method == "goal.acceptance.oracle.plan"
        else {},
    )
    with pytest.raises(TypeError):
        acceptance_verification.run_goal_acceptance_validation_effect(
            effect={"validation_files": [], "oracle": "invalid-oracle"},
            registry_path=registry,
            goal_id="g-1",
        )

    stale = acceptance_verification.run_goal_acceptance_validation_effect(
        effect={
            "validation_label": "check",
            "validation_files": [{"path": "nested/check.py", "sha256": "deadbeef"}],
        },
        registry_path=registry,
        goal_id="g-1",
    )
    assert stale["status"] == "validation_basis_changed"
    assert stale["passed"] is False

    versions_seq = [
        {"input_versions": [{"ref": "in.txt", "sha256": "aa"}], "output_versions": []},
        {"input_versions": [{"ref": "in.txt", "sha256": "bb"}], "output_versions": []},
    ]
    monkeypatch.setattr(acceptance_verification, "oracle_versions", lambda _ws, _oracle: versions_seq.pop(0))
    monkeypatch.setattr(
        acceptance_verification,
        "run_snapshot_oracle",
        lambda **_kw: ({"exit_code": 0, "passed": True}, True),
    )
    drifted = acceptance_verification.run_goal_acceptance_validation_effect(
        effect={
            "validation_label": "oracle-check",
            "validation_files": [{"path": "nested/check.py", "sha256": digest}],
            "oracle": {"kind": "owner_pinned_oracle", "source_id": "src-1", "input_refs": ["in.txt"], "output_refs": []},
        },
        registry_path=registry,
        goal_id="g-1",
    )
    assert drifted["status"] == "validation_basis_changed"
    assert drifted["passed"] is False

    stable_version = {"input_versions": [{"ref": "in.txt", "sha256": "aa"}], "output_versions": []}
    monkeypatch.setattr(acceptance_verification, "oracle_versions", lambda _ws, _oracle: stable_version)
    with patch.object(acceptance_verification, "run_snapshot_oracle", return_value=({"exit_code": 0, "passed": True}, True)):
        passed = acceptance_verification.run_goal_acceptance_validation_effect(
            effect={
                "validation_label": "oracle-check",
                "validation_files": [{"path": "nested/check.py", "sha256": digest}],
                "oracle": {"kind": "owner_pinned_oracle", "source_id": "src-1", "input_refs": ["in.txt"], "output_refs": []},
            },
            registry_path=registry,
            goal_id="g-1",
        )
    assert passed["passed"] is True
    evidence = passed["oracle_evidence"]
    assert isinstance(evidence, dict)
    assert evidence["source_id"] == "src-1"
    assert str(evidence["checked_at"]).endswith("Z")

    with pytest.raises(ValueError, match="criterion identity"):
        acceptance_verification.run_goal_acceptance_effects(
            effects=[{"criterion_id": ""}], registry_path=registry, goal_id="g-1"
        )
    monkeypatch.setattr(
        acceptance_verification,
        "run_goal_acceptance_validation_effect",
        lambda **_kw: {"exit_code": 999, "passed": False, "oracle_evidence": {"source_id": "s"}},
    )
    normalized = acceptance_verification.run_goal_acceptance_effects(
        effects=[{"criterion_id": "c1"}], registry_path=registry, goal_id="g-1"
    )
    assert normalized == [{"criterion_id": "c1", "passed": False, "exit_code": None, "oracle_evidence": {"source_id": "s"}}]


def test_observe_canonical_public_and_verify_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    assert acceptance_verification.observe_canonical_goal_acceptance({}, registry_path=registry, goal_id="g-1") is None
    with pytest.raises(ValueError, match="oracle observation basis"):
        acceptance_verification.observe_canonical_goal_acceptance(
            {
                "goal_acceptance_contract": {"revision": 1, "digest": "d1"},
                "goal_acceptance_oracle_observation_basis": {"revision": 2, "digest": "d1", "criteria": []},
            },
            registry_path=registry,
            goal_id="g-1",
        )

    projected = acceptance.public_goal_acceptance(
        {"status": "loaded", "goal_id": "g-1", "goal_acceptance_contract": {"revision": 1}, "secret": "drop"},
        include_contract=False,
    )
    assert projected == {"ok": True, "status": "loaded", "goal_id": "g-1"}

    monkeypatch.setattr(
        acceptance,
        "_routing",
        lambda *_a: (registry, {"id": "g-1"}, {"runtime_root": str(tmp_path), "goal_id": "g-1"}),
    )
    monkeypatch.setattr(
        acceptance,
        "inspect_goal_acceptance",
        lambda **_kw: {
            "status": "loaded",
            "provider_revision": "rev-2",
            "contract": {"criteria": [{"id": "c1", "validation_argv": ["true"]}]},
            "goal_acceptance_contract": {"revision": 1, "digest": "d1"},
        },
    )
    with pytest.raises(ValueError, match="reload the current contract"):
        acceptance.verify_goal_acceptance(
            registry_path=registry, goal_id="g-1", expected_provider_revision="rev-1"
        )
    dry = acceptance.verify_goal_acceptance(registry_path=registry, goal_id="g-1", execute=False)
    assert dry["status"] == "planned" and dry["executed"] is False
