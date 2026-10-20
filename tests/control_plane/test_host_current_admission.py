"""Current source quota and health must still allow the actual native launch."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.configure_goal import configure_goal
from loopx.control_plane.turn_driver import codex_cli, codex_operation_host
from loopx.global_registry import sync_project_registry_to_global
from tests.control_plane.test_codex_operation_launch import _bindings, _cli, _fixture
from tests.test_loopx_turn_codex_cli import _fake_codex


def _quota(registry: Path, runtime: Path, project: Path) -> dict:
    _, payload = _cli([
        "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "quota", "should-run", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
        "--host-surface", "generic_cli", "--scheduler-owner", "outer_controller",
        "--execution-mode", "isolated_headless", "--scan-root", str(project),
    ])
    return payload


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("refusal", ["source_compute_zero", "missing_state"])
@pytest.mark.parametrize("host", ["exec", "operation"])
def test_current_source_admission_preempts_stale_mirror_native_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, refusal: str, host: str,
) -> None:
    runtime, source, native, model, arguments = _fixture(tmp_path, monkeypatch, provider)
    project = source.parent.parent
    synchronized = sync_project_registry_to_global(
        registry_path=source, runtime_root_override=str(runtime), goal_id="loopx-turn-fixture",
    )
    assert synchronized["ok"], synchronized
    mirror = runtime / "registry.global.json"
    before_mirror = mirror.read_bytes()
    source_payload = json.loads(source.read_text())
    state = project / source_payload["goals"][0]["state_file"]
    original_state = state.read_bytes()
    if host == "exec":
        executable, model = _fake_codex(tmp_path)
        native = tmp_path / "native-process"
        executable.write_text(executable.read_text().replace(
            "args = sys.argv[1:]", f"pathlib.Path({str(native)!r}).write_text('native')\nargs = sys.argv[1:]", 1,
        ))
        monkeypatch.setenv("FAKE_CODEX_LOG", str(model))
        arguments.remove("--codex-operation-tools")
        arguments[arguments.index("--codex-bin") + 1] = str(executable)
    arguments[arguments.index("--registry") + 1] = str(mirror)
    observations = []
    if refusal == "source_compute_zero":
        configured = configure_goal(registry_path=source, goal_id="loopx-turn-fixture", quota_compute=0, execute=True)
        assert configured["written"], configured
        current = _quota(source, runtime, project)
        assert current["should_run"] is False and "compute_quota_zero" in json.dumps(current), current
        assert _quota(mirror, runtime, project)["should_run"] is True
        observations.append(current)
    else:
        def lose_state_after_selection():
            state.rename(state.with_suffix(".saved"))
            for registry in (source, mirror):
                current = _quota(registry, runtime, project)
                assert current["should_run"] is False and current["status_health_ok"] is False, current
                assert current["state"] == "blocked_health", current
                observations.append(current)
        if host == "exec":
            original_prompt = codex_cli._prompt

            def lose_state_before_prompt(request):
                lose_state_after_selection()
                return original_prompt(request)

            monkeypatch.setattr(codex_cli, "_prompt", lose_state_before_prompt)
        else:
            original_start = codex_operation_host.CodexChatAgentSession.start

            def lose_state_before_start(**kwargs):
                lose_state_after_selection()
                return original_start(**kwargs)

            monkeypatch.setattr(codex_operation_host.CodexChatAgentSession, "start", lose_state_before_start)
    before_bindings = _bindings(runtime)
    code, payload = _cli([*arguments, "--execute"])
    assert observations and mirror.read_bytes() == before_mirror
    assert not native.exists() and not model.exists(), payload
    assert code == 1 and "goal_launch_not_allowed" in json.dumps(payload), payload
    assert _bindings(runtime) == before_bindings
    if refusal == "source_compute_zero":
        assert state.read_bytes() == original_state
    else:
        assert not state.exists()
    index = runtime / "goals/loopx-turn-fixture/runs/index.jsonl"
    assert not index.exists() or "quota_slot_spent" not in index.read_text()
