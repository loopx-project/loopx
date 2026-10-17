"""Agent preparation and one product confirmation use the original owners.

These disposable local profiles retain the full installed-journey hold. No
active Goal, paid model, imported session or copied credential is involved.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.goals import planned_transfer_io
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.projects.registry_codec import project_registry_transaction
from tests.control_plane.test_planned_transfer_cancellation import source as source_fixture
from tests.control_plane.test_planned_transfer_cutover import OPERATION
from tests.control_plane.test_planned_transfer_operation import _http

source = source_fixture


def _goal_document(source: dict, *, absolute: bool = False) -> Path:
    document = source["project"] / "requirements/GOAL.md"
    document.parent.mkdir()
    document.write_text("# Original acceptance\nRead input.txt and produce exactly 21 in report.txt.\n")
    with project_registry_transaction(source["registry"], operation="fixture-goal-document") as transaction:
        registry = transaction.payload_copy()
        registry["goals"][0]["authority_sources"] = [{"kind": "goal_doc",
            "path": str(document) if absolute else "requirements/GOAL.md", "role": "primary_goal_document"}]
        transaction.commit(registry)
    return document


@pytest.mark.parametrize("absolute", [False, True])
def test_preparation_carries_the_registered_goal_document_without_manual_enumeration(source: dict, tmp_path: Path, absolute: bool):
    document = _goal_document(source, absolute=absolute)
    expected = document.read_bytes()
    target, prepared = _prepared(source, tmp_path)
    assert (target["project"] / "requirements/GOAL.md").read_bytes() == expected
    assert _prepared(source, tmp_path)[1] == prepared
    review = prepared["prepared_cutover"]
    source["cli"]("goal-transfer", "confirm", "--goal-id", "report", "--operation-id", OPERATION,
        "--expected-preview-sha256", review["preview_sha256"], "--execute")
    goal = json.loads(target["registry"].read_text())["goals"][0]
    assert {"kind": "goal_doc", "path": "requirements/GOAL.md", "role": "primary_goal_document"} in goal["authority_sources"]
    later = expected + b"\nLater destination clarification remains current.\n"
    (target["project"] / "requirements/GOAL.md").write_bytes(later)
    source["cli"]("goal-transfer", "confirm", "--goal-id", "report", "--operation-id", OPERATION,
        "--expected-preview-sha256", review["preview_sha256"], "--execute")
    assert (target["project"] / "requirements/GOAL.md").read_bytes() == later
    source["project"].rename(tmp_path / "offline-source-project")
    source["runtime"].rename(tmp_path / "offline-source-runtime")
    context = target["cli"]("goal-transfer", "context", "--goal-id", "report",
        "--agent-id", "agent-a", "--todo-id", source["todo"])["context"]
    assert context["current_goal"] == goal
    assert context["original_goal"]["authority_sources"][0]["path"] == (str(document) if absolute else "requirements/GOAL.md")
    assert any(row["relative_path"] == "requirements/GOAL.md" for row in context["materials"])
    assert not source["project"].exists() and not source["runtime"].exists()


def test_changed_goal_document_keeps_the_original_product_cancel_path(source: dict, tmp_path: Path):
    _goal_document(source)
    target, prepared = _prepared(source, tmp_path)
    (target["project"] / "requirements/GOAL.md").write_text("Changed after review\n")
    server, thread, call = _http(source)
    try:
        review = prepared["prepared_cutover"]
        assert call({"action": "confirm", "goal_id": "report", "operation_id": OPERATION,
            "preview_sha256": review["preview_sha256"]})[0] == 400
        view = call({"action": "inspect", "goal_id": "report"})[1]
        assert view["cancel_available"] and view["prepared_cutover_unavailable"]
        assert call({"action": "cancel", "goal_id": "report", "operation_id": OPERATION})[0] == 200
        lease = source["acquire"]("resume-after-goal-document-refusal")["lease"]
        source["release"](lease)
        assert (target["project"] / "requirements/GOAL.md").read_bytes() == b"Changed after review\n"
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


@pytest.mark.parametrize("fault", ["missing_document", "external_document", "reserved_document", "unsupported_configuration", "required_capability"])
def test_required_goal_dependencies_are_held_before_preparation_changes_source(source: dict, tmp_path: Path, fault: str):
    document = _goal_document(source)
    if fault == "missing_document":
        document.unlink()
    elif fault == "required_capability":
        source["cli"]("todo", "add", "--goal-id", "report", "--role", "agent", "--claimed-by", "agent-a",
            "--text", "Retain the required evidence dependency", "--required-capability", "external_evidence_poll",
            "--operation-id", "required-evidence")
    else:
        with project_registry_transaction(source["registry"], operation="fixture-required-dependency") as transaction:
            registry = transaction.payload_copy()
            if fault == "external_document":
                outside = tmp_path / "external-credentials.txt"
                outside.write_text("Not a workspace material\n")
                registry["goals"][0]["authority_sources"][0]["path"] = str(outside)
            elif fault == "reserved_document":
                reserved = source["project"] / ".loopx/GOAL.md"
                reserved.write_bytes(document.read_bytes())
                registry["goals"][0]["authority_sources"][0]["path"] = str(reserved)
            else:
                registry["goals"][0]["control_plane"] = {"capability_improvement": {"mode": "bounded"}}
            transaction.commit(registry)
    before = source["fence"].read_bytes(), source["registry"].read_bytes()
    destination = tmp_path / "unprepared-dependencies"
    result = source["cli"]("goal-transfer", "prepare-local", "--goal-id", "report", "--operation-id", OPERATION,
        "--destination-root", str(destination), "--material", str(source["project"] / "input.txt"), "--execute", expected_code=1)
    assert result["ok"] is False
    assert not destination.exists()
    assert before == (source["fence"].read_bytes(), source["registry"].read_bytes())
    lease = source["acquire"]("continue-after-unprepared-dependency")["lease"]
    source["release"](lease)


def _prepared(source: dict, root: Path) -> tuple[dict, dict]:
    destination = root / "local-move"
    view = source["cli"]("goal-transfer", "prepare-local", "--goal-id", "report", "--operation-id", OPERATION,
        "--destination-root", str(destination), "--material", str(source["project"] / "input.txt"), "--execute")
    project, runtime = destination / "workspace", destination / "runtime"
    registry = project / ".loopx/registry.json"

    def cli(*arguments: str, expected_code: int = 0):
        result = subprocess.run([sys.executable, "-I", "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
            "--runtime-root", str(runtime), *arguments], cwd=project, text=True, capture_output=True, timeout=90)
        assert result.returncode == expected_code, result.stdout + result.stderr
        return json.loads(result.stdout)

    return {"project": project, "runtime": runtime, "registry": registry, "cli": cli}, view


def test_agent_prepares_then_settings_confirms_original_operation(source: dict, tmp_path: Path):
    original = source["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"]
    target, prepared = _prepared(source, tmp_path)
    review = prepared["prepared_cutover"]
    assert review["operation_id"] == OPERATION and review["destination_name"] == "local-move"
    assert set(review) == {"operation_id", "preview_sha256", "destination_name"}
    assert prepared["continuation_available"] is False  # Not full installed qualification.
    assert json.loads(target["registry"].read_text())["goals"][0]["activation"]["state"] == "stopped"
    restarted, repeated = _prepared(source, tmp_path)
    assert restarted["registry"] == target["registry"] and repeated == prepared
    restart_effect_runtime(wait_for_exit=True)
    server, thread, call = _http(source)
    try:
        assert call({"action": "inspect", "goal_id": "report"}) == (200, prepared)
        body = {"action": "confirm", "goal_id": "report", "operation_id": OPERATION,
            "preview_sha256": review["preview_sha256"]}
        assert call({**body, "destination_registry": str(tmp_path / "forged")})[0] == 400
        assert call({**body, "preview_sha256": "0" * 64})[0] == 400
        assert json.loads(target["registry"].read_text())["goals"][0]["activation"]["state"] == "stopped"
        status, confirmed = call(body)
        assert status == 200 and confirmed["recorded"] is True
        assert confirmed["readback"]["local_cutover"]["target_runtime_admitted"] is True
        assert "prepared_cutover" not in confirmed["readback"]
        registry = target["registry"].read_bytes()
        assert call(body) == (200, confirmed)
        assert target["registry"].read_bytes() == registry
        assert call({"action": "cancel", "goal_id": "report", "operation_id": OPERATION})[0] == 400
        rendered = subprocess.run([sys.executable, "-I", "-m", "loopx.cli", "--registry", str(source["registry"]),
            "goal-transfer", "confirm", "--goal-id", "report", "--operation-id", OPERATION,
            "--expected-preview-sha256", review["preview_sha256"], "--execute"], text=True, capture_output=True, timeout=60)
        assert rendered.returncode == 0 and "recorded: True" in rendered.stdout and "Original work:" in rendered.stdout
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
    assert target["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"] == original
    assert not (tmp_path / "forged").exists()


def test_changed_destination_refuses_product_confirmation_and_keeps_cancel(source: dict, tmp_path: Path):
    target, prepared = _prepared(source, tmp_path)
    (target["project"] / "input.txt").write_text("Changed after review\n")
    before = target["registry"].read_bytes()
    server, thread, call = _http(source)
    try:
        body = {"action": "confirm", "goal_id": "report", "operation_id": OPERATION,
            "preview_sha256": prepared["prepared_cutover"]["preview_sha256"]}
        assert call(body)[0] == 400
        assert target["registry"].read_bytes() == before
        restart_effect_runtime(wait_for_exit=True)
        status, current = call({"action": "inspect", "goal_id": "report"})
        assert status == 200 and current["cancel_available"] is True and current["prepared_cutover_unavailable"] is True
        assert "prepared_cutover" not in current
        assert call({"action": "cancel", "goal_id": "report", "operation_id": OPERATION})[0] == 200
        assert call({"action": "inspect", "goal_id": "report"})[1]["source_state"] == "available"
        assert source["acquire"]("resume-after-material-refusal")["ok"] is True
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


@pytest.mark.parametrize("fault", ["source_overlap", "missing_material", "unreleased_task"])
def test_preparation_input_holds_precede_source_fencing(source: dict, tmp_path: Path, fault: str):
    destination = tmp_path / "unprepared"
    material = source["project"] / "input.txt"
    lease = None
    if fault == "source_overlap":
        destination = source["project"] / "overlap"
    elif fault == "missing_material":
        material = source["project"] / "missing.txt"
    else:
        lease = source["acquire"]("still-running-original-work")["lease"]
    before = source["fence"].read_bytes(), source["registry"].read_bytes()
    failed = source["cli"]("goal-transfer", "prepare-local", "--goal-id", "report", "--operation-id", OPERATION,
        "--destination-root", str(destination), "--material", str(material), "--execute", expected_code=1)
    assert failed["ok"] is False and not destination.exists()
    assert before == (source["fence"].read_bytes(), source["registry"].read_bytes())
    if lease is not None:
        source["release"](lease)
        assert _prepared(source, tmp_path)[1]["cancel_available"] is True


@pytest.mark.parametrize("fault", ["finish", "readback"])
def test_same_product_operation_recovers_after_recorded_source_final(source: dict, tmp_path: Path, monkeypatch, fault: str):
    target, prepared = _prepared(source, tmp_path)
    original_effect = planned_transfer_io.effect_runtime_result

    def interrupted(method, params, **kwargs):
        if method == "coordination.planned_transfer.finish_local_cutover" and fault == "finish":
            raise OSError("Disconnected before destination admission")
        if method == "coordination.planned_transfer.inspect_operation" and fault == "readback":
            raise OSError("Disconnected after known accepted switch")
        return original_effect(method, params, **kwargs)

    monkeypatch.setattr(planned_transfer_io, "effect_runtime_result", interrupted)
    server, thread, call = _http(source)
    body = {"action": "confirm", "goal_id": "report", "operation_id": OPERATION,
        "preview_sha256": prepared["prepared_cutover"]["preview_sha256"]}
    try:
        status, result = call(body)
        if fault == "finish":
            assert status == 400
        else:
            assert status == 200 and result["recorded"] is True and result["readback"] is None
        original_registry = target["registry"].read_bytes()
        monkeypatch.setattr(planned_transfer_io, "effect_runtime_result", original_effect)
        restart_effect_runtime(wait_for_exit=True)
        current = call({"action": "inspect", "goal_id": "report"})[1]
        if fault == "finish":
            assert current["local_cutover"]["target_runtime_admitted"] is False
            assert current["prepared_cutover"] == prepared["prepared_cutover"]
        accepted = call(body)
        assert accepted[0] == 200 and accepted[1]["readback"]["local_cutover"]["target_runtime_admitted"] is True
        assert call(body) == accepted and target["registry"].read_bytes() == original_registry
        assert source["acquire"]("old-source-must-stay-closed", expected_code=1)["ok"] is False
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
