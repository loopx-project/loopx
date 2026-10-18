#!/usr/bin/env python3
"""Smoke-test the mocked GitHub issue metadata adapter preview."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.capabilities.issue_fix.intake_surface import (  # noqa: E402
    CONTENT_OPS_ISSUE_FIX_METADATA_PREVIEW_PACKET_SCHEMA_VERSION,
    GITHUB_ISSUE_METADATA_PREVIEW_SCHEMA_VERSION,
)
from tests.control_plane.host_process_fixture import write_python_host_fixture  # noqa: E402


PRIVATE_PATTERNS = [
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"/private/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]

FORBIDDEN_VALUES = [
    "raw issue body text that must stay gated",
    "full issue comment text that must stay gated",
    "raw provider response payload",
    "private repro log",
    "secret-value",
    "credential-value",
]


def assert_public_safe(payload: dict[str, Any] | str) -> None:
    text = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    for pattern in PRIVATE_PATTERNS:
        if pattern.search(text):
            raise AssertionError(f"payload matched private pattern {pattern.pattern!r}")
    leaked = [value for value in FORBIDDEN_VALUES if value in text]
    assert not leaked, leaked


def run_cli(
    args: list[str],
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        cwd=REPO_ROOT,
        check=check,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def main() -> int:
    provider_payload = {
        "number": 123,
        "state": "open",
        "title": "Crash on metadata adapter preview",
        "labels": [{"name": "bug"}, {"name": "needs-repro"}],
        "updated_at": "2026-06-23T00:00:00Z",
        "author_association": "CONTRIBUTOR",
        "comments_count": 2,
        "body": "raw issue body text that must stay gated",
        "comments": ["full issue comment text that must stay gated"],
        "raw": "raw provider response payload",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        payload_path = Path(tmpdir) / "provider.json"
        payload_path.write_text(json.dumps(provider_payload), encoding="utf-8")
        result = run_cli(
            [
                "--format",
                "json",
                "content-ops",
                "issue-fix-metadata-preview",
                "--url",
                "https://github.com/huangruiteng/loopx/issues/123",
                "--metadata-json",
                str(payload_path),
            ]
        )

    payload = json.loads(result.stdout)
    assert payload["ok"] is True, payload
    assert (
        payload["schema_version"]
        == CONTENT_OPS_ISSUE_FIX_METADATA_PREVIEW_PACKET_SCHEMA_VERSION
    )
    assert payload["mode"] == "content-ops-issue-fix-metadata-preview", payload
    assert payload["external_reads_performed"] is False, payload
    assert payload["external_writes_performed"] is False, payload
    assert payload["todo_write_performed"] is False, payload
    assert payload["private_source_bodies_read"] is False, payload
    assert payload["local_paths_captured"] is False, payload
    assert payload["automerge_allowed"] is False, payload

    metadata = payload["github_metadata_preview"]
    assert metadata["schema_version"] == GITHUB_ISSUE_METADATA_PREVIEW_SCHEMA_VERSION
    assert metadata["provider_mode"] == "mocked_metadata", metadata
    assert metadata["repo"] == "huangruiteng/loopx", metadata
    assert metadata["issue_ref"] == "issues_123", metadata
    assert metadata["kind"] == "issue", metadata
    assert metadata["number"] == 123, metadata
    assert metadata["state"] == "open", metadata
    assert metadata["labels"] == ["bug", "needs-repro"], metadata
    assert "body" not in metadata, metadata
    assert "comments" not in metadata, metadata
    assert "raw" not in metadata, metadata
    assert metadata["body_captured"] is False, metadata
    assert metadata["comment_bodies_captured"] is False, metadata
    assert metadata["response_payload_captured"] is False, metadata
    assert metadata["private_repo_state_read"] is False, metadata
    assert metadata["gated_provider_fields_present"] == ["body", "comments", "raw"]

    adapter = payload["adapter_preview"]
    assert adapter["provider"] == "mock", adapter
    assert adapter["input_mode"] == "mocked_provider_payload", adapter
    assert adapter["live_read_performed"] is False, adapter
    assert adapter["live_read_allowed_by_default"] is False, adapter
    todo_previews = adapter["candidate_loopx_todo_writeback_preview"]
    assert len(todo_previews) == 2, todo_previews
    assert todo_previews[0]["role"] == "agent", todo_previews
    assert todo_previews[0]["would_write"] is False, todo_previews
    assert todo_previews[1]["role"] == "user", todo_previews
    assert todo_previews[1]["task_class"] == "user_gate", todo_previews

    intake = payload["issue_fix_intake"]
    first_screen = intake["first_screen"]
    assert first_screen["waiting_on"] == "agent", first_screen
    assert first_screen["user_action_required"] is False, first_screen
    assert first_screen["agent_can_continue"] is True, first_screen
    assert intake["boundary"]["todo_write_performed"] is False, intake["boundary"]
    assert intake["boundary"]["issue_body_captured"] is False, intake["boundary"]
    assert intake["boundary"]["comment_bodies_captured"] is False, intake["boundary"]

    validation = payload["validation"]
    assert validation["ok"] is True, validation
    assert validation["gated_provider_field_count"] == 3, validation
    assert validation["todo_writeback_preview_count"] == 2, validation
    assert validation["errors"] == [], validation
    assert_public_safe(payload)

    reference_only = json.loads(
        run_cli(
            [
                "--format",
                "json",
                "content-ops",
                "issue-fix-metadata-preview",
                "--repo",
                "OpenViking/Viking",
                "--issue-ref",
                "issue_456",
            ]
        ).stdout
    )
    assert reference_only["ok"] is True, reference_only
    assert reference_only["adapter_preview"]["input_mode"] == "reference_only"
    assert reference_only["validation"]["gated_provider_field_count"] == 0
    assert_public_safe(reference_only)

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_bin = Path(tmpdir)
        marker = fake_bin / "fixture-called"
        gh = write_python_host_fixture(
            fake_bin / "gh",
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import json",
                    "import sys",
                    "from pathlib import Path",
                    f"Path({str(marker)!r}).write_text('called', encoding='utf-8')",
                    "argv = sys.argv[1:]",
                    "assert argv[:2] == ['api', '-H'], argv",
                    "assert 'repos/huangruiteng/loopx/issues/123' in argv, argv",
                    "assert '--jq' in argv, argv",
                    "json.dump({",
                    "  'number': 123,",
                    "  'state': 'open',",
                    "  'title': 'Crash on metadata adapter preview',",
                    "  'labels': ['bug', 'needs-repro'],",
                    "  'updated_at': '2026-06-23T00:00:00Z',",
                    "  'author_association': 'CONTRIBUTOR',",
                    "  'comments_count': 2,",
                    "  'kind': 'issue',",
                    "}, sys.stdout)",
                    "sys.stdout.write('\\n')",
                ]
            ),
        )
        live_env = {**os.environ, "PATH": os.pathsep.join((str(fake_bin), os.environ.get("PATH", "")))}
        assert Path(shutil.which("gh", path=live_env["PATH"]) or "").resolve() == gh.resolve(), (
            "the smoke must resolve its fixture, never an installed GitHub CLI"
        )
        live_payload = json.loads(
            run_cli(
                [
                    "--format",
                    "json",
                    "content-ops",
                    "issue-fix-metadata-preview",
                    "--url",
                    "https://github.com/huangruiteng/loopx/issues/123",
                    "--fetch-metadata",
                ],
                env=live_env,
            ).stdout
        )
        assert marker.read_text(encoding="utf-8") == "called"
    assert live_payload["ok"] is True, live_payload
    assert live_payload["external_reads_performed"] is True, live_payload
    assert live_payload["private_source_bodies_read"] is False, live_payload
    assert live_payload["github_metadata_preview"]["provider_mode"] == "live_metadata"
    assert live_payload["github_metadata_preview"]["body_captured"] is False
    assert live_payload["github_metadata_preview"]["comment_bodies_captured"] is False
    assert live_payload["github_metadata_preview"]["gated_provider_fields_present"] == []
    assert live_payload["adapter_preview"]["provider"] == "github"
    assert live_payload["adapter_preview"]["input_mode"] == "live_metadata"
    assert live_payload["adapter_preview"]["live_read_performed"] is True
    assert live_payload["adapter_preview"]["live_read_allowed_by_default"] is False
    assert live_payload["issue_fix_intake"]["boundary"]["external_reads_performed"] is True
    assert live_payload["issue_fix_intake"]["boundary"]["issue_body_captured"] is False
    assert live_payload["issue_fix_intake"]["boundary"]["comment_bodies_captured"] is False
    assert_public_safe(live_payload)

    rejected_fetch_mix = run_cli(
        [
            "--format",
            "json",
            "content-ops",
            "issue-fix-metadata-preview",
            "--url",
            "https://github.com/huangruiteng/loopx/issues/123",
            "--metadata-json",
            "-",
            "--fetch-metadata",
        ],
        check=False,
    )
    assert rejected_fetch_mix.returncode == 1, rejected_fetch_mix
    rejected_fetch_mix_payload = json.loads(rejected_fetch_mix.stdout)
    assert rejected_fetch_mix_payload["ok"] is False, rejected_fetch_mix_payload
    assert "cannot be combined" in rejected_fetch_mix_payload["error"]

    markdown = run_cli(["content-ops", "issue-fix-metadata-preview"]).stdout
    assert "LoopX Repo Issue Fix Metadata Preview" in markdown, markdown
    assert "Todo Writeback Preview" in markdown, markdown
    assert "would_write=`False`" in markdown, markdown
    assert_public_safe(markdown)

    rejected = run_cli(
        [
            "--format",
            "json",
            "content-ops",
            "issue-fix-metadata-preview",
            "--url",
            "https://github.com/huangruiteng/loopx/issues/123?debug=1",
        ],
        check=False,
    )
    assert rejected.returncode == 1, rejected
    rejected_payload = json.loads(rejected.stdout)
    assert rejected_payload["ok"] is False, rejected_payload
    assert "must not include query or fragment" in rejected_payload["error"]

    print("content-ops-issue-fix-metadata-preview-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
