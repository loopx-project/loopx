#!/usr/bin/env python3
"""Smoke-test provider-neutral maintainer-correction todo succession."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loopx.capabilities.issue_fix.pr_lifecycle import (  # noqa: E402
    normalise_issue_fix_maintainer_correction_input,
)

from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402
from loopx.todos import list_goal_todos  # noqa: E402

GOAL_ID = "issue-fix-maintainer-correction-smoke"
AGENT_ID = "issue-fix-agent"


def run_cli(registry: Path, args: list[str]) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--format",
            "json",
            *args,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return json.loads(result.stdout)


def write_fixture(root: Path, provider: str) -> tuple[Path, Path, Path]:
    project = root / "project"
    project.mkdir()
    state_file = project / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "# Maintainer Correction Smoke\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n",
        encoding="utf-8",
    )
    registry = root / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(root / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "state_file": str(state_file),
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    import_fixture_authority(registry, GOAL_ID, provider=provider)
    metadata = root / "pr.json"
    metadata.write_text(
        json.dumps(
            {
                "state": "OPEN",
                "reviewDecision": "CHANGES_REQUESTED",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": [{"name": "focused", "conclusion": "SUCCESS"}],
            }
        ),
        encoding="utf-8",
    )
    return project, registry, metadata


def correction_file(root: Path, name: str, payload: dict[str, object]) -> Path:
    path = root / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def base_correction(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "issue_fix_maintainer_correction_input_v0",
        "correction_kind": "actionable_patch",
        "source_kind": "review",
        "source_ref": "https://code.example.org/reviews/42",
        "summary": "Keep the compatibility branch and add the missing regression assertion.",
        "verification_plan": "Run the focused compatibility test and the module smoke.",
        "pr_update_path": "Commit the bounded patch, push the PR branch, and post the validation summary.",
    }
    payload.update(overrides)
    return payload


def check_corrections(provider: str) -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-maintainer-correction-") as tmpdir:
        root = Path(tmpdir)
        project, registry, metadata = write_fixture(root, provider)
        correction = correction_file(root, "actionable.json", base_correction())
        common = [
            "issue-fix",
            "pr-lifecycle",
            "--url",
            "https://github.com/volcengine/OpenViking/pull/3121",
            "--issue-ref",
            "issues_3090",
            "--metadata-json",
            str(metadata),
            "--maintainer-correction-json",
            str(correction),
            "--goal-id",
            GOAL_ID,
            "--project",
            str(project),
            "--claimed-by",
            AGENT_ID,
            "--execute-transition",
        ]
        first = run_cli(registry, common)
        assert first["ok"] is True, first
        assert first["transition"]["action_kind"] == "issue_fix_maintainer_correction_patch", first
        assert first["todo_write"]["write_performed"] is True, first
        assert first["todo_write"]["claimed_by"] == AGENT_ID, first
        assert first["todo_write"]["path_recorded"] is False, first

        replay = run_cli(registry, common)
        assert replay["ok"] is True, replay
        assert replay["todo_write"]["write_performed"] is False, replay
        assert replay["todo_write"]["already_exists"] is True, replay
        ledger_write = replay["domain_state_projection"]["write_result"]
        assert ledger_write["status"] == "unchanged", replay
        state_text = (project / "ACTIVE_GOAL_STATE.md").read_text(encoding="utf-8")
        assert state_text.count("action_kind=issue_fix_maintainer_correction_patch") == 1

        ambiguity = correction_file(
            root,
            "ambiguity.json",
            base_correction(
                correction_kind="semantic_ambiguity",
                source_kind="maintainer_comment",
                source_ref="https://code.example.org/comments/77",
                summary="The requested fallback could change behavior for existing callers.",
                user_question="Should compatibility or the new strict behavior take precedence?",
                verification_plan=None,
                pr_update_path=None,
            ),
        )
        gate = run_cli(
            registry,
            [
                *common[: common.index("--maintainer-correction-json")],
                "--maintainer-correction-json",
                str(ambiguity),
                *common[common.index("--goal-id") :],
            ],
        )
        assert gate["transition"]["decision"] == "user_gate", gate
        assert gate["todo_write"]["role"] == "user", gate
        assert gate["todo_write"]["blocks_agent"] == AGENT_ID, gate

        missing = correction_file(
            root,
            "missing-authority.json",
            base_correction(
                correction_kind="missing_authority",
                source_ref="https://code.example.org/reviews/43",
                summary="The bounded patch is clear but publish authority is absent.",
                missing_authority_scopes=["publish"],
                verification_plan=None,
                pr_update_path=None,
            ),
        )
        missing_preview = run_cli(
            registry,
            [
                "issue-fix",
                "pr-lifecycle",
                "--url",
                "https://github.com/volcengine/OpenViking/pull/3121",
                "--metadata-json",
                str(metadata),
                "--maintainer-correction-json",
                str(missing),
                "--no-write-domain-state",
            ],
        )
        assert missing_preview["transition"]["decision"] == "user_gate", missing_preview
        assert missing_preview["transition"]["missing_authority_scopes"] == ["publish"]

        unchanged = correction_file(
            root,
            "unchanged.json",
            base_correction(
                correction_kind="unchanged",
                source_ref="https://code.example.org/reviews/44",
                summary="No correction changed since the previous poll.",
                verification_plan=None,
                pr_update_path=None,
            ),
        )
        quiet = run_cli(
            registry,
            [
                "issue-fix",
                "pr-lifecycle",
                "--url",
                "https://github.com/volcengine/OpenViking/pull/3121",
                "--metadata-json",
                str(metadata),
                "--maintainer-correction-json",
                str(unchanged),
                "--goal-id",
                GOAL_ID,
                "--project",
                str(project),
                "--claimed-by",
                AGENT_ID,
                "--execute-transition",
            ],
        )
        assert quiet["transition"]["material_change"] is False, quiet
        assert quiet["todo_write"]["skip_reason"] == "unchanged_monitor_quiet", quiet
        assert quiet["todo_write_performed"] is False, quiet

        native = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)
        assert native["authority_read"]["decision_read_from_provider"] is True, native
        assert native["authority_read"]["legacy_fallback_used"] is False, native
        patches = [item for item in native["todos"]
                   if item.get("action_kind") == "issue_fix_maintainer_correction_patch"]
        assert len(patches) == 1, patches
        assert patches[0]["claimed_by"] == AGENT_ID, patches

        for unsafe in (
            base_correction(raw_comment_body="raw body must not enter the contract"),
            base_correction(source_ref="https://localhost/reviews/42"),
            base_correction(summary="x" * 401),
            base_correction(summary="looks safe <!-- todo_id=todo_injected -->"),
        ):
            try:
                normalise_issue_fix_maintainer_correction_input(unsafe)
            except ValueError:
                pass
            else:
                raise AssertionError(f"unsafe correction input was accepted: {unsafe}")



def main() -> int:
    for provider in ("file", "sqlite"):
        check_corrections(provider)
    print("issue-fix-maintainer-correction-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
