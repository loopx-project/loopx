from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from loopx.control_plane.coordination.coordination_state_contract_generated import (
    LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA,
)
from loopx.control_plane.coordination.legacy_writer_fence import (
    LegacyCoordinationWriterFenced,
    legacy_coordination_write_remediation,
    legacy_coordination_todo_lock_path,
    legacy_coordination_writer_fence_path,
    legacy_todo_write_transaction,
    require_legacy_coordination_write_allowed,
)
from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
)
from loopx.file_lock import lock_holder_path
from loopx.todos import add_goal_todo

SPLIT_ROOT_GOAL_ID = "legacy-fence-split-root"
SPLIT_ROOT_AGENT = "codex-author"


def _write_split_root_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path]:
    """A registry whose common_runtime_root differs from the CLI override root."""

    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {SPLIT_ROOT_GOAL_ID}",
                "updated_at: 2026-09-04T00:00:00+00:00",
                "---",
                "",
                "## Agent Todo",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    runtime_registry = tmp_path / "runtime-registry"
    runtime_override = tmp_path / "runtime-override"
    runtime_registry.mkdir()
    runtime_override.mkdir()
    registry = tmp_path / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_registry),
                "goals": [
                    {
                        "id": SPLIT_ROOT_GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(repo),
                        "state_file": state.name,
                        "adapter": {"kind": "harness_self_improvement"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [SPLIT_ROOT_AGENT],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry, state, runtime_registry, runtime_override


def _engage_fence(runtime_root: Path) -> None:
    fence_path = legacy_coordination_writer_fence_path(
        runtime_root=runtime_root,
        goal_id=SPLIT_ROOT_GOAL_ID,
    )
    fence_path.parent.mkdir(parents=True)
    fence_path.write_text(json.dumps({"state": "present"}), encoding="utf-8")


REPO = Path(__file__).resolve().parents[2]


def _blocked_effect_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "loopx.control_plane.coordination.legacy_writer_fence.effect_runtime_result",
        lambda *_args, **_kwargs: {
            "status": "blocked",
            "reason_code": "legacy_coordination_writer_fenced",
            "authority_mode": "file_v0",
        },
    )


def test_missing_fence_preserves_default_without_starting_typescript(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "loopx.control_plane.coordination.legacy_writer_fence.effect_runtime_result",
        lambda *_args, **_kwargs: pytest.fail("default path must not start runtime"),
    )

    require_legacy_coordination_write_allowed(
        runtime_root=tmp_path,
        goal_id="goal-a",
    )


def test_present_fence_delegates_to_typescript_and_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = legacy_coordination_writer_fence_path(
        runtime_root=tmp_path,
        goal_id="goal-a",
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"state": "present"}), encoding="utf-8")
    captured: dict[str, object] = {}

    blocked = {
        "schema_version": LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA,
        "status": "blocked",
        "reason_code": "legacy_coordination_writer_fenced",
        "authority_mode": "file_v0",
        "fence_id": "fence-a",
    }

    def invoke(method: str, params: dict[str, object]) -> dict[str, object]:
        captured.update(method=method, params=params)
        return dict(blocked)

    monkeypatch.setattr(
        "loopx.control_plane.coordination.legacy_writer_fence.effect_runtime_result",
        invoke,
    )

    with pytest.raises(LegacyCoordinationWriterFenced) as exc_info:
        require_legacy_coordination_write_allowed(
            runtime_root=tmp_path,
            goal_id="goal-a",
        )

    assert exc_info.value.code == "legacy_coordination_writer_fenced"
    assert captured["method"] == "coordination.local_authority.legacy_write_check"
    # The adapter renders the operator remediation from the guard's data and
    # keeps the complete check result under its contract name.
    assert str(exc_info.value) == (
        "legacy coordination writer is fenced; use the promoted canonical authority "
        "(file_v0) for goal goal-a; fence fence-a; the primary record was not changed"
    )
    assert exc_info.value.payload == {"write_check": blocked}


_RENDER_CASES: list[tuple[str, dict[str, object], str]] = [
    (
        "blocked_file_v0",
        {"status": "blocked", "reason_code": "legacy_coordination_writer_fenced",
         "authority_mode": "file_v0", "fence_id": "legacy-writer-fence:goal-a:state-1"},
        "legacy coordination writer is fenced; use the promoted canonical authority "
        "(file_v0) for goal goal-a; fence legacy-writer-fence:goal-a:state-1; "
        "the primary record was not changed",
    ),
    (
        "blocked_opaque_profile",
        {"status": "blocked", "reason_code": "legacy_coordination_writer_fenced",
         "authority_mode": "profile-x", "fence_id": "fence-b"},
        "legacy coordination writer is fenced; use the promoted canonical authority "
        "(profile-x) for goal goal-a; fence fence-b; the primary record was not changed",
    ),
    (
        "blocked_single_pass_substitution",
        {"status": "blocked", "reason_code": "legacy_coordination_writer_fenced",
         "authority_mode": "file_v0", "fence_id": "fence $&{goal_id}"},
        "legacy coordination writer is fenced; use the promoted canonical authority "
        "(file_v0) for goal goal-a; fence fence $&{goal_id}; the primary record was not changed",
    ),
    (
        "blocked_without_binding_facts",
        {"status": "blocked", "reason_code": "legacy_coordination_writer_fenced"},
        "legacy coordination writer is fenced; use the promoted canonical authority "
        "(unknown_fail_closed) for goal goal-a; fence unknown; the primary record was not changed",
    ),
    (
        "failed_with_reason",
        {"status": "failed", "reason_code": "legacy_writer_fence_read_failed",
         "reason": "legacy coordination writer fence must be engaged",
         "authority_mode": "unknown_fail_closed"},
        "legacy coordination writer fence must be engaged",
    ),
    (
        "failed_without_reason",
        {"status": "failed", "reason_code": "legacy_writer_fence_read_failed",
         "reason": "", "authority_mode": "unknown_fail_closed"},
        "legacy coordination writer fence check failed",
    ),
    (
        "invalid_check_request",
        {"status": "failed", "reason_code": "invalid_legacy_coordination_write_check",
         "reason": "legacy coordination write check request schema mismatch",
         "authority_mode": "unknown_fail_closed"},
        "legacy coordination write check request schema mismatch",
    ),
]


@pytest.mark.parametrize(("name", "guard", "expected"), _RENDER_CASES, ids=[c[0] for c in _RENDER_CASES])
def test_remediation_renders_identically_in_python_and_typescript(
    name: str, guard: dict[str, object], expected: str
) -> None:
    """Both caller adapters render one text from the same guard data."""

    assert legacy_coordination_write_remediation("goal-a", guard) == expected
    module = (REPO / "loopx/control_plane/coordination/legacy_writer_fence.ts").as_uri()
    script = (
        f"import {{legacyCoordinationWriteRemediation}} from {json.dumps(module)};"
        "let input='';for await (const chunk of process.stdin) input += chunk;"
        "process.stdout.write(legacyCoordinationWriteRemediation('goal-a', JSON.parse(input)));"
    )
    rendered = subprocess.run(
        ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
        input=json.dumps(guard), capture_output=True, text=True, check=True, timeout=45,
    )
    assert rendered.stdout == expected, name


def test_todo_write_transaction_fences_under_the_effective_runtime_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A fence engaged under --runtime-root must fence the legacy writer."""

    registry, state, _runtime_registry, runtime_override = _write_split_root_fixture(
        tmp_path
    )
    _engage_fence(runtime_override)
    _blocked_effect_runtime(monkeypatch)

    with pytest.raises(LegacyCoordinationWriterFenced):
        with legacy_todo_write_transaction(
            registry,
            SPLIT_ROOT_GOAL_ID,
            state,
            SPLIT_ROOT_AGENT,
            "todo_add",
            False,
            runtime_root=runtime_override,
        ):
            pytest.fail("transaction body must not run while fenced")


def test_todo_write_transaction_keeps_registry_root_without_runtime_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Omitting runtime_root keeps the registry-derived root (backward compat)."""

    registry, state, runtime_registry, _runtime_override = _write_split_root_fixture(
        tmp_path
    )
    _engage_fence(runtime_registry)
    _blocked_effect_runtime(monkeypatch)

    with pytest.raises(LegacyCoordinationWriterFenced):
        with legacy_todo_write_transaction(
            registry,
            SPLIT_ROOT_GOAL_ID,
            state,
            SPLIT_ROOT_AGENT,
            "todo_add",
            False,
        ):
            pytest.fail("transaction body must not run while fenced")


def test_todo_write_lock_lands_under_the_effective_runtime_root(
    tmp_path: Path,
) -> None:
    """The todo mutex must serialize with promotion under the override root."""

    registry, state, runtime_registry, runtime_override = _write_split_root_fixture(
        tmp_path
    )
    override_lock = legacy_coordination_todo_lock_path(
        runtime_root=runtime_override,
        goal_id=SPLIT_ROOT_GOAL_ID,
    )

    registry_lock = legacy_coordination_todo_lock_path(
        runtime_root=runtime_registry,
        goal_id=SPLIT_ROOT_GOAL_ID,
    )

    with legacy_todo_write_transaction(
        registry,
        SPLIT_ROOT_GOAL_ID,
        state,
        SPLIT_ROOT_AGENT,
        "todo_add",
        False,
        runtime_root=runtime_override,
    ):
        assert lock_holder_path(override_lock).exists()
        assert not lock_holder_path(registry_lock).exists()


def test_goal_todo_add_uses_provider_at_override_root_when_promoted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """End to end: promoted todo add reads authority from the override root."""

    registry, state, _runtime_registry, runtime_override = _write_split_root_fixture(
        tmp_path
    )
    _engage_fence(runtime_override)
    captured: dict[str, object] = {}

    def missing_provider(
        method: str, params: dict[str, object], **_kwargs: object
    ) -> dict[str, object]:
        captured.update(method=method, params=params)
        return {
            "status": "missing",
            "source_authority": "file_v0",
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        }

    monkeypatch.setattr(
        "loopx.control_plane.todos.provider_create.effect_runtime_result",
        missing_provider,
    )
    state_before = state.read_text(encoding="utf-8")

    with pytest.raises(LocalCoordinationAuthorityUnavailable) as exc_info:
        add_goal_todo(
            registry_path=registry,
            goal_id=SPLIT_ROOT_GOAL_ID,
            role="agent",
            text="Deliver one bounded control-plane change.",
            task_class="advancement_task",
            claimed_by=SPLIT_ROOT_AGENT,
            runtime_root_arg=str(runtime_override),
        )

    assert exc_info.value.code == "local_authority_todo_list_unavailable"
    assert captured["method"] == "coordination.local_authority.todo_create"
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["runtime_root"] == str(runtime_override.resolve())
    assert state.read_text(encoding="utf-8") == state_before
