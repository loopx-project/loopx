"""Explicit outgoing checks distinguish blockers, advisories and unread scope."""
from __future__ import annotations

import subprocess

import pytest

from loopx import contract
from tests.test_contract_scan_missing_roots import _registry
from tests.test_contract_credential_pattern_precision import (
    AUTH_HEADER, BEARER, PRIVATE_LARK_TENANT_HOST, REAL_BEARER_VALUE,
)


def test_selected_examples_keep_locations_without_claiming_a_clean_scan(tmp_path):
    outgoing = tmp_path / "request.txt"
    outgoing.write_text(
        'endpoint = "http://192.168.' + '10.10"\n'
        'auth = "Authorization: Bearer ${SERVICE_TOKEN}"\n'
    )
    checked = contract.check_contract(registry_path=_registry(tmp_path),
        runtime_root_override=None, scan_roots=[outgoing], limit=0)
    assert checked["ok"] is True
    assert checked["public_boundary_scan"]["state"] == "completed"
    assert checked["public_boundary_scan"]["ok"] is True
    assert set(checked["public_boundary_scan"]["warnings"]) == {
        "request.txt:1: private_ip", "request.txt:2: credential",
    }
    assert all(hit in checked["warnings"] for hit in checked["public_boundary_scan"]["warnings"])
    assert not any("scan clean" in text for text in checked["checks"])


@pytest.mark.parametrize("text", [
    f"{AUTH_HEADER} {BEARER} {REAL_BEARER_VALUE}",
    f"https://{PRIVATE_LARK_TENANT_HOST}/wiki/example",
])
def test_selected_literal_credentials_and_private_links_still_block(tmp_path, text):
    outgoing = tmp_path / "request.txt"
    outgoing.write_text(text)
    checked = contract.check_contract(registry_path=_registry(tmp_path),
        runtime_root_override=None, scan_roots=[outgoing], limit=0)
    assert checked["ok"] is False
    assert checked["public_boundary_scan"]["ok"] is False
    assert any(row["code"] == "public_boundary_violation" for row in checked["error_diagnostics"])


@pytest.mark.parametrize("tracked", [False, True])
@pytest.mark.parametrize("directory", [False, True])
def test_explicit_private_selection_cannot_hide_behind_git_exclusion(
    tmp_path, monkeypatch, tracked, directory,
):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".local/\n")
    private = tmp_path / ".local" / "notes.md"
    private.parent.mkdir()
    private.write_text("Owner-only synthetic notes.\n")
    if tracked:
        subprocess.run(["git", "-C", str(tmp_path), "add", "-f", ".local/notes.md"], check=True)

    def must_not_read(paths):
        assert list(paths) == [], "explicit private scope was opened before rejection"
        return iter(())

    monkeypatch.setattr(contract, "iter_utf8_file_reads", must_not_read)
    checked = contract.scan_public_boundary([private.parent if directory else private])
    assert checked["ok"] is False
    assert checked["blocking_hits"] and all("local_private_state" in hit for hit in checked["hits"])
    assert checked["scanned_files"] == 0


def test_tracked_private_state_is_not_public_even_without_content_matches(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    private = tmp_path / "ACTIVE_GOAL_STATE.md"
    private.write_text("Owner-only synthetic notes.\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", private.name], check=True)
    checked = contract.scan_public_boundary([tmp_path])
    assert checked["ok"] is False
    assert checked["blocking_hits"] == ["ACTIVE_GOAL_STATE.md: local_private_state"]


def test_public_check_reobserves_withdrawn_tracked_source_permission(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    outgoing = tmp_path / "reference.md"
    outgoing.write_text(f"https://{PRIVATE_LARK_TENANT_HOST}/wiki/example")
    subprocess.run(["git", "-C", str(tmp_path), "add", outgoing.name], check=True)
    allowed = contract.scan_public_boundary([outgoing],
        registry={"public_boundary": {"tracked_private_doc_urls": "allow"}})
    assert allowed["ok"] and allowed["allowed_hits"]
    withdrawn = contract.scan_public_boundary([outgoing], registry={})
    assert not withdrawn["ok"] and withdrawn["blocking_hits"]


def test_invalid_utf8_is_unexamined_and_never_clean(tmp_path):
    outgoing = tmp_path / "request.md"
    outgoing.write_bytes(b"\xff\xfe")
    checked = contract.check_contract(registry_path=_registry(tmp_path),
        runtime_root_override=None, scan_roots=[outgoing], limit=0)
    assert checked["ok"] is False
    assert checked["public_boundary_scan"]["unreadable_files"] == ["request.md: invalid UTF-8"]
    assert not any("scan clean" in text for text in checked["checks"])


@pytest.mark.parametrize("command", [
    ["status"], ["diagnose"], ["review-packet", "--goal-id", "fixture"],
    ["quota", "should-run", "--goal-id", "fixture"],
    ["turn", "plan", "--goal-id", "fixture", "--agent-id", "fixture"],
    ["turn", "managed-step", "--goal-id", "fixture", "--agent-id", "fixture", "--turn-key", "fixture"],
    ["turn", "run-once", "--goal-id", "fixture", "--agent-id", "fixture", "--project", "."],
    ["ready-score"], ["agent-directory", "--goal-id", "fixture"],
    ["global-summary"], ["global-gates"], ["global-todos"], ["global-risks"],
])
@pytest.mark.parametrize("option", ["--scan-root", "--scan-path"])
def test_operational_commands_reject_retired_scan_options(command, option):
    from loopx.cli import build_parser

    parser = build_parser()
    parser.parse_args(command)
    with pytest.raises(SystemExit) as error:
        parser.parse_args([*command, option, "."])
    assert error.value.code == 2


def test_explicit_check_keeps_outgoing_selection_options():
    from loopx.cli import build_parser

    selected = build_parser().parse_args(["check", "--scan-root", ".", "--scan-path", "outgoing.md"])
    assert selected.scan_path == ["outgoing.md"]
