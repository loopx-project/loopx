"""Real public CLI cutover, registration drift and receipt recovery on local stores."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from tests.control_plane.shadow_e2e_fixture import REPO, workspace


def prepare(tmp_path: Path, provider: str, strategy: str | None = None, source_mode: str | None = None):
    ws = workspace(tmp_path, bootstrap=False)
    if strategy is not None or source_mode is not None:
        ws.state.write_text(
            ws.state.read_text().replace(
                "handoff_mode: hard_lease", f"handoff_mode: {source_mode or 'legacy'}"
            )
        )
    if provider == "sqlite":
        subprocess.run(
            [
                os.environ.get("LOOPX_CONTROL_PLANE_NODE", "node"),
                "--no-warnings",
                "--experimental-strip-types",
                "--experimental-sqlite",
                "loopx/control_plane/coordination/local_authority_provider.ts",
                "--runtime-root",
                str(ws.runtime),
                "--goal-id",
                ws.goal,
                "--execute",
            ],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        )
    assert (
        ws.cli("coordination-shadow", "bootstrap", "--execute")["bootstrap"]["status"]
        == "applied"
    )
    for index in range(3):
        ws.add(f"Preserve migration record {index}")
    drained = ws.drain(budget_seconds="60")
    assert drained["ok"] is True, drained
    arguments = () if strategy is None else ("--handoff-mode-migration", strategy)
    preview = ws.cli("coordination-shadow", "promote", *arguments)
    assert preview["promotion"]["status"] == "preview_ready", preview
    saved = tmp_path / "reviewed.json"
    saved.write_text(json.dumps(preview), encoding="utf-8")
    return ws, saved, preview


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("strategy", [None, "preserve", "hard_lease"])
def test_saved_plan_cutover_and_recovery_after_canonical_write_and_missing_legacy(
    tmp_path, provider, strategy
):
    ws, saved, preview = prepare(tmp_path, provider, strategy)
    arguments = ("coordination-shadow", "promote", "--reviewed-plan", str(saved))
    dry_run = ws.cli(*arguments)
    assert dry_run["promotion"]["status"] == "preview_ready"
    assert dry_run["executed"] is False
    applied = ws.cli(*arguments, "--execute")
    assert applied["promotion"]["status"] == "applied", applied
    assert applied["promotion"]["canonical_authority"] == f"{provider}_v0"
    assert (
        applied["promotion"]["promotion_plan_sha256"]
        == preview["promotion"]["plan"]["promotion_plan_sha256"]
    )
    created = ws.add("Continue after provider cutover")
    readback = ws.cli("todo", "list", "--todo-id", created["todo_id"])
    assert readback["authority_read"]["source_authority"] == f"{provider}_v0"
    ws.state.unlink()
    # Recovery follows durable proof, not a fresh source or shadow opt-in.
    config = json.loads(ws.registry.read_text())
    config["goals"][0]["coordination"].pop("runtime_shadow", None)
    ws.registry.write_text(json.dumps(config))
    for execution in [(), ("--execute",)]:
        replay = ws.cli(
            "coordination-shadow",
            "recover-promotion",
            "--reviewed-plan",
            str(saved),
            *execution,
        )
        assert replay["promotion"]["status"] == "replayed", replay
        assert replay["promotion"]["cursor"] == "1"
        assert (
            replay["promotion"]["provider_revision"]
            == applied["promotion"]["provider_revision"]
        )
        assert replay["executed"] is False
        assert not ws.state.exists()


def test_saved_plan_source_drift_does_not_freeze_legacy_writes(tmp_path):
    ws, saved, _ = prepare(tmp_path, "file")
    ws.add("New work before cutover")
    result = ws.cli(
        "coordination-shadow",
        "promote",
        "--reviewed-plan",
        str(saved),
        "--execute",
        success=False,
    )
    assert result["ok"] is False
    assert result["promotion"]["reason_code"] == "local_authority_reviewed_plan_changed"
    assert result["promotion"]["legacy_writer_fenced"] is False
    assert ws.add("Legacy writer remains usable")["ok"] is True


def test_saved_plan_rejects_policy_override_and_recovery_without_fence(tmp_path):
    ws, saved, _ = prepare(tmp_path, "file")
    result = ws.cli(
        "coordination-shadow",
        "promote",
        "--reviewed-plan",
        str(saved),
        "--minimum-operations",
        "1",
        "--execute",
        success=False,
    )
    assert result["ok"] is False and "owns qualification policy" in result["error"]
    result = ws.cli(
        "coordination-shadow",
        "recover-promotion",
        "--reviewed-plan",
        str(saved),
        "--execute",
        success=False,
    )
    assert result["ok"] is False
    assert (
        result["promotion"]["reason_code"]
        == "local_authority_writer_fence_not_verified"
    )


def test_saved_plan_rechecks_current_registered_agents(tmp_path):
    ws, saved, _ = prepare(tmp_path, "file", "preserve")
    registry = json.loads(ws.registry.read_text())
    registry["goals"][0]["coordination"]["registered_agents"].remove("agent-b")
    ws.registry.write_text(json.dumps(registry))
    result = ws.cli(
        "coordination-shadow",
        "promote",
        "--reviewed-plan",
        str(saved),
        "--execute",
        success=False,
    )
    assert result["ok"] is False
    assert result["promotion"]["reason_code"] == "promotion_registration_changed_retry"
    assert result["promotion"]["legacy_writer_fenced"] is False


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_default_promotion_preserves_soft_policy(tmp_path, provider):
    ws, saved, preview = prepare(tmp_path, provider, source_mode="soft_claim")
    policy = preview["promotion"]["plan"]["handoff_mode_migration"]
    assert policy["strategy"] == "preserve" and policy["target_mode"] == "soft_claim"
    applied = ws.cli("coordination-shadow", "promote", "--reviewed-plan", str(saved), "--execute")
    assert applied["ok"], applied
    shown = ws.cli("handoff-mode", "show")
    assert shown["handoff_mode"] == "soft_claim"
