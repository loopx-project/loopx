"""Installation provenance belongs to the source root, never its container."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.doctor_git import (
    GitRevisionRelation,
    git_metadata_for_root,
    git_revision_relation,
    trusted_release_ref_for_root,
)
from loopx.release_manifest import build_release_manifest
from loopx.skill_install_readback import build_skill_install_readback


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True,
        capture_output=True, encoding="utf-8",
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    # Git hooks/CI may otherwise lend these disposable fixtures another identity.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("LOOPX_RESOLVED_SOURCE_GIT_COMMIT", raising=False)
    root = tmp_path / "仓库"
    root.mkdir()
    git(root, "init", "--initial-branch=source")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Source Fixture")
    (root / "tracked.txt").write_text("original\n", encoding="utf-8")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-m", "source fixture")
    return root


def source_observations(source: Path, tmp_path: Path):
    manifest = build_release_manifest(
        release_root=tmp_path / "release", release_id="fixture", source_root=source,
        env={"LOOPX_PROMOTION_MODE": "canary"},
    )["source"]
    skills = build_skill_install_readback(
        skills_dir=tmp_path / "skills", skill_ids=[], source_root=source,
    )
    return git_metadata_for_root(source), manifest, skills


def test_archive_inside_unrelated_repository_has_no_git_identity(repository, tmp_path):
    archive = repository / "download" / "source"
    archive.mkdir(parents=True)
    (archive / "pyproject.toml").write_text("# archive\n", encoding="utf-8")
    doctor, manifest, skills = source_observations(archive, tmp_path)
    for observation in (doctor, manifest):
        assert {key: observation[key] for key in ("git_commit", "git_ref", "git_dirty")} == {
            "git_commit": None, "git_ref": None, "git_dirty": None,
        }
    assert skills["source"]["revision_kind"] == "skills_digest"
    assert skills["source"]["revision"] == skills["skills"]["digest"]
    assert skills["source"]["git_dirty"] is None

    parent_commit = git(repository, "rev-parse", "HEAD")
    git(repository, "remote", "add", "origin", "https://github.com/example/source.git")
    git(repository, "update-ref", "refs/remotes/origin/stable", parent_commit)
    assert trusted_release_ref_for_root(
        archive, repository="example/source", ref="stable",
    ) is None
    (repository / "tracked.txt").write_text("new\n", encoding="utf-8")
    git(repository, "commit", "-am", "next fixture")
    assert git_revision_relation(
        archive, installed_commit=parent_commit,
        comparison_commit=git(repository, "rev-parse", "HEAD"),
    ) is GitRevisionRelation.UNKNOWN


@pytest.mark.parametrize("explicit", ["environment", "manifest"])
def test_nested_archive_retains_explicit_source_identity(
    repository, tmp_path, monkeypatch, explicit,
):
    archive = repository / "archive"
    archive.mkdir()
    commit = "a" * 40
    if explicit == "environment":
        monkeypatch.setenv("LOOPX_RESOLVED_SOURCE_GIT_COMMIT", commit)
        env = {"LOOPX_RESOLVED_SOURCE_GIT_COMMIT": commit}
    else:
        (archive / "release.json").write_text(json.dumps({"source": {
            "kind": "github_archive", "git_commit": commit, "git_ref": "v1.0.0",
            "git_dirty": False, "ref": "v1.0.0",
        }}), encoding="utf-8")
        env = {"LOOPX_PROMOTION_MODE": "canary"}
    manifest = build_release_manifest(
        release_root=tmp_path / "release", release_id="fixture",
        source_root=archive, env=env,
    )
    skills = build_skill_install_readback(
        skills_dir=tmp_path / "skills", skill_ids=[], source_root=archive,
    )
    assert manifest["source"]["git_commit"] == commit
    assert manifest["source"]["git_ref"] == ("v1.0.0" if explicit == "manifest" else None)
    assert manifest["source"]["git_dirty"] is (False if explicit == "manifest" else None)
    assert skills["source"]["revision"] == commit
    assert skills["source"]["revision_kind"] == "git_commit"
    assert skills["source"]["git_dirty"] is False
    assert git_metadata_for_root(archive)["git_commit"] is None


@pytest.mark.parametrize("layout", ["checkout", "linked_worktree", "symlink"])
@pytest.mark.parametrize("state", ["clean", "dirty", "tag"])
def test_owned_git_sources_preserve_identity(repository, tmp_path, layout, state):
    source = repository
    if layout == "linked_worktree":
        source = tmp_path / "linked"
        git(repository, "worktree", "add", "-b", "linked-source", str(source))
        assert (source / ".git").is_file()
    elif layout == "symlink":
        source = tmp_path / "alias"
        source.symlink_to(repository, target_is_directory=True)
    expected_ref = "linked-source" if layout == "linked_worktree" else "source"
    if state == "tag":
        git(source, "tag", "v1.0.0")
        git(source, "checkout", "--detach")
        expected_ref = "v1.0.0"
    elif state == "dirty":
        (source / "tracked.txt").write_text("changed\n", encoding="utf-8")
    expected_commit = git(source, "rev-parse", "HEAD")
    doctor, manifest, skills = source_observations(source, tmp_path)
    for observation in (doctor, manifest):
        assert observation["git_commit"] == expected_commit
        assert observation["git_ref"] == expected_ref
        assert observation["git_dirty"] is (state == "dirty")
    assert skills["source"]["revision"] == expected_commit
    assert skills["source"]["git_dirty"] is (state == "dirty")


def test_missing_source_is_unavailable(tmp_path):
    doctor, manifest, skills = source_observations(tmp_path / "missing", tmp_path)
    assert doctor["git_commit"] is None
    assert manifest["git_commit"] is None
    assert skills["source"]["revision_kind"] == "skills_digest"


@pytest.mark.parametrize("marker", ["broken", "borrowed"])
def test_git_marker_alone_cannot_lend_an_archive_identity(
    repository, tmp_path, monkeypatch, marker,
):
    archive = repository / "archive"
    archive.mkdir()
    (archive / ".git").write_text("gitdir: missing\n", encoding="utf-8")
    if marker == "borrowed":
        # A hook's ambient Git directory still names the enclosing checkout.
        monkeypatch.setenv("GIT_DIR", str(repository / ".git"))
    doctor, manifest, skills = source_observations(archive, tmp_path)
    assert doctor["git_commit"] is None
    assert manifest["git_commit"] is None
    assert skills["source"]["revision_kind"] == "skills_digest"


@pytest.mark.skipif(os.name == "nt", reason="POSIX source installer")
@pytest.mark.parametrize("layout", ["checkout", "linked_worktree", "archive"])
def test_installer_auto_promotion_uses_only_the_owned_source(repository, tmp_path, layout):
    source = repository
    if layout == "linked_worktree":
        source = tmp_path / "linked"
        git(repository, "worktree", "add", "-b", "linked-source", str(source))
    elif layout == "archive":
        source = repository / "archive"
        source.mkdir()
    installer = (Path(__file__).parents[1] / "scripts/install-local.sh").read_text()
    functions = "\n".join(
        name + "() {" + installer.split(name + "() {", 1)[1].split("\n}\n", 1)[0] + "\n}"
        for name in ("read_source_git_commit", "resolve_default_promotion")
    )
    result = subprocess.run(
        ["bash", "-c", 'set -eu\nrepo_root="$1"\npromote_default_request=auto\n'
         'source_git_commit=""\n' + functions + '\n'
         'if resolve_default_promotion; then printf "promote:%s" "$promotion_mode"; '
         'else printf "hold:%s" "$promotion_mode"; fi', "source-test", str(source)],
        env={**os.environ, "LOOPX_PYTHON": sys.executable,
             "PYTHONPATH": str(Path(__file__).parents[1]),
             "LOOPX_APPROVED_DEFAULT_REF": "HEAD"},
        capture_output=True, text=True, check=True,
    )
    assert result.stdout == (
        "hold:canary_only_unverified_source" if layout == "archive"
        else "promote:trusted_main_auto"
    )
