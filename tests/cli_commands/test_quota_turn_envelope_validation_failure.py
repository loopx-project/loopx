from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import loopx.cli_commands.quota as quota_command_module
from loopx.cli_commands.quota import handle_quota_command
from loopx.control_plane.quota.error_codes import QuotaCommandValidationError


def _print_payload_collector(out: list[str]):
    def print_payload(*call_args, **_kwargs):  # noqa: ANN002, ANN003 - CLI callback shape
        out.append(json.dumps(call_args[0]))

    return print_payload


@pytest.mark.parametrize("turn_envelope", [True, False])
def test_turn_envelope_renders_typed_failure_when_context_preparation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    turn_envelope: bool,
) -> None:
    """Regression: --begin-turn --turn-envelope must not raise UnboundLocalError.

    When ``prepare_quota_command_context`` fails scheduler execution-context
    validation, the CLI returns the typed validation payload; the post-try
    ``--turn-envelope`` renderer used to read the never-assigned
    ``scheduler_context`` local and crash (issue #3687).
    """
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{}", encoding="utf-8")

    def _fail_context(*_args, **_kwargs):
        raise QuotaCommandValidationError(
            "scheduler execution context validation failed"
        )

    monkeypatch.setattr(
        quota_command_module, "prepare_quota_command_context", _fail_context
    )

    args = argparse.Namespace(
        quota_command="should-run",
        goal_id="synthetic-goal",
        agent_id="synthetic-agent",
        begin_turn=True,
        turn_envelope=turn_envelope,
        format="json",
    )
    out: list[str] = []
    result = handle_quota_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=None,
        print_payload=_print_payload_collector(out),
        append_cli_rollout_event=lambda *args, **kwargs: {},
    )

    # A typed validation failure returns the failure exit code, not a crash.
    assert result in (0, 1)
    payload = json.loads(out[-1])
    assert payload["ok"] is False
    assert payload.get("decision") == "skip"
    assert payload.get("error_code")
    if turn_envelope:
        # The envelope is additive; on a failure payload it degrades to the
        # typed diagnostic instead of masking it with a renderer crash.
        assert payload.get("turn_envelope_skipped") or payload.get("view") == "turn_envelope"
