#!/usr/bin/env python3
"""Exercise the public exact-release-commit qualification command."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from loopx import __version__  # noqa: E402
from loopx.control_plane.testing.actual_default_model_behavior_portfolio import (  # noqa: E402
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_COUNT,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT,
)
from loopx.control_plane.testing.release_commit_qualification import (  # noqa: E402
    EXPECTED_RESULT_SCHEMA_BY_QUALIFICATION,
    REQUIRED_QUALIFICATION_IDS,
    collect_release_source_identity,
)


DIGEST = "sha256:" + "5" * 64


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def summary(qualification_id: str) -> dict[str, object]:
    return {
        "pytest": {"passed_count": 1, "failed_count": 0, "skipped_count": 0},
        "ruff": {"violation_count": 0},
        "mypy": {"error_count": 0},
        "risk_canary": {
            "selected_check_count": 1,
            "failure_count": 0,
            "manual_hold_count": 0,
        },
        "full_public": {
            "ready": True,
            "shard_count": 1,
            "failure_count": 0,
            "timeout_count": 0,
        },
        "install_upgrade_host": {
            "install_passed": True,
            "upgrade_passed": True,
            "host_passed": True,
            "packaged_frontend_passed": True,
        },
        "public_boundary": {"scanned_path_count": 1, "violation_count": 0},
        "doubao_actual_default": {
            "model_id": "doubao-seed-1.6",
            "topology": "actual_default_one_arm",
            "scenario_count": ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT,
            "contrast_count": ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_COUNT,
            "contrast_failure_count": 0,
            "repeats_per_scenario": ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS,
            "actor_call_count": (
                ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT
                * ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS
            ),
            "failure_count": 0,
            "skip_count": 0,
            "qualification_passed": True,
        },
    }[qualification_id]


def manifest(source: dict[str, object]) -> dict[str, object]:
    completed_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": "exact_release_commit_qualification_manifest_v0",
        "candidate": source,
        "claims": {},
        "qualifications": {
            qualification_id: {
                "schema_version": "exact_release_check_receipt_v0",
                "source": source,
                "status": "passed",
                "result_schema_version": EXPECTED_RESULT_SCHEMA_BY_QUALIFICATION[
                    qualification_id
                ],
                "result_digest": DIGEST,
                "completed_at": completed_at,
                "summary": summary(qualification_id),
            }
            for qualification_id in REQUIRED_QUALIFICATION_IDS
        },
    }


def run_cli(manifest_path: Path, repo: Path, *, output_format: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            output_format,
            "canary",
            "release-qualification",
            "--manifest-json",
            str(manifest_path),
            "--repo-root",
            str(repo),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-release-qualification-") as tmp:
        tmp_root = Path(tmp)
        repo = tmp_root / "source"
        (repo / "loopx").mkdir(parents=True)
        git(repo, "init")
        git(repo, "config", "user.email", "loopx@example.invalid")
        git(repo, "config", "user.name", "LoopX Smoke")
        (repo / "source.txt").write_text("qualified source\n", encoding="utf-8")
        (repo / "pyproject.toml").write_text(
            f'[project]\nname = "loopx"\nversion = "{__version__}"\n',
            encoding="utf-8",
        )
        (repo / "loopx" / "__init__.py").write_text(
            f'__version__ = "{__version__}"\n',
            encoding="utf-8",
        )
        git(repo, "add", "source.txt", "pyproject.toml", "loopx/__init__.py")
        git(repo, "commit", "-m", "qualified source")

        source = collect_release_source_identity(repo)
        manifest_path = tmp_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest(source)), encoding="utf-8")

        json_result = run_cli(manifest_path, repo, output_format="json")
        assert json_result.returncode == 0, json_result.stderr
        payload = json.loads(json_result.stdout)
        assert payload["ready_for_release"] is True, payload
        assert payload["decision"] == "ready_for_owner_release", payload
        assert payload["claims"] == {}, payload
        assert payload["read_boundary"]["checks_executed"] is False, payload
        assert payload["read_boundary"]["model_api_invoked"] is False, payload
        assert str(tmp_root) not in json_result.stdout

        markdown_result = run_cli(manifest_path, repo, output_format="markdown")
        assert markdown_result.returncode == 0, markdown_result.stderr
        assert "# Exact Release Commit Qualification" in markdown_result.stdout
        assert "`doubao_actual_default`: `passed`" in markdown_result.stdout
        assert "does not run checks, call a model, move refs, tag, or publish" in (
            markdown_result.stdout
        )

    print("exact-release-commit-qualification-smoke ok")


if __name__ == "__main__":
    main()
