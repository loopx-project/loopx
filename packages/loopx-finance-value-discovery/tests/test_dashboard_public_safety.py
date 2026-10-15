from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx_finance_value_discovery.cli import run
from loopx_finance_value_discovery.dashboard import (
    build_finance_research_dashboard_packet,
)
from loopx_finance_value_discovery import presentation_compat
from loopx_finance_value_discovery import presentation_validation
from loopx_finance_value_discovery.presentation_validation import plain_text


def dashboard_request():
    return json.loads(
        (Path(__file__).parents[1] / "examples/research-dashboard.json").read_text()
    )


@pytest.mark.parametrize(
    "private_text",
    [
        "source=/Users/alice/private.json",
        "path=/private/notes.md",
        "source=~/private.json",
        "source=file:///Users/alice/private.json",
        "source=//Users/alice/private.json",
    ],
)
def test_public_dashboard_rejects_paths_in_assignment_context(private_text):
    payload = dashboard_request()
    payload["title"] = f"Research summary; {private_text}"

    with pytest.raises(ValueError, match="local path"):
        build_finance_research_dashboard_packet(payload)


def test_path_classifier_keeps_ordinary_text_and_rejects_legacy_multislash():
    assert plain_text("Research covers /Users paths", context="summary") == (
        "Research covers /Users paths"
    )
    with pytest.raises(ValueError, match="local path"):
        plain_text("See ///tmp/private.json", context="summary")


def test_non_presentation_validation_keeps_legacy_path_guard_without_core_owner(
    monkeypatch,
):
    monkeypatch.setattr(presentation_validation, "_find_public_safe_local_path", None)

    with pytest.raises(ValueError, match="local path"):
        plain_text("/Users/alice/private.json", context="summary")


def test_dashboard_compatibility_requires_the_shared_path_classifier(monkeypatch):
    def import_without_shared_path_classifier(module_name):
        if module_name == presentation_compat._PRESENTATION_MODULE:
            return SimpleNamespace(load_presentation_view_validator=lambda: None)
        return SimpleNamespace()

    monkeypatch.setattr(
        presentation_compat, "import_module", import_without_shared_path_classifier
    )

    error = presentation_compat.presentation_api_error()
    assert error is not None
    assert "shared public-safe local-path classifier" in error


def test_lark_card_cli_rejects_assignment_prefixed_local_path(
    capsys, tmp_path
):
    payload = dashboard_request()
    payload["title"] = "Research summary; source=/Users/alice/private.json"
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload))

    assert run(["render-lark-card", "--input-json", str(path)]) == 1
    output = capsys.readouterr().out
    assert "local path" in output
    assert "/Users/alice/private.json" not in output
