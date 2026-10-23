"""Built-in endpoints name what is missing so the owner can repair it in place."""

from __future__ import annotations

import sys
from pathlib import Path

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
