"""Built-in endpoints name what is missing so the owner can repair it in place."""

from __future__ import annotations

import sys
from pathlib import Path

import loopx.chat_endpoint_catalog as chat_endpoint_catalog
from loopx.control_plane.turn_driver import host_binding
from loopx.chat_endpoint_catalog import builtin_chat_endpoints


def _rows(monkeypatch, tmp_path: Path, **bins: str) -> dict[str, dict]:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "set")
    rows = builtin_chat_endpoints(
        codex_bin=bins.get("codex_bin", "definitely-missing-codex"),
        claude_bin=bins.get("claude_bin", "definitely-missing-claude"),
        kiro_cli_bin=bins.get("kiro_cli_bin", "definitely-missing-kiro"),
        runtime_root=tmp_path,
    )
    return {row["agent_id"]: row for row in rows}


def test_missing_executables_and_keys_name_their_requirement(monkeypatch, tmp_path: Path) -> None:
    rows = _rows(monkeypatch, tmp_path)
    for agent_id, executable in (("codex", "definitely-missing-codex"), ("claude-code", "definitely-missing-claude")):
        assert rows[agent_id]["available"] is False
        assert rows[agent_id]["unavailable_reason"] == "executable_not_found"
        assert rows[agent_id]["executable"] == executable
    assert rows["anthropic-api"]["available"] is False
    assert rows["anthropic-api"]["unavailable_reason"] == "api_key_not_set"
    assert rows["anthropic-api"]["environment_variable"] == "ANTHROPIC_API_KEY"


def test_present_requirements_report_available_without_a_reason(monkeypatch, tmp_path: Path) -> None:
    rows = _rows(monkeypatch, tmp_path, codex_bin=sys.executable)
    assert rows["codex"]["available"] is True
    assert rows["codex"]["executable"] == sys.executable
    assert "unavailable_reason" not in rows["codex"]
    assert rows["openai-api"]["available"] is True
    assert rows["openai-api"]["environment_variable"] == "OPENAI_API_KEY"
    assert "unavailable_reason" not in rows["openai-api"]


def test_managed_profile_refusal_exposes_its_real_configuration_source(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    monkeypatch.setenv("LOOPX_TURN_REASONING_EFFORT", "fixture")
    monkeypatch.setattr(
        host_binding,
        "dsh_runtime_importable",
        lambda *args, **kwargs: True,
    )

    row = chat_endpoint_catalog.managed_host_capability(tmp_path)

    assert row["unavailable_remediation"] == [
        "correct_execution_profile",
        "select_individual_host",
    ]
    assert row["execution_profile_details"]["reasoning_effort_env_var"] == (
        "LOOPX_TURN_REASONING_EFFORT"
    )
    assert row["credential_env"] == "DEEPSEEK_API_KEY"
    assert "fixture-secret" not in str(row)
