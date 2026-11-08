"""Off-state Turn composition must not load report construction providers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


_SOURCE = Path(__file__).resolve().parents[2]
_CONSTRUCTION_MODULES = (
    "loopx.capabilities.periodic_report.presets",
    # cadence still uses profile's canonical schedule validator, including
    # disabled subscriptions. This slice preserves that validation boundary.
    "loopx.capabilities.periodic_report.project_progress",
    "loopx.capabilities.periodic_report.project_progress_snapshot",
    "loopx.capabilities.periodic_report.stage_completion",
    "loopx.capabilities.periodic_report.request_action",
    "loopx.capabilities.periodic_report.todo_source",
)


@pytest.mark.parametrize(
    "override,default,warning",
    [
        (None, None, None),
        ({"enabled": False, "profile_preset": "weekly"}, None, None),
        ({"enabled": False}, True, None),
        (None, False, None),
        ({"enabled": "true"}, None, "enabled must be a boolean"),
        ({"enabled": False, "timezone": "unknown/timezone"}, None, "timezone is unknown"),
        (None, "true", "enabled must be a boolean"),
    ],
)
def test_disabled_turn_composition_keeps_validation_without_construction(
    tmp_path: Path, override: object, default: object, warning: str | None
) -> None:
    goal: dict[str, object] = {"id": "fixture-goal"}
    if override is not None:
        goal["control_plane"] = {"periodic_report": override}
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [goal]}), encoding="utf-8")
    runtime = tmp_path / "runtime"
    if default is not None:
        store = runtime / "machine" / "configuration.json"
        store.parent.mkdir(parents=True)
        store.write_text(json.dumps({
            "schema_version": "loopx_machine_configuration_v0",
            "namespaces": {"periodic_report": {
                "schema_version": "periodic_report_machine_defaults_v0",
                "enabled": default, "profile_preset": "weekly-progress",
                "route_ref": "fixture-route", "timezone": "UTC",
            }},
        }), encoding="utf-8")
    script = f"""
import importlib.abc, os, sys, warnings
from pathlib import Path
from types import SimpleNamespace
sys.dont_write_bytecode = True
class RejectConstruction(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {_CONSTRUCTION_MODULES!r}:
            raise AssertionError('off-state loaded construction: ' + fullname)
sys.meta_path.insert(0, RejectConstruction())
def reject_effect(event, args):
    assert event not in ('subprocess.Popen', 'os.system', 'os.posix_spawn', 'socket.connect'), event
    if event == 'open':
        mode, flags = args[1:3]
        assert not (isinstance(mode, str) and any(c in mode for c in 'wax+')), args
        assert not (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)), args
sys.addaudithook(reject_effect)
from loopx.cli_commands.turn_post_writeback import drain_committed_turn_post_writeback_hooks
from loopx.capabilities.periodic_report.post_writeback_hook import periodic_report_post_writeback_hooks_for_goal
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter('always')
    assert periodic_report_post_writeback_hooks_for_goal(
        registry_path=Path({str(registry)!r}), runtime_root=Path({str(runtime)!r}),
        goal_id='fixture-goal') == ()
    assert drain_committed_turn_post_writeback_hooks(
        registry_path=Path({str(registry)!r}), runtime_root=Path({str(runtime)!r}),
        identity=SimpleNamespace(goal_id='fixture-goal'), goal_ref=None,
        available_capabilities=['shell']) == {{}}
expected = {warning!r}
assert len(caught) == (2 if expected else 0), [str(w.message) for w in caught]
assert expected is None or all(expected in str(w.message) for w in caught)
assert not set({_CONSTRUCTION_MODULES!r}) & set(sys.modules)
"""
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(_SOURCE), "LOOPX_USAGE_PING": "0"},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_lazy_report_exports_retain_owner_identity_and_patch_points(tmp_path, monkeypatch):
    import importlib
    import loopx.capabilities.periodic_report.post_writeback_hook as report
    import loopx.cli_commands.turn_post_writeback as turn

    for module in (report, turn):
        for name, owner in module._LAZY_EXPORTS.items():
            original = getattr(importlib.import_module(owner), name)
            assert getattr(module, name) is original
            assert getattr(module, name) is original
            assert name in dir(module)
        with pytest.raises(AttributeError):
            getattr(module, "unknown_report_export")
    wildcard = {}
    exec("from loopx.cli_commands.turn_post_writeback import *", wildcard)
    assert wildcard["read_report_source_history"] is turn.read_report_source_history
    assert wildcard["periodic_report_post_writeback_hooks_for_goal"] is report.periodic_report_post_writeback_hooks_for_goal
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "fixture-goal", "control_plane": {
        "periodic_report": {"enabled": True, "profile_preset": "weekly",
                            "route_ref": "fixture-route"},
    }}]}), encoding="utf-8")
    seen = []
    original = report.build_periodic_report_preset_activation
    def activation(preset):
        seen.append(preset)
        return original(preset)
    monkeypatch.setattr(report, "build_periodic_report_preset_activation", activation)
    hooks = report.periodic_report_post_writeback_hooks_for_goal(
        registry_path=registry, runtime_root=tmp_path / "runtime", goal_id="fixture-goal")
    assert seen == ["weekly"]
    assert len(hooks) == 1 and hooks[0].hook_id == "periodic_report.runtime_trigger"
    expected = original("weekly")
    assert hooks[0].policy_version == "weekly-" + expected["profile_digest"][-16:]
    sentinel = object()
    monkeypatch.setattr(turn, "periodic_report_post_writeback_hooks_for_goal", lambda **_: ())
    from types import SimpleNamespace
    monkeypatch.setattr(turn, "read_heartbeat_settlement", lambda *a, **k: sentinel)
    assert turn.drain_committed_turn_post_writeback_hooks(
        registry_path=registry, runtime_root=tmp_path / "runtime",
        identity=SimpleNamespace(goal_id="fixture-goal"), goal_ref=None,
        available_capabilities=None) == {}
