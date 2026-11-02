"""Native snapshot reads preserve current facts and the incumbent off path."""
from copy import deepcopy
import json
from pathlib import Path
import shlex
import subprocess

import pytest

from loopx.capabilities.native_chat.antigravity_adapter import AntigravityAdapter
from loopx.capabilities.native_chat.manager_context import native_manager_context


def _context():
    return {"schema_version": "manager_turn_context_v1", "snapshot_id": "original",
        "coverage": {"complete": False}, "warnings": ["unread_remote"],
        "context_delegation": {"source_id": "original-request", "targets": []},
        "goals": [{"goal_id": "goal/with~characters", "attention": {"items": ["STOP"]},
            "current_todos": {"todos": [{"text": "完整要求", "tail_constraint": "KEEP"}]},
            "future_field": {"unclassified": "keep inline"},
            "recent_delivery_history": {"status": "read", "source_revision": "original-history",
                "coverage": {"complete": False},
                "deliveries": [{"recorded_details": {"negative_evidence": "失败原文\n" * 15000},
                                "last_constraint": "DO NOT MERGE"}]}}]}


def _adapter(profile="trusted_owner"):
    adapter = object.__new__(AntigravityAdapter)
    adapter.runtime_profile = profile
    return adapter


def _read(command):
    """Execute the emitted CLI, including its independent integrity/page checks."""
    fragments = []
    while command:
        result = subprocess.run(shlex.split(command), text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        page = json.loads(result.stdout)
        assert page["ok"] is True and page["offset"] == len("".join(fragments).encode())
        fragments.append(page["content"])
        command = page.get("next_command")
        assert bool(command) != page["eof"]
    return json.loads("".join(fragments))


def test_native_history_reader_and_full_recovery_keep_the_same_complete_source(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    context = _context()
    original = deepcopy(context)
    result = native_manager_context(context, _adapter(), runtime_root=tmp_path, capture_root=tmp_path)
    assert context == original
    for key in ("coverage", "warnings", "context_delegation", "snapshot_id"):
        assert result[key] == original[key]
    goal = result["goals"][0]
    assert {k: v for k, v in goal.items() if k != "recent_delivery_history"} == {
        k: v for k, v in original["goals"][0].items() if k != "recent_delivery_history"}
    history = goal["recent_delivery_history"]
    assert history["status"] == "not_read" and history["deliveries"] == []
    assert history["coverage"] == original["goals"][0]["recent_delivery_history"]["coverage"]
    selected = _read(history["read_reference"]["command"])
    assert selected["selections"] == [{"pointer": "/goals/0/recent_delivery_history",
                                     "value": original["goals"][0]["recent_delivery_history"]}]
    # A lost model context can recover the whole exact source; no admission or
    # native send is part of this CLI. Repeated reads remain read-only.
    assert _read(result["context_read"]["recovery_command"]) == original
    assert _read(result["context_read"]["recovery_command"]) == original
    reference = Path(result["context_read"]["reference_file"])
    source = Path(json.loads(reference.read_text())["path"])
    assert reference.stat().st_mode & 0o077 == source.stat().st_mode & 0o077 == 0
    assert reference.parent.stat().st_mode & 0o077 == 0
    source.write_text(source.read_text() + " ")
    rejected = subprocess.run(shlex.split(history["read_reference"]["command"]), text=True,
                              capture_output=True, timeout=30)
    assert rejected.returncode != 0 and "digest mismatch" in rejected.stdout + rejected.stderr


@pytest.mark.parametrize("reason", ["restricted", "other_host", "review", "off", "invalid", "environment_off",
                                   "unavailable", "empty", "unknown_field", "capture_failure", "platform"])
def test_unqualified_or_disabled_native_capture_keeps_full_inline_parity(tmp_path, monkeypatch, reason):
    from loopx.control_plane.turn_driver import decision_capture

    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    adapter = _adapter()
    context = _context()
    if reason == "restricted":
        adapter.runtime_profile = "restricted"
    elif reason == "other_host":
        adapter = object()
    elif reason == "review":
        adapter._read_only_review = True
    elif reason in {"off", "invalid"}:
        from loopx.capabilities.machine_configuration.store import configure_machine_configuration
        from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry

        configuration = {"schema_version": "loopx_machine_configuration_v0", "namespaces": {
            "context_delivery": {"schema_version": "context_delivery_machine_defaults_v0", "mode": "off"}}}
        registry = build_builtin_machine_configuration_registry()
        plan = configure_machine_configuration(runtime_root=tmp_path, configuration=configuration,
                                                 registry=registry, execute=False)
        configure_machine_configuration(runtime_root=tmp_path, configuration=configuration, registry=registry,
                                          execute=True, expected_plan_revision=plan["plan_revision"])
        if reason == "invalid":
            (tmp_path / "machine/configuration.json").write_text("{")
    elif reason == "environment_off":
        monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "off")
    elif reason == "unavailable":
        context["goals"][0]["recent_delivery_history"]["status"] = "unavailable"
    elif reason == "empty":
        context["goals"][0]["recent_delivery_history"]["deliveries"] = []
    elif reason == "unknown_field":
        context["context_read"] = {"future_contract": "do not overwrite"}
    elif reason == "capture_failure":
        monkeypatch.setattr(decision_capture, "capture_context_projection",
                            lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic capture failure")))
    elif reason == "platform":
        monkeypatch.setattr(decision_capture, "private_capture_uid",
                            lambda: (_ for _ in ()).throw(ValueError("unsupported platform")))
    original = deepcopy(context)
    assert native_manager_context(context, adapter, runtime_root=tmp_path, capture_root=tmp_path) is context
    assert context == original and not list(tmp_path.glob("manager-context-*"))
