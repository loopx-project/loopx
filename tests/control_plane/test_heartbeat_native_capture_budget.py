"""Native capture keeps complete recovery instructions within the host limit."""
from __future__ import annotations

import pytest

from loopx.heartbeat_prompt import build_heartbeat_prompt
from tests.control_plane.cli_output_fixture import stable_budget_fixture_root


def native_capture(tmp_path, *, path_width, cli_width, envelope, scopes=()):
    with stable_budget_fixture_root(tmp_path) as base:
        root = (base / ("p" * (path_width - len(str(base.resolve())) - 1))).resolve()
        root.mkdir()
        cli_root = (base / ("c" * (cli_width - len(str(base.resolve())) - 7))).resolve()
        cli = str(cli_root / "loopx")
        assert len(str(root)) == path_width and len(cli) == cli_width
        captures, context = root / "captures", root / "context"
        captures.mkdir()
        context.mkdir()
        result = build_heartbeat_prompt(
            goal_id="settlement-cli-fixture", agent_id="codex-settlement-cli",
            runtime_profile="codex_app_ssh_goal", thin=True,
            registry_path=root / "project/.loopx/registry.json", runtime_root=root / "runtime",
            cli_bin=cli, decision_output_root=captures, codex_context_root=str(context),
            context_delivery="on", turn_envelope=envelope, agent_scopes=list(scopes),
        )
        body = result["task_body"]
        assert len(body) <= 4000
        assert result["interface_budget"]["max_chars"] == 4000
        for path in (cli, str(root / "runtime"), str(root / "project/.loopx/registry.json"),
                     str(captures), str(context)):
            assert path in body and path in result["quota_guard_command"]
        assert "--runtime-profile codex_app_ssh_goal --begin-turn" in body
        assert "Never rebuild static refresh/spend" in body
        assert "ambiguous effects: readback/recovery, no blind retry" in body
        assert "Local outcomes never replace root continuation/budget/stop" in body
        assert "Only user /goal resume reactivates; then rerun quota" in body
        if envelope:
            assert "Read complete same-invocation detail_ref.full_decision before selection/work/settlement" in body
        return result


@pytest.mark.parametrize("envelope", [False, True])
@pytest.mark.parametrize("path_width,cli_width", [(160, 140), (133, 180)])
def test_native_capture_preserves_recovery_at_the_real_host_limit(tmp_path, envelope, path_width, cli_width):
    native_capture(tmp_path, path_width=path_width, cli_width=cli_width, envelope=envelope)


def test_native_capture_still_rejects_oversized_work_instructions(tmp_path):
    with pytest.raises(ValueError, match="4000-character"):
        native_capture(tmp_path, path_width=160, cli_width=140, envelope=True,
                       scopes=tuple(letter * 320 for letter in "stu"))


def test_native_capture_preserves_custom_policy_paragraphs(tmp_path):
    material = "Read the registered source first.\n\nThen judge its current evidence."
    permission = "Keep user authority.\n\nStop the affected action if authority is missing."
    captures, context = tmp_path / "captures", tmp_path / "context"
    captures.mkdir()
    context.mkdir()
    packet = build_heartbeat_prompt(
        goal_id="policy-goal", agent_id="policy-worker", thin=True,
        runtime_profile="codex_app_ssh_goal", context_delivery="on",
        decision_output_root=captures, codex_context_root=str(context),
        material_queue_rule=material, permission_rule=permission,
    )
    assert material in packet["task_body"]
    assert permission in packet["task_body"]
