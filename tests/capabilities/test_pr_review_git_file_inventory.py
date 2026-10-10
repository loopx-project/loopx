"""A capped GitHub inventory is complete only with independent versioned evidence."""
from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest

from loopx.capabilities.pr_review_queue import github_source as source


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


@pytest.fixture(scope="module")
def inventory(tmp_path_factory):
    repo = tmp_path_factory.mktemp("inventory")
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.test")
    git(repo, "remote", "add", "origin", "https://github.com/owner/repo.git")
    (repo / "old.txt").write_text("one\n")
    (repo / "deleted.txt").write_text("gone\n")
    git(repo, "add", "old.txt", "deleted.txt")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "old.txt").unlink()
    (repo / "deleted.txt").unlink()
    (repo / "new.txt").write_text("one\ntwo\n")
    for index in range(3000):
        (repo / f"file-{index:04d}.txt").write_bytes(b"")
    special = " space\nwith\ttabs.txt "
    (repo / special).write_text("content\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "head")
    head = git(repo, "rev-parse", "HEAD")
    # Branch advancement must not change the PR's merge-base comparison.
    git(repo, "checkout", "-q", base)
    (repo / "base-only.txt").write_text("unrelated\n")
    git(repo, "add", "base-only.txt")
    git(repo, "commit", "-qm", "base advanced")
    advanced_base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", head)
    (repo / "binary.bin").write_bytes(b"\0binary")
    git(repo, "add", "binary.bin")
    git(repo, "commit", "-qm", "binary head")
    binary_head = git(repo, "rev-parse", "HEAD")
    snapshot = {"number": 7, "headRefOid": head, "baseRefOid": advanced_base,
                "changedFiles": 3003, "additions": 2, "deletions": 1}
    api = [
        {"filename": "new.txt", "status": "renamed", "previous_filename": "old.txt",
         "additions": 1, "deletions": 0},
        {"filename": "deleted.txt", "status": "removed", "additions": 0, "deletions": 1},
        # API-omitted per-file stats must be retained, separately from Git totals.
        {"filename": special, "status": "added", "additions": 0, "deletions": 0},
    ]
    # Keep the extra head separate so ordinary recovery still exercises 3003.
    (repo / ".git" / "fixture-binary-head").write_text(binary_head)
    return repo, snapshot, api, special


def attach(inventory, *, mutate_api=None, mutate_snapshot=None, drift=None, cwd=None):
    repo, original, api, _ = inventory
    row = copy.deepcopy(original)
    if mutate_snapshot:
        mutate_snapshot(row)
    pages = [copy.deepcopy(api)]
    if mutate_api:
        mutate_api(pages[0])
    calls = []
    snapshots = 0

    def gh(args, *, cwd=None):
        nonlocal snapshots
        calls.append(args)
        assert "statusCheckRollup" not in ",".join(args)
        if args[:2] == ["api", "--paginate"]:
            return copy.deepcopy(pages)
        fields = args[args.index("--json") + 1].split(",")
        if "body" in fields:
            return {**{field: [] for field in fields}, **row, "body": "author text",
                    "files": [{"path": "incomplete.txt"}]}
        snapshots += 1
        current = copy.deepcopy(row)
        if drift and snapshots == drift[0]:
            current[drift[1]] = drift[2]
        return current

    ok = source.attach_pr_review_details(row, repository="owner/repo",
            cwd=cwd or repo, run_gh_json=gh, wait_for_ci=False)
    return ok, row, calls


def test_capped_source_recovers_real_merge_base_and_preserves_api_stats(inventory):
    ok, row, calls = attach(inventory)
    assert ok
    files = {entry["path"]: entry for entry in row["files"]}
    assert len(files) == 3003
    assert "old.txt" not in files and "base-only.txt" not in files
    assert files["new.txt"] == {"path": "new.txt", "additions": 1, "deletions": 0,
                                "source": "github"}
    assert files[inventory[3]]["additions"] == 0
    assert files[inventory[3]]["source"] == "github"
    assert files["file-2999.txt"]["source"] == "git"
    # Mixed per-file display values need not sum to the verified remote totals.
    assert sum(item["additions"] for item in files.values()) == 1
    assert len(calls) == 4  # details, version fence, REST inventory, version readback


@pytest.mark.parametrize("field,value", [
    ("changedFiles", 3004), ("additions", 3), ("deletions", 0),
    ("headRefOid", "a" * 40), ("baseRefOid", "b" * 40),
    ("headRefOid", "--all"), ("baseRefOid", "HEAD"),
    ("additions", None), ("additions", True),
])
def test_count_totals_and_exact_objects_are_decisive(inventory, field, value):
    ok, row, _ = attach(inventory, mutate_snapshot=lambda row: row.update({field: value}))
    assert not ok
    assert "files" not in row


@pytest.mark.parametrize("fence", [1, 2])
@pytest.mark.parametrize("field,value", [
    ("headRefOid", "c" * 40), ("baseRefOid", "d" * 40),
    ("changedFiles", 3004), ("additions", 3), ("deletions", 2),
])
def test_drift_around_api_and_git_read_never_attaches(inventory, fence, field, value):
    assert not attach(inventory, drift=(fence, field, value))[0]


@pytest.mark.parametrize("mutation", [
    lambda rows: rows[0].update(previous_filename="missing.txt"),
    lambda rows: rows[0].update(previous_filename="new.txt"),
    lambda rows: rows[0].update(status="added"),
    lambda rows: rows[0].update(additions=2),
    lambda rows: rows.append(dict(rows[0])),
    lambda rows: rows.append({"filename": "extra.txt", "status": "renamed",
                             "previous_filename": "old.txt", "additions": 0}),
    lambda rows: rows.append({"filename": "not-in-diff.txt"}),
    lambda rows: rows[0].update(filename=42),
    lambda rows: rows[0].update(additions=1.5),
    lambda rows: rows[0].update(deletions=True),
])
def test_ambiguous_or_unobserved_renames_and_unknown_paths_fail(inventory, mutation):
    assert not attach(inventory, mutate_api=mutation)[0]


def test_wrong_repository_and_absent_checkout_fail(inventory, tmp_path):
    repo = tmp_path / "other"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", "https://github.com/other/repo.git")
    assert not attach(inventory, cwd=repo)[0]
    assert not attach(inventory, cwd=tmp_path)[0]


@pytest.mark.parametrize("fault", ["hybrid_binary", "malformed", "invalid_utf8", "command_failure"])
def test_invalid_local_diff_fails_closed(inventory, monkeypatch, fault):
    original = source._read_git

    def read(args, *, cwd):
        if "--numstat" in args:
            if fault == "command_failure":
                raise subprocess.CalledProcessError(1, ["git"])
            return {"hybrid_binary": b"-\t0\tbinary\0", "malformed": b"1\t0\tpath",
                    "invalid_utf8": b"1\t0\t\xff\0"}[fault]
        return original(args, cwd=cwd)

    monkeypatch.setattr(source, "_read_git", read)
    assert not attach(inventory)[0]


def test_ordinary_complete_rest_and_legacy_closeout_never_invoke_git(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary inventory must not touch Git")

    monkeypatch.setattr(source, "_read_git", forbidden)
    expected = [{"path": "one", "additions": 1, "deletions": 0},
                {"path": "two", "additions": 0, "deletions": 2}]
    calls = []

    def gh(args, **kwargs):
        calls.append(args)
        return [[{"filename": "one", "additions": 1}],
                [{"filename": "two", "deletions": 2}]]

    assert source._fetch_complete_pr_files(repository="owner/repo", number="7",
        expected_count=2, cwd=None, run_gh_json=gh) == expected
    assert len(calls) == 1
    assert source._fetch_complete_pr_files(repository="owner/repo", number="7",
        expected_count=3003, cwd=None, run_gh_json=gh) is None


def test_api_failure_does_not_trigger_git_guess(inventory, monkeypatch):
    repo, snapshot, _, _ = inventory
    def failure(*args, **kwargs):
        raise RuntimeError("API unavailable")
    def forbidden(*args, **kwargs):
        raise AssertionError("must not guess renames")
    monkeypatch.setattr(source, "_read_git", forbidden)
    assert source._fetch_complete_pr_files(repository="owner/repo", number="7",
        expected_count=3003, cwd=repo, run_gh_json=failure, snapshot=snapshot) is None


def test_local_git_reads_disable_fetch_and_external_execution(monkeypatch, tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=b"local", stderr=b"")
    monkeypatch.setattr(source.subprocess, "run", run)
    assert source._read_git(["diff", "--no-ext-diff", "--no-textconv"], cwd=tmp_path) == b"local"
    args, options = calls[0]
    assert args[:2] == ["git", "--no-replace-objects"]
    assert options["env"]["GIT_NO_LAZY_FETCH"] == "1"
    assert options["env"]["GIT_OPTIONAL_LOCKS"] == "0"
    assert options["timeout"] == 30


def test_production_target_scan_recovers_default_current_checkout(inventory, monkeypatch):
    repo, snapshot, api, _ = inventory
    monkeypatch.chdir(repo)
    calls = []
    def gh(args, **kwargs):
        calls.append(args)
        if args[0] == "api":
            return [api]
        fields = args[args.index("--json") + 1].split(",")
        if "body" in fields:
            return {**{field: [] for field in fields}, **snapshot,
                    "body": "motivation", "files": []}
        return copy.deepcopy(snapshot)
    packet = source.scan_github_pull_request_targets(repository="owner/repo",
        exact_heads=[f"7@{snapshot['headRefOid']}"], run_gh_json=gh, wait_for_ci=False)
    assert packet["complete"] is True
    assert len(packet["pull_requests"][0]["files"]) == 3003
    assert len(calls) == 5
    assert not any("statusCheckRollup" in ",".join(call) for call in calls)


def test_real_binary_row_has_unknown_counts_and_verified_text_totals(inventory):
    repo, _, _, _ = inventory
    binary_head = (repo / ".git" / "fixture-binary-head").read_text()
    ok, row, _ = attach(inventory, mutate_snapshot=lambda row:
                       row.update(headRefOid=binary_head, changedFiles=3004))
    assert ok
    binary = next(item for item in row["files"] if item["path"] == "binary.bin")
    assert binary == {"path": "binary.bin", "additions": None, "deletions": None,
                      "source": "git"}
    assert row["additions"] == 2 and row["deletions"] == 1
