"""Default native routing may omit a derivable argument, never its authority."""
from __future__ import annotations

import os
import shlex

import pytest

from loopx.cli import build_parser
from loopx.cli_commands.context_machine import apply_context_machine_options
from loopx.heartbeat_prompt import build_heartbeat_prompt


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Native continuity owns POSIX defaults")


def prompt(tmp_path, *, custom=False, turn_envelope=False, default_capture=False):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    context = (tmp_path / "custom-context") if custom else runtime / "context-delivery"
    context.mkdir(mode=0o700)
    captures = context if default_capture else tmp_path / "captures"
    if not default_capture:
        captures.mkdir(mode=0o700)
    registry = tmp_path / "absent-registry.json"
    payload = build_heartbeat_prompt(goal_id="route-goal", agent_id="route-worker", thin=True,
        runtime_root=runtime, registry_path=registry, runtime_profile="codex_app_heartbeat",
        context_delivery="on", decision_output_root=captures, codex_context_root=str(context),
        turn_instance_id="original-route-turn", turn_envelope=turn_envelope)
    return payload, runtime, registry, context, captures


@pytest.mark.parametrize("turn_envelope", [False, True])
@pytest.mark.parametrize("default_capture", [False, True])
def test_default_native_guard_normalizes_to_the_complete_explicit_route(tmp_path, turn_envelope, default_capture):
    payload, runtime, registry, context, captures = prompt(tmp_path, turn_envelope=turn_envelope, default_capture=default_capture)
    guard = shlex.split(payload["quota_guard_command"])
    assert "--codex-context-root" not in guard
    assert guard[guard.index("--context-delivery") + 1] == "on"
    assert guard[guard.index("--turn-instance-id") + 1] == "${LOOPX_TURN:?}"
    assert "LOOPX_TURN=original-route-turn" in payload["task_body"]
    assert ("--decision-output-root" not in guard) is default_capture
    assert "--runtime-root" in guard
    implicit = build_parser().parse_args(guard[1:])
    explicit_args = [*guard[1:], "--codex-context-root", str(context)]
    if default_capture:
        explicit_args.extend(["--decision-output-root", str(captures)])
    explicit = build_parser().parse_args(explicit_args)
    for args in (implicit, explicit):
        apply_context_machine_options(args, registry_path=registry, runtime_root_arg=str(runtime))
    assert vars(implicit) == vars(explicit)
    assert implicit.codex_context_root == str(context)
    assert not registry.exists() and list(context.iterdir()) == []


@pytest.mark.parametrize("turn_envelope", [False, True])
def test_custom_native_route_stays_explicit(tmp_path, turn_envelope):
    payload, _, _, context, _ = prompt(tmp_path, custom=True, turn_envelope=turn_envelope)
    for key in ("quota_guard_command", "thin_prompt_command"):
        guard = shlex.split(payload[key])
        assert guard[guard.index("--codex-context-root") + 1] == str(context)


@pytest.mark.parametrize("unsafe", ["public", "symlink"])
def test_derived_native_root_rejects_unsafe_paths_without_repair(tmp_path, unsafe):
    payload, runtime, registry, context, _ = prompt(tmp_path)
    if unsafe == "symlink":
        context.rmdir()
        target = tmp_path / "foreign-context"
        target.mkdir(mode=0o700)
        context.symlink_to(target, target_is_directory=True)
    else:
        context.chmod(0o755)
        target = context
    before = target.stat().st_mode
    guard = shlex.split(payload["quota_guard_command"])
    args = build_parser().parse_args(guard[1:])
    with pytest.raises(ValueError, match="private physical directory"):
        apply_context_machine_options(args, registry_path=registry, runtime_root_arg=str(runtime))
    assert target.stat().st_mode == before and list(target.iterdir()) == []
    assert not registry.exists()
