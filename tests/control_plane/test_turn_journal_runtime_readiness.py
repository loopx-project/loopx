from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane import effect_runtime
from loopx.doctor import collect_doctor, render_doctor_markdown


class _Completed:
    def __init__(self, *, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def test_runtime_fingerprint_rotates_when_any_owned_source_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = Path(effect_runtime.__file__).resolve().parent
    source_files = effect_runtime._runtime_source_files(source_root)
    assert "effect_runtime_server.ts" in source_files
    assert "runtime_decode.ts" in source_files
    assert "turn_transaction_contract.json" in source_files
    for relative in source_files:
        source = source_root / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    monkeypatch.setattr(effect_runtime, "_control_plane_root", lambda: tmp_path)

    original = effect_runtime._runtime_fingerprint()
    for index, relative in enumerate(source_files):
        target = tmp_path / relative
        original_bytes = target.read_bytes()
        target.write_bytes(original_bytes + f"\n// fingerprint-{index}\n".encode())
        assert effect_runtime._runtime_fingerprint() != original
        target.write_bytes(original_bytes)
    assert effect_runtime._runtime_fingerprint() == original


def test_runtime_fingerprint_reuses_hash_until_source_snapshot_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "runtime_decode.ts").write_text("export {};\n", encoding="utf-8")
    (tmp_path / "turn_transaction_contract.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(effect_runtime, "_control_plane_root", lambda: tmp_path)
    # Some filesystems preserve directory metadata across rapid entry changes.
    # The source inventory must not rely on that metadata to discover new files.
    monkeypatch.setattr(
        effect_runtime,
        "_runtime_directory_snapshot",
        lambda *_args: (("", 0, 0),),
        raising=False,
    )
    original_read_bytes = Path.read_bytes
    reads: list[Path] = []

    def counted_read_bytes(path: Path) -> bytes:
        reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)

    original = effect_runtime._runtime_fingerprint()
    assert len(reads) == 2
    assert effect_runtime._runtime_fingerprint() == original
    assert len(reads) == 2

    added = tmp_path / "new_runtime_dependency.ts"
    added.write_text("export const added = true;\n", encoding="utf-8")
    expanded = effect_runtime._runtime_fingerprint()
    assert expanded != original
    assert len(reads) == 5

    added.unlink()
    assert effect_runtime._runtime_fingerprint() == original
    assert len(reads) == 5


def test_runtime_fingerprint_rescans_when_a_discovered_file_disappears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kept = tmp_path / "kept.ts"
    removed = tmp_path / "removed.ts"
    kept.write_text("export const kept = true;\n", encoding="utf-8")
    removed.write_text("export const removed = true;\n", encoding="utf-8")
    original_scan = effect_runtime._scan_runtime_source_files
    scans: list[tuple[str, ...]] = []

    def scan_then_remove(root: Path) -> tuple[str, ...]:
        files = original_scan(root)
        scans.append(files)
        if len(scans) == 1:
            removed.unlink()
        return files

    monkeypatch.setattr(effect_runtime, "_scan_runtime_source_files", scan_then_remove)

    monkeypatch.setattr(effect_runtime, "_control_plane_root", lambda: tmp_path)

    assert len(effect_runtime._runtime_fingerprint()) == 64
    assert scans == [("kept.ts", "removed.ts"), ("kept.ts",)]


def test_runtime_fingerprint_rescans_when_a_snapshotted_file_disappears_while_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.ts"
    later = tmp_path / "later.ts"
    first.write_text("export const first = true;\n", encoding="utf-8")
    later.write_text("export const later = true;\n", encoding="utf-8")
    original_read_bytes = Path.read_bytes
    reads: list[str] = []

    def remove_later_after_first_read(path: Path) -> bytes:
        reads.append(path.name)
        content = original_read_bytes(path)
        if path == first and later.exists():
            later.unlink()
        return content

    monkeypatch.setattr(effect_runtime, "_control_plane_root", lambda: tmp_path)
    monkeypatch.setattr(Path, "read_bytes", remove_later_after_first_read)

    assert len(effect_runtime._runtime_fingerprint()) == 64
    assert reads == ["first.ts", "later.ts", "first.ts"]


def _install_persistent_stat_read_churn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, ...]]:
    first = tmp_path / "first.ts"
    later = tmp_path / "later.ts"
    first.write_text("export const first = true;\n", encoding="utf-8")
    original_scan = effect_runtime._scan_runtime_source_files
    original_read_bytes = Path.read_bytes
    scans: list[tuple[str, ...]] = []

    def restore_then_scan(root: Path) -> tuple[str, ...]:
        later.write_text("export const later = true;\n", encoding="utf-8")
        files = original_scan(root)
        scans.append(files)
        return files

    def remove_later_after_first_read(path: Path) -> bytes:
        content = original_read_bytes(path)
        if path == first:
            later.unlink()
        return content

    monkeypatch.setattr(effect_runtime, "_control_plane_root", lambda: tmp_path)
    monkeypatch.setattr(effect_runtime, "_scan_runtime_source_files", restore_then_scan)
    monkeypatch.setattr(Path, "read_bytes", remove_later_after_first_read)
    return scans


def test_runtime_source_churn_has_a_stable_readiness_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scans = _install_persistent_stat_read_churn(tmp_path, monkeypatch)
    monkeypatch.setattr(effect_runtime.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(
        effect_runtime.subprocess,
        "run",
        lambda *_args, **_kwargs: _Completed(stdout="v22.22.3\n"),
    )
    result = effect_runtime.collect_effect_runtime_readiness()

    assert result["status"] == "package_invalid"
    assert result["ready"] is False
    assert (
        result["runtime_lifecycle"]["diagnostic_code"]
        == "packaged_runtime_source_unstable"
    )
    assert scans == [("first.ts", "later.ts"), ("first.ts", "later.ts")]


def test_runtime_request_source_churn_raises_a_stable_startup_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scans = _install_persistent_stat_read_churn(tmp_path, monkeypatch)

    with pytest.raises(effect_runtime.EffectRuntimeStartupError) as error:
        effect_runtime.effect_runtime_request("runtime.ping", {})

    assert error.value.diagnostic_code == "packaged_runtime_source_unstable"
    assert scans == [("first.ts", "later.ts"), ("first.ts", "later.ts")]


def test_missing_node_blocks_the_typescript_control_plane_and_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(effect_runtime.shutil, "which", lambda _name: None)

    result = effect_runtime.collect_effect_runtime_readiness()

    assert result["status"] == "missing"
    assert result["ready"] is False
    assert result["default_cli_blocking"] is True
    assert result["required_for"] == ["control_plane"]
    assert "Node.js 22.22.3 or newer" in str(result["recommended_action"])


def test_missing_node_request_raises_startup_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(effect_runtime.shutil, "which", lambda _name: None)

    with pytest.raises(effect_runtime.EffectRuntimeStartupError) as error:
        effect_runtime.effect_runtime_result("runtime.ping", {})

    assert error.value.diagnostic_code == "node_unavailable"
    assert "Node.js 22.22.3 or newer" in str(error.value)


@pytest.mark.parametrize("version", ["20.19.5", "22.18.0", "22.22.2"])
def test_old_node_is_reported_without_running_semantic_probe(
    version: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(effect_runtime.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(
        effect_runtime.subprocess,
        "run",
        lambda *_args, **_kwargs: _Completed(stdout=f"v{version}\n"),
    )

    result = effect_runtime.collect_effect_runtime_readiness(deep=True)

    assert result["status"] == "unsupported"
    assert result["detected_node_version"] == version
    assert result["semantic_probe"] == "not_run"


def test_current_node_standard_probe_does_not_execute_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(effect_runtime.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(
        effect_runtime.subprocess,
        "run",
        lambda *_args, **_kwargs: _Completed(stdout="v22.22.3\n"),
    )

    result = effect_runtime.collect_effect_runtime_readiness()

    assert result["status"] == "ready"
    assert result["ready"] is True
    assert result["semantic_probe"] == "not_requested"
    assert result["runtime_lifecycle"] == {
        "schema_version": "loopx_effect_runtime_lifecycle_v0",
        "management": "on_demand_managed",
        "state": "stopped",
        "manual_start_required": False,
        "restart_policy": "automatic_on_next_control_plane_request",
        "idle_shutdown": True,
        "diagnostic_code": None,
    }


def test_deep_probe_executes_packaged_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(effect_runtime.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(
        effect_runtime.subprocess,
        "run",
        lambda *_args, **_kwargs: _Completed(stdout="v24.1.0\n"),
    )

    def request(method: str, params: dict[str, Any]) -> dict[str, object]:
        calls.append((method, params))
        if method == "runtime.ping":
            return {"ready": True}
        return {"effect_id": "doctor-probe:doctor-probe:doctor-probe:doctor-probe"}

    monkeypatch.setattr(
        effect_runtime,
        "effect_runtime_result",
        request,
    )

    result = effect_runtime.collect_effect_runtime_readiness(deep=True)

    assert result["status"] == "ready"
    assert result["semantic_probe"] == "passed"
    assert result["runtime_lifecycle"]["state"] == "running"
    assert [method for method, _params in calls] == [
        "runtime.ping",
        "settlement.identity",
    ]
    assert calls[1][1]["goal_id"] == "doctor-probe"


def test_deep_probe_failure_is_public_safe_and_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(effect_runtime.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(
        effect_runtime.subprocess,
        "run",
        lambda *_args, **_kwargs: _Completed(stdout="v22.22.3\n"),
    )

    def failed(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise effect_runtime.EffectRuntimeStartupError(
            "/private/path/worker.mjs failed",
            diagnostic_code="runtime_exited_before_ready",
        )

    monkeypatch.setattr(
        effect_runtime,
        "effect_runtime_result",
        failed,
    )

    result = effect_runtime.collect_effect_runtime_readiness(deep=True)

    assert result["status"] == "probe_failed"
    assert result["ready"] is False
    assert result["semantic_probe"] == "failed"
    assert "/private/path" not in str(result)
    assert "reinstall LoopX" in str(result["recommended_action"])
    assert result["runtime_lifecycle"]["state"] == "unavailable"
    assert (
        result["runtime_lifecycle"]["diagnostic_code"]
        == "runtime_exited_before_ready"
    )


def test_doctor_markdown_projects_runtime_lifecycle_for_app_health() -> None:
    rendered = render_doctor_markdown(
        {
            "typescript_control_plane": {
                "status": "probe_failed",
                "runtime_lifecycle": {
                    "state": "unavailable",
                    "diagnostic_code": "runtime_exited_before_ready",
                },
            },
            "checks": [],
        }
    )

    assert "typescript_runtime_state: `unavailable`" in rendered
    assert (
        "typescript_runtime_diagnostic: `runtime_exited_before_ready`" in rendered
    )


def test_missing_required_runtime_fails_doctor_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = {
        "schema_version": "loopx_effect_runtime_readiness_v0",
        "ready": True,
        "status": "ready",
        "required_for": ["control_plane"],
        "default_cli_blocking": True,
        "minimum_node_version": "22.22.3",
        "detected_node_version": "24.1.0",
        "semantic_probe": "not_requested",
        "recommended_action": None,
    }
    missing = {
        **ready,
        "ready": False,
        "status": "missing",
        "detected_node_version": None,
        "recommended_action": "Install Node.js 22.22.3 or newer.",
    }
    monkeypatch.setattr(
        effect_runtime,
        "collect_effect_runtime_readiness",
        lambda *, deep=False: ready,
    )
    ready_doctor = collect_doctor()
    monkeypatch.setattr(
        effect_runtime,
        "collect_effect_runtime_readiness",
        lambda *, deep=False: missing,
    )
    missing_doctor = collect_doctor()

    assert ready_doctor["ok"] is True
    assert missing_doctor["ok"] is False
    runtime_check = next(
        check
        for check in missing_doctor["checks"]
        if check["id"] == "typescript_effect_runtime_ready"
    )
    assert runtime_check == {
        "id": "typescript_effect_runtime_ready",
        "required": True,
        "ok": False,
        "detail": "missing",
    }
    assert missing_doctor["typescript_control_plane"] == missing


def test_deep_doctor_fails_when_present_runtime_cannot_execute_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = {
        "schema_version": "loopx_effect_runtime_readiness_v0",
        "ready": False,
        "status": "probe_failed",
        "required_for": ["control_plane"],
        "default_cli_blocking": True,
        "minimum_node_version": "22.22.3",
        "detected_node_version": "24.1.0",
        "semantic_probe": "failed",
        "recommended_action": "Reinstall LoopX.",
    }
    monkeypatch.setattr(
        effect_runtime,
        "collect_effect_runtime_readiness",
        lambda *, deep=False: failed,
    )
    monkeypatch.setattr(
        "loopx.release_candidate.collect_release_candidate_checks",
        lambda **_kwargs: {"ok": True, "checks": []},
    )

    doctor = collect_doctor(deep=True)

    runtime_check = next(
        check
        for check in doctor["checks"]
        if check["id"] == "typescript_effect_runtime_ready"
    )
    assert runtime_check["required"] is True
    assert runtime_check["ok"] is False
    assert doctor["ok"] is False
