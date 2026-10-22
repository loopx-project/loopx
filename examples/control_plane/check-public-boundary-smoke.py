#!/usr/bin/env python3
"""Smoke-test check behavior in a fresh directory with no project registry."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_DOC_MARKER = "https://" + "la" + "rk" + "office.example/doc"


def run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    home = cwd / "home"
    home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home)
    env.pop("LOOPX_REGISTRY", None)
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        implicit_project = root / "implicit-project"
        implicit_project.mkdir()
        (implicit_project / "README.md").write_text("public docs stay clean\n", encoding="utf-8")
        implicit = run_cli(
            root,
            "--format",
            "json",
            "check",
            "--scan-root",
            str(implicit_project),
        )
        if implicit.returncode != 0:
            raise AssertionError(implicit.stderr or implicit.stdout)
        payload = json.loads(implicit.stdout)
        if not payload.get("ok"):
            raise AssertionError(payload)
        warnings = payload.get("warnings") or []
        if not any("registry file does not exist" in str(item) for item in warnings):
            raise AssertionError(payload)

        explicit = run_cli(
            root,
            "--registry",
            str(root / "missing-registry.json"),
            "--format",
            "json",
            "check",
            "--scan-root",
            str(REPO_ROOT),
        )
        if explicit.returncode == 0:
            raise AssertionError(explicit.stdout)
        explicit_payload = json.loads(explicit.stdout)
        if explicit_payload.get("ok"):
            raise AssertionError(explicit_payload)

        project = root / "project"
        project.mkdir()
        (project / "README.md").write_text("public docs stay clean\n", encoding="utf-8")
        for dirname in [".local", ".loopx", ".goal-harness", ".goal-wrapper.local", "runtime"]:
            local_dir = project / dirname
            local_dir.mkdir(parents=True)
            (local_dir / "private.md").write_text(PRIVATE_DOC_MARKER, encoding="utf-8")

        local_only = run_cli(root, "--format", "json", "check", "--scan-root", str(project))
        if local_only.returncode != 0:
            raise AssertionError(local_only.stderr or local_only.stdout)
        local_only_payload = json.loads(local_only.stdout)
        if not local_only_payload.get("ok"):
            raise AssertionError(local_only_payload)
        if (local_only_payload.get("summary") or {}).get("errors"):
            raise AssertionError(local_only_payload)
        checks = "\n".join(str(item) for item in local_only_payload.get("checks") or [])
        if "public boundary scan clean: 1 files" not in checks:
            raise AssertionError(local_only_payload)

        (project / "public.md").write_text(PRIVATE_DOC_MARKER, encoding="utf-8")
        public_leak = run_cli(root, "--format", "json", "check", "--scan-root", str(project))
        if public_leak.returncode == 0:
            raise AssertionError(public_leak.stdout)
        public_payload = json.loads(public_leak.stdout)
        errors = public_payload.get("errors") or []
        if not any("public.md" in str(item) and "private_doc_url" in str(item) for item in errors):
            raise AssertionError(public_payload)

        scheduler_marker = project / "scheduler.py"
        scheduler_marker.write_text("reset_token=args.reset_token\n", encoding="utf-8")
        scheduler_marker_check = run_cli(
            root,
            "--format",
            "json",
            "check",
            "--scan-path",
            str(scheduler_marker),
        )
        if scheduler_marker_check.returncode != 0:
            raise AssertionError(scheduler_marker_check.stderr or scheduler_marker_check.stdout)
        scheduler_marker_payload = json.loads(scheduler_marker_check.stdout)
        if not scheduler_marker_payload.get("ok"):
            raise AssertionError(scheduler_marker_payload)

        credential_marker = project / "credential.py"
        credential_marker.write_text("tok" + "en=abc123\n", encoding="utf-8")
        credential_check = run_cli(
            root,
            "--format",
            "json",
            "check",
            "--scan-path",
            str(credential_marker),
        )
        if credential_check.returncode == 0:
            raise AssertionError(credential_check.stdout)
        credential_payload = json.loads(credential_check.stdout)
        credential_errors = credential_payload.get("errors") or []
        if not any("credential.py" in str(item) and "credential" in str(item) for item in credential_errors):
            raise AssertionError(credential_payload)

        git_project = root / "git-project"
        git_project.mkdir()
        init = run_git(git_project, "init")
        if init.returncode != 0:
            raise AssertionError(init.stderr or init.stdout)
        state_file = git_project / ".codex" / "goals" / "demo" / "ACTIVE_GOAL_STATE.md"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(f"private_doc_url: {PRIVATE_DOC_MARKER}\n", encoding="utf-8")

        local_state = run_cli(
            root,
            "--format",
            "json",
            "check",
            "--scan-path",
            str(state_file),
        )
        if local_state.returncode == 0:
            raise AssertionError(local_state.stderr or local_state.stdout)
        local_state_payload = json.loads(local_state.stdout)
        if local_state_payload.get("ok"):
            raise AssertionError(local_state_payload)
        if not any("local_private_state" in error
                   for error in local_state_payload.get("errors", [])):
            raise AssertionError(local_state_payload)

        add_state = run_git(git_project, "add", str(state_file.relative_to(git_project)))
        if add_state.returncode != 0:
            raise AssertionError(add_state.stderr or add_state.stdout)
        tracked_state = run_cli(
            root,
            "--format",
            "json",
            "check",
            "--scan-path",
            str(state_file),
        )
        if tracked_state.returncode == 0:
            raise AssertionError(tracked_state.stdout)
        tracked_state_payload = json.loads(tracked_state.stdout)
        tracked_errors = tracked_state_payload.get("errors") or []
        if not any("ACTIVE_GOAL_STATE.md" in str(item) and "local_private_state" in str(item) for item in tracked_errors):
            raise AssertionError(tracked_state_payload)

        tracked_root = run_cli(
            root,
            "--format",
            "json",
            "check",
            "--scan-root",
            str(git_project),
        )
        if tracked_root.returncode == 0:
            raise AssertionError(tracked_root.stdout)
        tracked_root_payload = json.loads(tracked_root.stdout)
        tracked_root_errors = tracked_root_payload.get("errors") or []
        if not any(
            "ACTIVE_GOAL_STATE.md" in str(item) and "private_doc_url" in str(item)
            for item in tracked_root_errors
        ):
            raise AssertionError(tracked_root_payload)

        registry_path = git_project / ".loopx" / "registry.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(
            json.dumps(
                {
                    "public_boundary": {
                        "tracked_private_doc_urls": "allow",
                    },
                    "goals": [],
                }
            ),
            encoding="utf-8",
        )
        allowed_state = run_cli(
            root,
            "--registry",
            str(registry_path),
            "--format",
            "json",
            "check",
            "--scan-path",
            str(state_file),
        )
        if allowed_state.returncode == 0:
            raise AssertionError(allowed_state.stderr or allowed_state.stdout)
        allowed_state_payload = json.loads(allowed_state.stdout)
        if allowed_state_payload.get("ok"):
            raise AssertionError(allowed_state_payload)
        # A document-link policy never makes explicitly selected local state
        # public, including when Git already tracks that state.
        if not any("local_private_state" in str(item)
                   for item in allowed_state_payload.get("errors", [])):
            raise AssertionError(allowed_state_payload)

        allowed_root = run_cli(
            root,
            "--registry",
            str(registry_path),
            "--format",
            "json",
            "check",
            "--scan-root",
            str(git_project),
        )
        if allowed_root.returncode == 0:
            raise AssertionError(allowed_root.stderr or allowed_root.stdout)
        allowed_root_payload = json.loads(allowed_root.stdout)
        if allowed_root_payload.get("ok"):
            raise AssertionError(allowed_root_payload)
        if not any("local_private_state" in str(item)
                   for item in allowed_root_payload.get("errors", [])):
            raise AssertionError(allowed_root_payload)
        checks = "\n".join(str(item) for item in allowed_root_payload.get("checks") or [])
        if "public boundary policy allowed: 1 private_doc_url hits" not in checks:
            raise AssertionError(allowed_root_payload)

    print("check-public-boundary-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
