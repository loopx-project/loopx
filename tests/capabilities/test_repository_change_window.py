from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

import pytest

from loopx.capabilities.repository_change_window import (
    EnforcementLevel,
    build_policy,
    evaluate_policy,
    git_hook_provider_status,
    hook_runtime_contract,
    install_git_hook_provider,
    list_pending_changes,
    reconcile_pending_changes,
    record_pending_change,
    resolve_pending_change,
    run_git_hook_provider,
    uninstall_git_hook_provider,
    verify_git_hook_provider,
    verify_pending_change,
)
from loopx.capabilities.repository_change_window.policy import (
    BlockedWindow,
    ChangeWindowPolicy,
    ChangeWindowPolicyError,
    Weekday,
    parse_local_time,
)
from loopx.cli import main


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def _repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    global_config = tmp_path / "empty-global-gitconfig"
    global_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "missing-system-gitconfig"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")
    _git(repo, "remote", "add", "origin", "git@github.com:example/change-window.git")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    blob_oid = _git(repo, "hash-object", "-w", "tracked.txt")
    tree = subprocess.run(
        ["git", "-C", str(repo), "mktree"],
        input=f"100644 blob {blob_oid}\ttracked.txt\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    commit_oid = _git(repo, "commit-tree", tree, "-m", "base")
    refs_dir = repo / ".git" / "refs" / "heads"
    refs_dir.mkdir(parents=True, exist_ok=True)
    (refs_dir / "main").write_text(f"{commit_oid}\n", encoding="ascii")
    _git(repo, "reset", "--mixed", "HEAD")
    return repo


def _weekday_policy():
    return build_policy(
        timezone_name="Asia/Shanghai",
        weekdays=["mon", "tue", "wed", "thu", "fri"],
        blocked_start="10:00",
        blocked_end="21:00",
    )


def _write_legacy_global_guard(root: Path) -> None:
    root.mkdir()
    (root / "README.md").write_text(
        "# LoopX Local Commit Time Gate\n",
        encoding="utf-8",
    )
    (root / "gate-common.sh").write_text(
        "loopx_commit_gate_enforce() { return 0; }\n",
        encoding="utf-8",
    )
    for name in ("pre-commit", "pre-push"):
        hook = root / name
        hook.write_text(
            "#!/bin/sh\n. \"$(dirname \"$0\")/gate-common.sh\"\nexit 0\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)


def _rewrite_provider_as_legacy(
    repo: Path,
    *,
    schema_version: str,
    enforcement_level: str | None,
    managed_hook_names: list[str],
    preserved_ssh_command: Path,
) -> None:
    common_dir = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common_dir.is_absolute():
        common_dir = (repo / common_dir).resolve()
    provider_dir = common_dir / "loopx" / "repository-change-window"
    state_path = provider_dir / "provider.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["schema_version"] = schema_version
    if enforcement_level is None:
        state.pop("enforcement_level", None)
    else:
        state["enforcement_level"] = enforcement_level
    for field in (
        "previous_ssh_local_override",
        "previous_ssh_local_value",
        "previous_ssh_command",
        "ssh_command_digest",
    ):
        state.pop(field, None)
    state["hook_digests"] = {
        name: state["hook_digests"][name] for name in managed_hook_names
    }
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    (provider_dir / "ssh-command").unlink(missing_ok=True)
    _git(repo, "config", "--local", "core.sshCommand", str(preserved_ssh_command))


def test_policy_uses_typed_timezone_boundaries_and_internal_fake_clock() -> None:
    policy = _weekday_policy()

    before = evaluate_policy(
        policy,
        now=datetime.fromisoformat("2026-08-18T09:59:00+08:00"),
    )
    start = evaluate_policy(
        policy,
        now=datetime.fromisoformat("2026-08-18T10:00:00+08:00"),
    )
    end = evaluate_policy(
        policy,
        now=datetime.fromisoformat("2026-08-18T21:00:00+08:00"),
    )
    weekend = evaluate_policy(
        policy,
        now=datetime.fromisoformat("2026-08-22T12:00:00+08:00"),
    )

    assert before["allowed"] is True
    assert start["allowed"] is False
    assert start["next_eligible_at"] == "2026-08-18T21:00:00+08:00"
    assert end["allowed"] is True
    assert weekend["allowed"] is True

    overnight = build_policy(
        timezone_name="UTC",
        weekdays=["fri"],
        blocked_start="22:00",
        blocked_end="02:00",
    )
    assert (
        evaluate_policy(
            overnight,
            now=datetime.fromisoformat("2026-08-22T01:30:00+00:00"),
        )["allowed"]
        is False
    )
    assert (
        evaluate_policy(
            overnight,
            now=datetime.fromisoformat("2026-08-22T02:00:00+00:00"),
        )["allowed"]
        is True
    )

    with pytest.raises(ChangeWindowPolicyError, match="timezone-aware"):
        evaluate_policy(policy, now=datetime(2026, 8, 18, 12, 0))

    full_week = tuple(Weekday(day) for day in range(7))
    with pytest.raises(ChangeWindowPolicyError, match="eligible local minute"):
        ChangeWindowPolicy(
            timezone_name="UTC",
            blocked_windows=(
                BlockedWindow(
                    weekdays=full_week,
                    start_local=parse_local_time("00:00"),
                    end_local=parse_local_time("12:00"),
                ),
                BlockedWindow(
                    weekdays=full_week,
                    start_local=parse_local_time("12:00"),
                    end_local=parse_local_time("00:00"),
                ),
            ),
        )


def test_install_is_default_off_preserves_prior_hook_and_is_linked_worktree_shared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    runtime_root = tmp_path / "runtime"
    prior_hooks = tmp_path / "prior-hooks"
    prior_hooks.mkdir()
    prior_hook = prior_hooks / "pre-commit"
    prior_hook.write_text(
        "#!/bin/sh\nprintf prior-hook-called > prior-hook.marker\n",
        encoding="utf-8",
    )
    prior_hook.chmod(0o755)
    _git(repo, "config", "--local", "core.hooksPath", str(prior_hooks))

    preview = install_git_hook_provider(repo_path=repo, policy=_weekday_policy())
    assert preview["dry_run"] is True
    assert _git(repo, "config", "--local", "--get", "core.hooksPath") == str(
        prior_hooks
    )
    assert (
        git_hook_provider_status(repo_path=repo)["status"]
        == "effective_external_guard_detected"
    )
    assert preview["installation_mode"] == "layered_external_guard"

    installed = install_git_hook_provider(
        repo_path=repo,
        policy=_weekday_policy(),
        execute=True,
    )
    assert installed["enabled"] is True
    assert installed["enforcement_level"] == "hook_only"
    assert installed["managed_hook_names"] == ["pre-commit", "pre-push"]
    assert installed["contains_personal_path"] is False
    assert installed["installation_mode"] == "layered_external_guard"
    assert str(tmp_path) not in json.dumps(installed, sort_keys=True)

    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-b", "feature/linked", str(linked))
    linked_status = git_hook_provider_status(
        repo_path=linked,
        now=datetime.fromisoformat("2026-08-22T12:00:00+08:00"),
    )
    assert linked_status["ok"] is True
    assert linked_status["repository_id"] == installed["repository_id"]
    assert linked_status["contains_personal_path"] is False

    allowed = run_git_hook_provider(
        repo_path=linked,
        runtime_root=runtime_root,
        event="pre-commit",
        now=datetime.fromisoformat("2026-08-22T12:00:00+08:00"),
    )
    assert allowed["status"] == "allowed"
    assert allowed["previous_hook_invoked"] is True
    assert (linked / "prior-hook.marker").read_text(
        encoding="utf-8"
    ) == "prior-hook-called"

    blocked = run_git_hook_provider(
        repo_path=linked,
        runtime_root=runtime_root,
        event="pre-commit",
        now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
    )
    assert blocked["status"] == "blocked_by_policy"
    assert blocked["pending_change"]["changed"] is True
    assert list_pending_changes(runtime_root=runtime_root)["count"] == 1

    uninstall_preview = uninstall_git_hook_provider(repo_path=linked)
    assert uninstall_preview["dry_run"] is True
    assert git_hook_provider_status(repo_path=repo)["installed"] is True
    uninstalled = uninstall_git_hook_provider(repo_path=linked, execute=True)
    assert uninstalled["status"] == "uninstalled"
    assert _git(repo, "config", "--local", "--get", "core.hooksPath") == str(
        prior_hooks
    )
    assert prior_hook.is_file()


def test_status_reports_external_and_recognized_legacy_global_guards_path_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    unknown_hooks = tmp_path / "unknown-hooks"
    unknown_hooks.mkdir()
    (unknown_hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    _git(repo, "config", "--global", "core.hooksPath", str(unknown_hooks))
    _git(
        repo,
        "config",
        "--global",
        "core.sshCommand",
        f"ssh -F {tmp_path / 'private-ssh-config'}",
    )

    unknown = git_hook_provider_status(repo_path=repo)
    assert unknown["status"] == "effective_external_guard_detected"
    diagnostic = unknown["external_guard"]
    assert diagnostic == {
        "schema_version": "repository_change_window_external_guard_diagnostic_v0",
        "detected": True,
        "status": "detected",
        "effective_surfaces": ["hooks_path", "ssh_command"],
        "configuration_scopes": {
            "hooks_path": "global",
            "ssh_command": "global",
        },
        "diagnostic_only": True,
        "trusted_provider": False,
        "policy_known": False,
        "admission_known": False,
        "recognized_legacy_kind": None,
        "path_free": True,
        "remote_write_authority_granted": False,
    }
    assert str(tmp_path) not in json.dumps(unknown, sort_keys=True)
    verification = verify_git_hook_provider(repo_path=repo)
    assert verification["ok"] is False
    assert verification["verified"] is False

    legacy_hooks = tmp_path / "legacy-hooks"
    _write_legacy_global_guard(legacy_hooks)
    _git(repo, "config", "--global", "core.hooksPath", str(legacy_hooks))
    recognized = git_hook_provider_status(repo_path=repo)
    recognized_guard = recognized["external_guard"]
    assert recognized_guard["recognized_legacy_kind"] == (
        "loopx_global_commit_time_gate_v0"
    )
    assert recognized_guard["policy_known"] is False
    assert recognized_guard["migration_preview"] == {
        "schema_version": "repository_change_window_legacy_migration_preview_v0",
        "action": "install_repository_provider",
        "integration_mode": "layered_legacy_global_guard",
        "preview_required": True,
        "execute_required": True,
        "automatic_migration": False,
        "preserves_effective_guard": True,
        "mutates_global_configuration": False,
        "grants_remote_write_authority": False,
    }
    preview = install_git_hook_provider(repo_path=repo, policy=_weekday_policy())
    assert preview["dry_run"] is True
    assert preview["installation_mode"] == "layered_legacy_global_guard"
    assert preview["previous_guard_diagnostic"] == recognized_guard
    assert str(tmp_path) not in json.dumps(preview, sort_keys=True)


def test_provider_is_shared_by_linked_worktrees_but_not_separate_clones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    installed = install_git_hook_provider(
        repo_path=repo,
        policy=_weekday_policy(),
        execute=True,
    )
    linked = tmp_path / "linked-provider"
    _git(repo, "worktree", "add", "-b", "feature/provider", str(linked))
    linked_status = git_hook_provider_status(repo_path=linked)
    assert linked_status["status"] == "ready"
    assert linked_status["repository_id"] == installed["repository_id"]

    clone = tmp_path / "separate-clone"
    _git(repo, "clone", "--quiet", "--no-hardlinks", str(repo), str(clone))
    clone_status = git_hook_provider_status(repo_path=clone)
    assert clone_status["status"] == "provider_not_installed"
    assert clone_status["installed"] is False


def test_reference_guard_uses_fake_clock_and_shared_linked_worktree_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    linked = tmp_path / "linked-guard"
    _git(repo, "worktree", "add", "-b", "feature/guard", str(linked))
    runtime_root = tmp_path / "runtime"
    prior_ssh = tmp_path / "prior-ssh"
    prior_ssh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    prior_ssh.chmod(0o755)
    _git(repo, "config", "--local", "core.sshCommand", str(prior_ssh))

    installed = install_git_hook_provider(
        repo_path=repo,
        policy=_weekday_policy(),
        enforcement_level=EnforcementLevel.REFERENCE_GUARD,
        execute=True,
    )
    assert installed["managed_hook_names"] == [
        "pre-commit",
        "pre-push",
        "reference-transaction",
    ]
    assert installed["guards_ssh_transport"] is True
    managed_ssh = Path(_git(repo, "config", "--local", "--get", "core.sshCommand"))
    assert managed_ssh.name == "ssh-command"
    assert managed_ssh.is_file()
    assert str(tmp_path) not in json.dumps(installed, sort_keys=True)
    linked_status = git_hook_provider_status(
        repo_path=linked,
        now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
    )
    assert linked_status["enforcement_level"] == "reference_guard"
    assert linked_status["decision"]["allowed"] is False

    old_oid = _git(linked, "rev-parse", "HEAD")
    tree_oid = _git(linked, "rev-parse", "HEAD^{tree}")
    new_oid = _git(
        linked,
        "commit-tree",
        tree_oid,
        "-p",
        old_oid,
        "-m",
        "unreferenced candidate",
    )
    blocked = run_git_hook_provider(
        repo_path=linked,
        runtime_root=runtime_root,
        event="reference-transaction",
        hook_args=("prepared",),
        hook_stdin=f"{old_oid} {new_oid} refs/heads/feature/guard\n".encode(),
        now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
    )
    assert blocked["status"] == "blocked_by_policy"
    assert blocked["pending_change"]["changed"] is True

    preparing = run_git_hook_provider(
        repo_path=linked,
        runtime_root=runtime_root,
        event="reference-transaction",
        hook_args=("preparing",),
        hook_stdin=f"{old_oid} {new_oid} refs/heads/feature/guard\n".encode(),
        now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
    )
    assert preparing["status"] == "blocked_by_policy"
    assert preparing["guarded_change"] is True

    for staged_ref in (
        "refs/tags/staged-candidate",
        "refs/notes/staged-candidate",
        "refs/loopx/staged-candidate",
    ):
        _git(linked, "update-ref", staged_ref, new_oid)
        staged = run_git_hook_provider(
            repo_path=linked,
            runtime_root=runtime_root,
            event="reference-transaction",
            hook_args=("prepared",),
            hook_stdin=f"{old_oid} {new_oid} refs/heads/feature/guard\n".encode(),
            now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
        )
        assert staged["status"] == "blocked_by_policy"
        assert staged["guarded_change"] is True

    blocked_ssh = run_git_hook_provider(
        repo_path=linked,
        runtime_root=runtime_root,
        event="ssh-transport",
        hook_args=("git@github.com", "git-receive-pack 'example/repo.git'"),
        now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
    )
    assert blocked_ssh["status"] == "blocked_by_policy"

    allowed_ssh = run_git_hook_provider(
        repo_path=linked,
        runtime_root=runtime_root,
        event="ssh-transport",
        hook_args=("git@github.com", "git-upload-pack 'example/repo.git'"),
        now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
    )
    assert allowed_ssh["status"] == "allowed"
    assert allowed_ssh["guarded_change"] is False
    assert allowed_ssh["previous_hook_invoked"] is False
    assert allowed_ssh["policy_evaluated"] is False
    assert allowed_ssh["reason"] == "read_only_ssh_transport"
    with pytest.raises(ValueError, match="unsupported Git SSH service"):
        run_git_hook_provider(
            repo_path=linked,
            runtime_root=runtime_root,
            event="ssh-transport",
            hook_args=("git@github.com", "custom-service 'example/repo.git'"),
            now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
        )

    existing_ref_move = run_git_hook_provider(
        repo_path=linked,
        runtime_root=runtime_root,
        event="reference-transaction",
        hook_args=("prepared",),
        hook_stdin=f"{old_oid} {old_oid} HEAD\n".encode(),
        now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
    )
    assert existing_ref_move["status"] == "allowed"
    assert existing_ref_move["guarded_change"] is False
    with pytest.raises(ValueError, match="requires exactly one"):
        run_git_hook_provider(
            repo_path=linked,
            runtime_root=runtime_root,
            event="reference-transaction",
            hook_args=(),
            hook_stdin=f"{old_oid} {new_oid} refs/heads/feature/guard\n".encode(),
            now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
        )

    replaced = install_git_hook_provider(
        repo_path=linked,
        policy=_weekday_policy(),
        enforcement_level=EnforcementLevel.HOOK_ONLY,
        replace=True,
        execute=True,
    )
    assert replaced["enforcement_level"] == "hook_only"
    assert _git(linked, "config", "--local", "--get", "core.sshCommand") == str(
        prior_ssh
    )
    assert managed_ssh.exists() is False
    reference_hook = Path(
        _git(linked, "rev-parse", "--git-path", "hooks/reference-transaction")
    )
    assert reference_hook.exists() is False
    reference_hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    drifted_status = git_hook_provider_status(repo_path=linked)
    assert drifted_status["status"] == "drifted"
    assert any(
        item["check"] == "hook:reference-transaction:absent"
        and item["status"] == "unexpected_managed_hook"
        for item in drifted_status["checks"]
    )


def test_read_only_ssh_transport_survives_provider_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    runtime_root = tmp_path / "runtime"
    install_git_hook_provider(
        repo_path=repo,
        policy=_weekday_policy(),
        enforcement_level=EnforcementLevel.REFERENCE_GUARD,
        execute=True,
    )
    _git(repo, "config", "--local", "--unset", "core.hooksPath")
    assert git_hook_provider_status(repo_path=repo)["status"] == "drifted"

    for service in ("git-upload-pack", "git-upload-archive"):
        allowed = run_git_hook_provider(
            repo_path=repo,
            runtime_root=runtime_root,
            event="ssh-transport",
            hook_args=("git@github.com", f"{service} 'example/repo.git'"),
            now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
        )
        assert allowed == {
            "ok": True,
            "schema_version": "repository_change_window_hook_result_v1",
            "status": "allowed",
            "event": "ssh-transport",
            "exit_code": 0,
            "guarded_change": False,
            "previous_hook_invoked": False,
            "policy_evaluated": False,
            "reason": "read_only_ssh_transport",
        }

    guarded = run_git_hook_provider(
        repo_path=repo,
        runtime_root=runtime_root,
        event="ssh-transport",
        hook_args=("git@github.com", "git-receive-pack 'example/repo.git'"),
        now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
    )
    assert guarded["status"] == "provider_drift"


def test_provider_detects_incompatible_hook_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    install_git_hook_provider(
        repo_path=repo,
        policy=_weekday_policy(),
        enforcement_level=EnforcementLevel.REFERENCE_GUARD,
        execute=True,
    )

    stale_bin = tmp_path / "stale-bin"
    stale_bin.mkdir()
    stale_loopx = stale_bin / "loopx"
    stale_loopx.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '{\"ok\":true,\"schema_version\":"
        "\"repository_change_window_hook_runtime_v1\","
        "\"supported_events\":[\"pre-commit\",\"pre-push\"]}'\n",
        encoding="utf-8",
    )
    stale_loopx.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stale_bin}:{os.environ.get('PATH', '')}")

    status = git_hook_provider_status(repo_path=repo)
    assert status["ok"] is False
    assert status["status"] == "drifted"
    assert any(
        item["check"] == "hook_runtime_contract"
        and item["status"] == "incompatible"
        for item in status["checks"]
    )


@pytest.mark.parametrize(
    ("legacy_schema", "legacy_enforcement", "managed_hook_names"),
    [
        ("repository_change_window_git_hook_state_v0", None, ["pre-commit", "pre-push"]),
        (
            "repository_change_window_git_hook_state_v1",
            "reference_guard",
            ["pre-commit", "pre-push", "reference-transaction"],
        ),
    ],
)
def test_pristine_legacy_provider_can_be_inspected_and_uninstalled_without_ssh_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy_schema: str,
    legacy_enforcement: str | None,
    managed_hook_names: list[str],
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    prior_ssh = tmp_path / "prior-ssh"
    prior_ssh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    prior_ssh.chmod(0o755)
    _git(repo, "config", "--local", "core.sshCommand", str(prior_ssh))
    install_git_hook_provider(
        repo_path=repo,
        policy=_weekday_policy(),
        enforcement_level=(
            EnforcementLevel.REFERENCE_GUARD
            if legacy_enforcement == "reference_guard"
            else EnforcementLevel.HOOK_ONLY
        ),
        execute=True,
    )
    _rewrite_provider_as_legacy(
        repo,
        schema_version=legacy_schema,
        enforcement_level=legacy_enforcement,
        managed_hook_names=managed_hook_names,
        preserved_ssh_command=prior_ssh,
    )

    status = git_hook_provider_status(repo_path=repo)
    assert status["ok"] is False
    assert status["status"] == "drifted"
    assert any(
        item["check"] == "provider_schema"
        and item["status"] == "migration_required"
        for item in status["checks"]
    )
    assert all(
        item["ok"]
        for item in status["checks"]
        if item["check"] not in {"provider_schema", "hook_runtime_contract"}
    )

    preview = uninstall_git_hook_provider(repo_path=repo)
    assert preview["status"] == "preview"
    assert preview["installed_state_schema_version"] == legacy_schema
    assert preview["restores_previous_ssh_route"] is False
    removed = uninstall_git_hook_provider(repo_path=repo, execute=True)
    assert removed["status"] == "uninstalled"
    assert _git(repo, "config", "--local", "--get", "core.sshCommand") == str(
        prior_ssh
    )


def test_reference_guard_v1_migration_preserves_original_ssh_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    prior_ssh = tmp_path / "prior-ssh"
    prior_ssh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    prior_ssh.chmod(0o755)
    _git(repo, "config", "--local", "core.sshCommand", str(prior_ssh))
    install_git_hook_provider(
        repo_path=repo,
        policy=_weekday_policy(),
        enforcement_level=EnforcementLevel.REFERENCE_GUARD,
        execute=True,
    )
    _rewrite_provider_as_legacy(
        repo,
        schema_version="repository_change_window_git_hook_state_v1",
        enforcement_level="reference_guard",
        managed_hook_names=["pre-commit", "pre-push", "reference-transaction"],
        preserved_ssh_command=prior_ssh,
    )

    replaced = install_git_hook_provider(
        repo_path=repo,
        policy=_weekday_policy(),
        enforcement_level=EnforcementLevel.REFERENCE_GUARD,
        replace=True,
        execute=True,
    )
    assert replaced["status"] == "installed"
    assert replaced["installation_mode"] == (
        "migrated_legacy_repository_provider"
    )
    assert git_hook_provider_status(repo_path=repo)["status"] == "ready"
    managed_ssh = _git(repo, "config", "--local", "--get", "core.sshCommand")
    assert managed_ssh != str(prior_ssh)

    uninstall_git_hook_provider(repo_path=repo, execute=True)
    assert _git(repo, "config", "--local", "--get", "core.sshCommand") == str(
        prior_ssh
    )


def test_hook_runtime_contract_is_typed_and_complete() -> None:
    assert hook_runtime_contract() == {
        "ok": True,
        "schema_version": "repository_change_window_hook_runtime_v1",
        "supported_events": [
            "pre-commit",
            "pre-push",
            "reference-transaction",
            "ssh-transport",
        ],
    }


def test_pending_change_ledger_is_restart_safe_idempotent_and_path_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    runtime_root = tmp_path / "runtime"
    decision = evaluate_policy(
        _weekday_policy(),
        now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
    )
    with pytest.raises(ValueError, match="absolute paths"):
        record_pending_change(
            runtime_root=runtime_root,
            repo_path=repo,
            decision=decision,
            source="manual_cli",
            validation_refs=["artifact:/home/example/private.log"],
        )
    with pytest.raises(ValueError, match="credentials"):
        record_pending_change(
            runtime_root=runtime_root,
            repo_path=repo,
            decision=decision,
            source="manual_cli",
            validation_refs=["Bear" + "er example-credential"],
        )
    first = record_pending_change(
        runtime_root=runtime_root,
        repo_path=repo,
        decision=decision,
        source="manual_cli",
        goal_id="example-goal",
        todo_id="todo_example123",
        write_scopes=["loopx/**", "tests/**"],
        validation_refs=["pytest:focused:passed"],
        execute=True,
        now=datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc),
    )
    repeated = record_pending_change(
        runtime_root=runtime_root,
        repo_path=repo,
        decision=decision,
        source="manual_cli",
        goal_id="example-goal",
        todo_id="todo_example123",
        write_scopes=["loopx/**", "tests/**"],
        validation_refs=["pytest:focused:passed"],
        execute=True,
        now=datetime(2026, 8, 18, 4, 1, tzinfo=timezone.utc),
    )
    assert first["changed"] is True
    assert repeated["duplicate"] is True

    automatic_refresh = record_pending_change(
        runtime_root=runtime_root,
        repo_path=repo,
        decision={**decision, "observed_at": "2026-08-18T12:01:00+08:00"},
        source="git_hook:pre-commit",
        execute=True,
    )
    assert automatic_refresh["record"]["change_id"] == first["record"]["change_id"]
    assert automatic_refresh["record"]["goal_id"] == "example-goal"
    assert automatic_refresh["record"]["todo_id"] == "todo_example123"
    assert automatic_refresh["record"]["write_scopes"] == ["loopx/**", "tests/**"]
    assert automatic_refresh["duplicate"] is True

    restarted_read = list_pending_changes(runtime_root=runtime_root)
    assert restarted_read["count"] == 1
    serialized_projection = json.dumps(restarted_read, sort_keys=True)
    assert str(tmp_path) not in serialized_projection
    assert '"contains_code": false' in serialized_projection
    change_id = first["record"]["change_id"]
    assert (
        verify_pending_change(
            runtime_root=runtime_root,
            change_id=change_id,
        )["status"]
        == "verified"
    )

    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    drifted = verify_pending_change(runtime_root=runtime_root, change_id=change_id)
    assert drifted["status"] == "drifted"
    assert any(
        item["check"] == "worktree_fingerprint" and item["status"] == "changed"
        for item in drifted["checks"]
    )
    assert any(
        item["check"] == "private_changed_path_inventory"
        and item["status"] == "changed"
        for item in drifted["checks"]
    )
    refreshed = record_pending_change(
        runtime_root=runtime_root,
        repo_path=repo,
        decision=decision,
        source="manual_cli",
        goal_id="example-goal",
        todo_id="todo_example123",
        write_scopes=["loopx/**", "tests/**"],
        validation_refs=["pytest:focused:passed"],
        execute=True,
    )
    assert refreshed["transition"] == "refreshed"
    assert refreshed["changed"] is True

    untracked = repo / "new.txt"
    untracked.write_text("first\n", encoding="utf-8")
    untracked_record = record_pending_change(
        runtime_root=runtime_root,
        repo_path=repo,
        decision=decision,
        source="manual_cli",
        goal_id="example-goal",
        todo_id="todo_example123",
        execute=True,
    )
    untracked.write_text("second\n", encoding="utf-8")
    assert (
        verify_pending_change(
            runtime_root=runtime_root,
            change_id=untracked_record["record"]["change_id"],
        )["status"]
        == "drifted"
    )
    untracked.unlink()
    record_pending_change(
        runtime_root=runtime_root,
        repo_path=repo,
        decision=decision,
        source="manual_cli",
        goal_id="example-goal",
        todo_id="todo_example123",
        execute=True,
    )

    resolved = resolve_pending_change(
        runtime_root=runtime_root,
        change_id=change_id,
        resolution="merged",
        evidence="github:example/change-window#42",
        execute=True,
    )
    duplicate_resolution = resolve_pending_change(
        runtime_root=runtime_root,
        change_id=change_id,
        resolution="merged",
        evidence="github:example/change-window#42",
        execute=True,
    )
    assert resolved["changed"] is True
    assert duplicate_resolution["duplicate"] is True
    assert list_pending_changes(runtime_root=runtime_root, state="open")["count"] == 0
    assert (
        list_pending_changes(runtime_root=runtime_root, state="resolved")["count"] == 1
    )
    assert (
        verify_pending_change(runtime_root=runtime_root, change_id=change_id)["status"]
        == "unlocatable"
    )


def test_pending_change_ledger_tracks_detached_worktree_without_public_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    detached = tmp_path / "detached-pending"
    _git(repo, "worktree", "add", "--detach", str(detached), "HEAD")
    runtime_root = tmp_path / "runtime"

    recorded = record_pending_change(
        runtime_root=runtime_root,
        repo_path=detached,
        decision={"allowed": False, "reason": "test_gate"},
        source="manual_cli",
        goal_id="example-goal",
        todo_id="todo_detached123",
        execute=True,
    )
    record = recorded["record"]
    assert record["checkout_kind"] == "detached"
    assert str(record["checkout_id"]).startswith("worktree_")
    assert record["branch"] is None
    assert str(tmp_path) not in json.dumps(record, sort_keys=True)
    assert (
        verify_pending_change(
            runtime_root=runtime_root,
            change_id=record["change_id"],
        )["status"]
        == "verified"
    )

    (detached / "tracked.txt").write_text("detached draft\n", encoding="utf-8")
    drifted = verify_pending_change(
        runtime_root=runtime_root,
        change_id=record["change_id"],
    )
    assert drifted["status"] == "drifted"
    assert any(
        item["check"] == "worktree_fingerprint" and item["status"] == "changed"
        for item in drifted["checks"]
    )


def test_reconcile_repairs_dirty_linked_worktrees_and_keeps_paths_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    linked = tmp_path / "linked-dirty"
    detached = tmp_path / "detached-dirty"
    _git(repo, "worktree", "add", "-b", "feature/reconcile", str(linked))
    _git(repo, "worktree", "add", "--detach", str(detached), "HEAD")
    (linked / "tracked.txt").write_text("linked draft\n", encoding="utf-8")
    (detached / "detached-note.txt").write_text("detached draft\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    decision = evaluate_policy(
        _weekday_policy(),
        now=datetime.fromisoformat("2026-08-18T12:00:00+08:00"),
    )

    bounded = reconcile_pending_changes(
        runtime_root=runtime_root,
        repo_path=repo,
        decision=decision,
    )
    assert bounded["scope"] == "current"
    assert bounded["scanned_worktree_count"] == 1
    assert bounded["dirty_worktree_count"] == 0

    preview = reconcile_pending_changes(
        runtime_root=runtime_root,
        repo_path=repo,
        decision=decision,
        include_linked_worktrees=True,
    )
    assert preview["status"] == "reconcile_preview"
    assert preview["scanned_worktree_count"] == 3
    assert preview["clean_worktree_count"] == 1
    assert preview["dirty_worktree_count"] == 2
    assert list_pending_changes(runtime_root=runtime_root)["count"] == 0

    executed = reconcile_pending_changes(
        runtime_root=runtime_root,
        repo_path=repo,
        decision=decision,
        include_linked_worktrees=True,
        execute=True,
        now=datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc),
    )
    assert executed["ok"] is True
    assert executed["changed_count"] == 2
    assert executed["error_count"] == 0
    projection = list_pending_changes(runtime_root=runtime_root)
    assert projection["count"] == 2
    serialized_public = json.dumps(
        {"reconcile": executed, "projection": projection}, sort_keys=True
    )
    assert str(tmp_path) not in serialized_public
    assert "tracked.txt" not in serialized_public
    assert "detached-note.txt" not in serialized_public
    assert all(item["changed_path_count"] == 1 for item in projection["changes"])

    locators = json.loads(
        (
            runtime_root / "repository-change-window" / "pending-change-locators.json"
        ).read_text(encoding="utf-8")
    )["changes"]
    private_paths = {
        path for locator in locators.values() for path in locator["changed_paths"]
    }
    assert private_paths == {"tracked.txt", "detached-note.txt"}
    assert {locator["worktree_path"] for locator in locators.values()} == {
        str(linked),
        str(detached),
    }
    verified = verify_pending_change(
        runtime_root=runtime_root,
        change_id=projection["changes"][0]["change_id"],
    )
    assert any(
        item["check"] == "private_changed_path_inventory"
        and item["status"] == "unchanged"
        for item in verified["checks"]
    )

    repeated = reconcile_pending_changes(
        runtime_root=runtime_root,
        repo_path=detached,
        decision=decision,
        include_linked_worktrees=True,
        execute=True,
        now=datetime(2026, 8, 18, 4, 1, tzinfo=timezone.utc),
    )
    assert repeated["changed_count"] == 0
    assert repeated["duplicate_count"] == 2
    assert list_pending_changes(runtime_root=runtime_root)["count"] == 2

    allowed = reconcile_pending_changes(
        runtime_root=runtime_root,
        repo_path=repo,
        decision={"allowed": True, "reason": "outside_blocked_window"},
        execute=True,
    )
    assert allowed["status"] == "window_open_noop"
    assert allowed["scanned_worktree_count"] == 0


def test_verify_detects_missing_branch_and_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    runtime_root = tmp_path / "runtime"
    decision = {"allowed": False, "reason": "test_gate"}

    linked = tmp_path / "linked-missing"
    _git(repo, "worktree", "add", "-b", "feature/missing", str(linked))
    recorded = record_pending_change(
        runtime_root=runtime_root,
        repo_path=linked,
        decision=decision,
        source="manual_cli",
        execute=True,
    )
    change_id = recorded["record"]["change_id"]
    _git(repo, "worktree", "remove", "--force", str(linked))
    missing_worktree = verify_pending_change(
        runtime_root=runtime_root,
        change_id=change_id,
    )
    assert missing_worktree["status"] == "missing_worktree"

    branch_record = record_pending_change(
        runtime_root=runtime_root,
        repo_path=repo,
        decision=decision,
        source="manual_cli",
        change_id="change_missingbranch123",
        execute=True,
    )
    _git(repo, "switch", "-c", "temporary-current")
    _git(repo, "branch", "-D", "main")
    missing_branch = verify_pending_change(
        runtime_root=runtime_root,
        change_id=branch_record["record"]["change_id"],
    )
    assert any(
        item["check"] == "branch" and item["status"] == "missing"
        for item in missing_branch["checks"]
    )


def test_verify_detects_branch_movement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    runtime_root = tmp_path / "runtime"
    recorded = record_pending_change(
        runtime_root=runtime_root,
        repo_path=repo,
        decision={"allowed": False, "reason": "test_gate"},
        source="manual_cli",
        execute=True,
    )
    (repo / "tracked.txt").write_text("advanced\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    previous = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "write-tree")
    advanced = _git(repo, "commit-tree", tree, "-p", previous, "-m", "advance")
    (repo / ".git" / "refs" / "heads" / "main").write_text(
        f"{advanced}\n", encoding="ascii"
    )

    result = verify_pending_change(
        runtime_root=runtime_root,
        change_id=recorded["record"]["change_id"],
    )
    assert result["status"] == "drifted"
    assert any(
        item["check"] == "branch_head"
        and item["status"] == "advanced_or_rewritten"
        and item["ok"] is False
        for item in result["checks"]
    )


@pytest.mark.parametrize("enforcement_level", list(EnforcementLevel))
def test_cli_install_verify_round_trip_preserves_lf_scripts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    enforcement_level: EnforcementLevel,
) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "base")
    args = [
        "--runtime-root", str(tmp_path / "runtime"), "--format", "json", "change-window",
    ]
    assert main([
        *args,
        "install", "--repo-path", str(repo),
        "--enforcement-level", enforcement_level.value,
        "--execute",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "installed"

    verify_args = [*args, "verify", "--repo-path", str(repo)]
    assert main(verify_args) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "ready"
    assert verified["verified"] is True
    assert verified["enabled"] is True

    hooks_dir = Path(_git(repo, "config", "--local", "--get", "core.hooksPath"))
    scripts = [hooks_dir / name for name in verified["managed_hook_names"]]
    if enforcement_level is EnforcementLevel.REFERENCE_GUARD:
        scripts.append(hooks_dir.parent / "ssh-command")
    for script in scripts:
        content = script.read_bytes()
        assert content.startswith(b"#!/bin/sh\n")
        assert b"\r\n" not in content

    scripts[0].write_bytes(scripts[0].read_bytes() + b"# modified\n")
    assert main(verify_args) == 1
    drifted = json.loads(capsys.readouterr().out)
    assert drifted["status"] == "drifted"
    assert drifted["verified"] is False


def test_cli_preview_and_global_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _repository(tmp_path, monkeypatch)
    runtime_root = tmp_path / "runtime"
    args = [
        "--runtime-root",
        str(runtime_root),
        "--format",
        "json",
        "change-window",
        "install",
        "--repo-path",
        str(repo),
    ]
    assert main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True
    assert git_hook_provider_status(repo_path=repo)["installed"] is False

    assert main([*args, "--execute"]) == 0
    installed = json.loads(capsys.readouterr().out)
    assert installed["status"] == "installed"
    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "change-window",
                "list",
            ]
        )
        == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert listed["changes"] == []
