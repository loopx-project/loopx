"""Failure readback must not require another execution or expose provider prose."""
from __future__ import annotations

import contextlib
import io
import json

import pytest

from loopx.cli import main
from loopx.cli_commands.turn_rendering import render_loopx_turn_execution_markdown
from loopx.control_plane.turn_driver.host_failure import BuiltInHostError, build_host_failure_record
from tests.test_loopx_turn_driver import _write_live_fixture


@pytest.mark.parametrize("kind", [
    "auth_failed", "contract_rejected", "executor_timeout", "output_budget_exhausted",
    "provider_capacity", "provider_overloaded", "quota_exhausted", "rate_limited",
    "session_missing", "transport_lost", "unknown",
])
def test_every_classified_failure_is_visible_without_raw_provider_details(kind):
    failure = build_host_failure_record(kind, attempt=1)
    payload = {"status": "failed", "result_kind": "host_failure", "host_failure": failure,
               "provider_message": "private provider detail"}
    before = json.dumps(payload, sort_keys=True)
    rendered = render_loopx_turn_execution_markdown(payload)
    assert f"- host_failure_kind: {kind}" in rendered
    assert f"- host_failure_retryable: {failure['retryable']}" in rendered
    assert "- host_failure_attempt: 1" in rendered
    assert "private provider detail" not in rendered
    assert json.dumps(payload, sort_keys=True) == before


def test_exhausted_retry_policy_is_a_hint_not_permission_to_execute():
    rendered = render_loopx_turn_execution_markdown({
        "status": "failed", "host_failure": build_host_failure_record("rate_limited", attempt=3),
    })
    assert "- host_failure_attempt: 3" in rendered
    assert "- host_failure_max_attempts: 3" in rendered
    assert "- host_failure_backoff_seconds: 240" in rendered
    assert "Recovery must still be admitted against the original Turn journal." in rendered


@pytest.mark.parametrize("kind", ["auth_failed", "rate_limited", "executor_timeout"])
def test_cli_replays_failed_journal_as_markdown_without_another_host_call(tmp_path, monkeypatch, kind):
    project, runtime, registry = _write_live_fixture(tmp_path)
    calls = []

    def unavailable_host(request, **kwargs):
        calls.append(request["turn_key"])
        raise BuiltInHostError(f"codex_cli_{kind}", failure_kind=kind)

    monkeypatch.setattr("loopx.cli_commands.turn_run_once.run_codex_cli_host", unavailable_host)
    args = ["--registry", str(registry), "--runtime-root", str(runtime), "turn", "run-once",
            "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
            "--host", "codex-cli", "--project", str(project), "--no-global-sync", "--execute"]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main([*args, "--format", "json"])
    first = json.loads(output.getvalue())
    assert code == 1 and first["host_failure"]["kind"] == kind
    journals = {path: path.read_bytes() for path in (runtime / "goals").rglob("turns/*.json")}
    assert journals
    replay = io.StringIO()
    with contextlib.redirect_stdout(replay):
        replay_code = main([*args, "--resume-turn-key", first["resume_turn_key"], "--format", "markdown"])
    assert replay_code == 1
    assert f"- host_failure_kind: {kind}" in replay.getvalue()
    assert "- host_failure_attempt: 1" in replay.getvalue()
    assert len(calls) == 1
    assert all(path.read_bytes() == original for path, original in journals.items())
