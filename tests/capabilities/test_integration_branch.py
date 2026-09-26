from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from loopx.capabilities.integration_branch import (
    configure_integration_branch,
    integration_branch_status,
    sync_integration_branch,
)
from loopx.capabilities.integration_branch.core import (
    IntegrationBranchError,
    _worktree_for_branch,
)
from loopx.cli import main


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, path: str, content: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")
    _commit(repo, ".gitignore", ".loopx/\n", "ignore LoopX state")
    _commit(repo, "shared.txt", "base\n", "base")

    _git(repo, "switch", "-c", "feature-a")
    _commit(repo, "a.txt", "a1\n", "feature a")
    _git(repo, "switch", "main")

    _git(repo, "switch", "-c", "fix-b")
    _commit(repo, "b.txt", "b1\n", "fix b")
    _git(repo, "switch", "main")
    return repo


def _remote_repositories(tmp_path: Path) -> tuple[Path, Path]:
    seed_root = tmp_path / "seed"
    seed_root.mkdir()
    seed = _repository(seed_root)
    remote = tmp_path / "remote.git"
    _git(remote.parent, "init", "--bare", "-b", "main", str(remote))
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "origin", "main", "feature-a", "fix-b")

    consumer = tmp_path / "consumer"
    publisher = tmp_path / "publisher"
    _git(tmp_path, "clone", str(remote), str(consumer))
    _git(tmp_path, "clone", str(remote), str(publisher))
    for repo in (consumer, publisher):
        _git(repo, "config", "user.name", "LoopX Test")
        _git(repo, "config", "user.email", "loopx@example.invalid")
    return consumer, publisher


def _configure(repo: Path) -> dict[str, object]:
    return configure_integration_branch(
        repo_path=repo,
        base_ref="main",
        integration_branch="codex/local-integration",
        source_refs=["feature-a", "fix-b"],
        execute=True,
    )


def test_configure_is_preview_first_and_idempotent(tmp_path: Path) -> None:
    repo = _repository(tmp_path)

    preview = configure_integration_branch(
        repo_path=repo,
        base_ref="main",
        integration_branch="codex/local-integration",
        source_refs=["feature-a", "fix-b"],
    )
    plan_path = Path(preview["plan_file"])
    assert preview["status"] == "preview"
    assert not plan_path.exists()

    configured = _configure(repo)
    repeated = _configure(repo)
    assert configured["changed"] is True
    assert repeated["changed"] is False
    assert json.loads(plan_path.read_text(encoding="utf-8"))["last_sync"] is None

    with pytest.raises(IntegrationBranchError, match="configured base ref"):
        configure_integration_branch(
            repo_path=repo,
            base_ref="main",
            integration_branch="main",
            source_refs=["feature-a"],
        )


def test_plan_file_must_remain_under_loopx_state_root(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    outside_plan = tmp_path / "outside.json"

    for plan_file in (outside_plan, Path(".loopx/../outside.json")):
        with pytest.raises(IntegrationBranchError, match="must remain under"):
            configure_integration_branch(
                repo_path=repo,
                base_ref="main",
                integration_branch="codex/local-integration",
                source_refs=["feature-a"],
                plan_file=plan_file,
                execute=True,
            )
    assert not outside_plan.exists()

    configured = configure_integration_branch(
        repo_path=repo,
        base_ref="main",
        integration_branch="codex/local-integration",
        source_refs=["feature-a"],
        plan_file=Path(".loopx/custom-integration.json"),
        execute=True,
    )
    assert Path(configured["plan_file"]).is_file()


def test_plan_file_rejects_symlink_escape(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    state_root = repo / ".loopx"
    state_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (state_root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(IntegrationBranchError, match="must remain under"):
        configure_integration_branch(
            repo_path=repo,
            base_ref="main",
            integration_branch="codex/local-integration",
            source_refs=["feature-a"],
            plan_file=Path(".loopx/escape/integration.json"),
            execute=True,
        )
    assert not (outside / "integration.json").exists()


def test_plan_file_must_be_ignored_local_state(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _git(repo, "rm", ".gitignore")
    _git(repo, "commit", "-m", "stop ignoring LoopX state")

    with pytest.raises(IntegrationBranchError, match="must be ignored"):
        configure_integration_branch(
            repo_path=repo,
            base_ref="main",
            integration_branch="codex/local-integration",
            source_refs=["feature-a"],
        )


def test_sync_detects_review_updates_and_reconciles_exact_heads(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _configure(repo)

    initial = integration_branch_status(repo_path=repo)
    assert initial["status"] == "drifted"
    assert initial["drift_reasons"] == [{"kind": "never_synced"}]

    preview = sync_integration_branch(repo_path=repo)
    assert preview["status"] == "preview_ready"
    assert _git(repo, "branch", "--list", "codex/local-integration") == ""

    synced = sync_integration_branch(repo_path=repo, execute=True)
    assert synced["candidate_tree_sha"] == preview["candidate_tree_sha"]
    assert (
        synced["status_packet"]["plan"]["last_sync"]["candidate_tree_sha"]
        == preview["candidate_tree_sha"]
    )
    first_integration_head = str(synced["candidate_sha"])
    assert synced["status"] == "synced"
    assert integration_branch_status(repo_path=repo)["status"] == "in_sync"
    for source in ("feature-a", "fix-b"):
        _git(
            repo,
            "merge-base",
            "--is-ancestor",
            source,
            "codex/local-integration",
        )

    _git(repo, "switch", "feature-a")
    updated_source_head = _commit(repo, "a.txt", "a2\n", "review fix")
    _git(repo, "switch", "main")

    drifted = integration_branch_status(repo_path=repo)
    assert _git(repo, "rev-parse", "codex/local-integration") == first_integration_head
    assert {reason["kind"] for reason in drifted["drift_reasons"]} == {
        "source_ref_moved"
    }

    resynced = sync_integration_branch(repo_path=repo, execute=True)
    assert resynced["status"] == "synced"
    assert resynced["candidate_source"] == "incremental"
    assert resynced["candidate_sha"] != first_integration_head
    _git(
        repo,
        "merge-base",
        "--is-ancestor",
        updated_source_head,
        "codex/local-integration",
    )
    assert _git(repo, "rev-parse", "feature-a") == updated_source_head
    assert integration_branch_status(repo_path=repo)["status"] == "in_sync"

    _git(repo, "switch", "feature-a")
    (repo / "a.txt").write_text("a3\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "--amend", "--no-edit")
    rebased_source_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "main")

    rebuilt = sync_integration_branch(repo_path=repo, execute=True)
    assert rebuilt["candidate_source"] == "built"
    _git(
        repo,
        "merge-base",
        "--is-ancestor",
        rebased_source_head,
        "codex/local-integration",
    )


def test_remote_refresh_observes_published_source_intent_and_reconciles(
    tmp_path: Path,
) -> None:
    consumer, publisher = _remote_repositories(tmp_path)
    configure_integration_branch(
        repo_path=consumer,
        base_ref="origin/main",
        integration_branch="codex/local-integration",
        source_refs=["origin/feature-a", "origin/fix-b"],
        execute=True,
    )
    initial = sync_integration_branch(repo_path=consumer, execute=True)
    assert initial["status"] == "synced"

    _git(publisher, "switch", "feature-a")
    published_head = _commit(publisher, "a.txt", "a2\n", "published review fix")
    _git(publisher, "push", "origin", "feature-a")

    stale = integration_branch_status(repo_path=consumer)
    assert stale["status"] == "in_sync"
    refreshed = integration_branch_status(
        repo_path=consumer,
        refresh_remotes=True,
    )
    assert refreshed["status"] == "drifted"
    assert refreshed["remote_refresh"] == {
        "requested": True,
        "remotes": ["origin"],
    }
    assert refreshed["resolved"]["sources"][0]["sha"] == published_head

    reconciled = sync_integration_branch(
        repo_path=consumer,
        refresh_remotes=True,
        execute=True,
    )
    assert reconciled["status"] == "synced"
    assert reconciled["status_packet"]["remote_refresh"] == {
        "requested": True,
        "remotes": ["origin"],
    }
    _git(
        consumer,
        "merge-base",
        "--is-ancestor",
        published_head,
        "codex/local-integration",
    )


def test_remote_refresh_fetches_only_configured_tracking_refs(
    tmp_path: Path,
) -> None:
    consumer, publisher = _remote_repositories(tmp_path)
    configure_integration_branch(
        repo_path=consumer,
        base_ref="origin/main",
        integration_branch="codex/local-integration",
        source_refs=["origin/feature-a", "origin/fix-b"],
        execute=True,
    )
    sync_integration_branch(repo_path=consumer, execute=True)
    _git(consumer, "config", "--unset-all", "remote.origin.fetch")
    _git(
        consumer,
        "config",
        "--add",
        "remote.origin.fetch",
        "+refs/heads/main:refs/remotes/origin/main",
    )
    fetch_head = Path(_git(consumer, "rev-parse", "--git-path", "FETCH_HEAD"))
    if not fetch_head.is_absolute():
        fetch_head = consumer / fetch_head
    fetch_head.write_text("preserve existing fetch evidence\n", encoding="utf-8")

    _git(publisher, "switch", "feature-a")
    published_head = _commit(publisher, "a.txt", "a2\n", "published review fix")
    _git(publisher, "push", "origin", "feature-a")
    _git(publisher, "switch", "-c", "unrelated", "main")
    _commit(publisher, "unrelated.txt", "unrelated\n", "unrelated branch")
    _git(publisher, "push", "origin", "unrelated")

    refreshed = integration_branch_status(
        repo_path=consumer,
        refresh_remotes=True,
    )

    assert refreshed["resolved"]["sources"][0]["sha"] == published_head
    with pytest.raises(subprocess.CalledProcessError):
        _git(
            consumer,
            "rev-parse",
            "--verify",
            "refs/remotes/origin/unrelated",
        )
    assert fetch_head.read_text(encoding="utf-8") == (
        "preserve existing fetch evidence\n"
    )


@pytest.mark.parametrize("relation", ["remote_ahead", "diverged"])
def test_remote_refresh_reconciles_published_integration_head(
    tmp_path: Path,
    relation: str,
) -> None:
    consumer, publisher = _remote_repositories(tmp_path)
    configure_integration_branch(
        repo_path=consumer,
        base_ref="origin/main",
        integration_branch="codex/local-integration",
        source_refs=["origin/feature-a", "origin/fix-b"],
        execute=True,
    )
    initial = sync_integration_branch(repo_path=consumer, execute=True)
    initial_head = str(initial["candidate_sha"])
    _git(consumer, "push", "-u", "origin", "codex/local-integration")
    local_head = initial_head
    if relation == "diverged":
        _git(consumer, "switch", "codex/local-integration")
        _git(consumer, "commit", "--allow-empty", "-m", "local integration receipt")
        local_head = _git(consumer, "rev-parse", "HEAD")
        _git(consumer, "switch", "main")

    _git(publisher, "fetch", "origin", "codex/local-integration")
    _git(
        publisher,
        "switch",
        "-c",
        "codex/local-integration",
        "--track",
        "origin/codex/local-integration",
    )
    _git(publisher, "commit", "--allow-empty", "-m", "peer integration receipt")
    peer_head = _git(publisher, "rev-parse", "HEAD")
    _git(publisher, "push", "origin", "codex/local-integration")

    stale = integration_branch_status(repo_path=consumer)
    assert stale["status"] == ("in_sync" if relation == "remote_ahead" else "drifted")
    refreshed = integration_branch_status(
        repo_path=consumer,
        refresh_remotes=True,
    )
    assert refreshed["status"] == "drifted"
    assert refreshed["resolved"]["integration"]["remote"] == {
        "remote": "origin",
        "ref": "refs/remotes/origin/codex/local-integration",
        "sha": peer_head,
    }
    remote_reason = next(
        reason
        for reason in refreshed["drift_reasons"]
        if reason["kind"] == "integration_remote_head_moved"
    )
    assert remote_reason == {
        "kind": "integration_remote_head_moved",
        "branch": "codex/local-integration",
        "remote_ref": "refs/remotes/origin/codex/local-integration",
        "local_sha": local_head,
        "remote_sha": peer_head,
        "relation": relation,
    }

    with pytest.raises(IntegrationBranchError, match="integration remote"):
        sync_integration_branch(
            repo_path=consumer,
            candidate_ref=local_head,
            refresh_remotes=True,
        )

    reconciled = sync_integration_branch(
        repo_path=consumer,
        refresh_remotes=True,
        execute=True,
    )
    assert reconciled["candidate_source"] == "remote_reconciled"
    _git(
        consumer,
        "merge-base",
        "--is-ancestor",
        peer_head,
        "codex/local-integration",
    )
    _git(consumer, "push", "origin", "codex/local-integration")


def test_merge_conflict_keeps_previous_integration_head(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _configure(repo)
    first = sync_integration_branch(repo_path=repo, execute=True)
    old_head = str(first["candidate_sha"])

    _git(repo, "switch", "feature-a")
    _commit(repo, "shared.txt", "from-a\n", "feature a shared change")
    _git(repo, "switch", "fix-b")
    _commit(repo, "shared.txt", "from-b\n", "fix b shared change")
    _git(repo, "switch", "main")

    failed = sync_integration_branch(repo_path=repo, execute=True)
    assert failed["ok"] is False
    assert failed["status"] == "merge_failed"
    assert failed["source_ref"] == "fix-b"
    assert failed["integration_unchanged"] is True
    assert _git(repo, "rev-parse", "codex/local-integration") == old_head

    _git(repo, "switch", "-c", "manual-resolution", "main")
    _git(repo, "merge", "--no-ff", "--no-edit", "feature-a")
    merge = subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", "--no-edit", "fix-b"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert merge.returncode != 0
    (repo / "shared.txt").write_text("resolved\n", encoding="utf-8")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "--no-edit")
    resolved_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "main")

    with pytest.raises(IntegrationBranchError, match="does not contain source"):
        sync_integration_branch(repo_path=repo, candidate_ref="main")

    preview = sync_integration_branch(repo_path=repo, candidate_ref=resolved_head)
    assert preview["status"] == "preview_ready"
    assert preview["candidate_source"] == "supplied"

    synced = sync_integration_branch(
        repo_path=repo,
        candidate_ref=resolved_head,
        execute=True,
    )
    assert synced["status"] == "synced"
    assert synced["candidate_sha"] == resolved_head
    assert (
        synced["status_packet"]["plan"]["last_sync"]["candidate_source"] == "supplied"
    )
    assert integration_branch_status(repo_path=repo)["status"] == "in_sync"

    _git(repo, "switch", "feature-a")
    updated_source_head = _commit(repo, "a.txt", "a2\n", "review fix")
    _git(repo, "switch", "main")

    resynced = sync_integration_branch(repo_path=repo, execute=True)
    assert resynced["status"] == "synced"
    assert resynced["candidate_source"] == "incremental"
    assert _git(repo, "show", "codex/local-integration:shared.txt") == "resolved"
    _git(
        repo,
        "merge-base",
        "--is-ancestor",
        updated_source_head,
        "codex/local-integration",
    )


def test_adopt_current_integration_head_without_touching_worktree(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _configure(repo)
    _git(repo, "switch", "-c", "codex/local-integration", "main")
    _git(repo, "merge", "--no-ff", "--no-edit", "feature-a")
    _git(repo, "merge", "--no-ff", "--no-edit", "fix-b")
    integration_head = _git(repo, "rev-parse", "HEAD")
    (repo / "shared.txt").write_text("local diagnostic\n", encoding="utf-8")

    synced = sync_integration_branch(
        repo_path=repo,
        candidate_ref=integration_head,
        execute=True,
    )

    assert synced["status"] == "synced"
    assert synced["updated"] is False
    assert _git(repo, "rev-parse", "HEAD") == integration_head
    assert (repo / "shared.txt").read_text(encoding="utf-8") == "local diagnostic\n"
    assert integration_branch_status(repo_path=repo)["status"] == "in_sync"


def test_dirty_checked_out_integration_worktree_fails_closed(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _configure(repo)
    sync_integration_branch(repo_path=repo, execute=True)

    source_worktree = tmp_path / "source-worktree"
    _git(repo, "worktree", "add", str(source_worktree), "feature-a")
    _commit(source_worktree, "a.txt", "a2\n", "review update")
    _git(repo, "switch", "codex/local-integration")
    (repo / "shared.txt").write_text("dirty\n", encoding="utf-8")

    preview = sync_integration_branch(repo_path=repo)
    assert preview["status"] == "preview_ready"
    assert preview["updated"] is False

    with pytest.raises(IntegrationBranchError, match="worktree is dirty"):
        sync_integration_branch(repo_path=repo, execute=True)


def test_cli_configure_and_status_json(tmp_path: Path, capsys) -> None:
    repo = _repository(tmp_path)
    common = [
        "--format",
        "json",
        "integration-branch",
    ]
    assert (
        main(
            [
                *common,
                "configure",
                "--repo-path",
                str(repo),
                "--base-ref",
                "main",
                "--integration-branch",
                "codex/local-integration",
                "--source-branch",
                "feature-a",
                "--source-branch",
                "fix-b",
                "--execute",
            ]
        )
        == 0
    )
    configured = json.loads(capsys.readouterr().out)
    assert configured["status"] == "configured"

    assert (
        main(
            [
                *common,
                "status",
                "--repo-path",
                str(repo),
            ]
        )
        == 0
    )
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "drifted"
    assert status["drift_reasons"] == [{"kind": "never_synced"}]

    assert (
        main(
            [
                *common,
                "status",
                "--repo-path",
                str(repo),
                "--refresh-remotes",
            ]
        )
        == 0
    )
    refreshed = json.loads(capsys.readouterr().out)
    assert refreshed["remote_refresh"] == {"requested": True, "remotes": []}


def test_cli_rejects_plan_file_outside_loopx_state_root(
    tmp_path: Path,
    capsys,
) -> None:
    repo = _repository(tmp_path)
    outside_plan = tmp_path / "outside.json"

    assert (
        main(
            [
                "--format",
                "json",
                "integration-branch",
                "configure",
                "--repo-path",
                str(repo),
                "--base-ref",
                "main",
                "--integration-branch",
                "codex/local-integration",
                "--source-branch",
                "feature-a",
                "--plan-file",
                str(outside_plan),
                "--execute",
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "invalid_state"
    assert "must remain under" in payload["error"]
    assert not outside_plan.exists()


def test_worktree_for_branch_frames_porcelain_records_on_lf(tmp_path: Path) -> None:
    """A worktree path containing U+0085 must resolve to the full path.

    `git worktree list --porcelain` frames records on LF and prints the path
    verbatim, so `str.splitlines()` used to return the `.../odd` prefix as the
    worktree path. The caller cleans and resets that path, so a torn prefix
    could send `reset --hard` at the wrong checkout.
    """
    repo = _repository(tmp_path)
    odd = tmp_path / "odd\u0085worktree"
    _git(repo, "worktree", "add", str(odd), "feature-a")

    assert _worktree_for_branch(repo, "feature-a") == odd.resolve()
