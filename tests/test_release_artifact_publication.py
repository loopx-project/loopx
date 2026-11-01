"""Publication may resume, but must never replace already published bytes."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SPEC = importlib.util.spec_from_file_location(
    "publication_artifacts", Path(__file__).parents[1] / "scripts/release_artifacts.py"
)
assert SPEC and SPEC.loader
owner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = owner
SPEC.loader.exec_module(owner)


@pytest.fixture
def bundle(tmp_path):
    identity = owner.ReleaseIdentity("loopx", "1.2.3")
    (tmp_path / "packages").mkdir()
    (tmp_path / "runtime").mkdir()
    for name in ["loopx-1.2.3-py3-none-any.whl", "loopx-1.2.3.tar.gz"]:
        (tmp_path / "packages" / name).write_bytes(name.encode())
    (tmp_path / "runtime/qualification.json").write_bytes(b'{"ok":true}')
    (tmp_path / "RELEASE-SOURCES.json").write_text(json.dumps({
        "product_tag": identity.tag, "product_commit": "a" * 40,
    }))
    owner.write_manifest(tmp_path / "packages", tmp_path / "SHA256SUMS", identity)
    return tmp_path, identity


def transport(files, *, mismatch=False, wrong_case=False, attestation_failure=False):
    """Fake only the external boundary; inspect actual downloaded bytes."""
    calls = []
    assets = [{"id": i, "name": path.name, "size": path.stat().st_size}
              for i, path in enumerate(files)]
    if wrong_case:
        assets[-1]["name"] = assets[-1]["name"].upper()

    def gh(*args):
        calls.append(args)
        if args[0] == "attestation" and attestation_failure:
            raise subprocess.CalledProcessError(1, "attestation")
        if args[0:2] == ("release", "upload") or args[0] == "attestation":
            return b""
        if "/commits/" in args[1]:
            return ("a" * 40).encode()
        return json.dumps([assets] if "--paginate" in args else {"id": 42}).encode()

    def download(argv, *, stdout, check):
        index = int(argv[2].rsplit("/", 1)[1])
        stdout.write(b"changed" if mismatch else files[index].read_bytes())
        return subprocess.CompletedProcess(argv, 0)

    return calls, gh, download


@pytest.mark.parametrize("dry_run", [True, False])
def test_resume_only_uploads_missing_files(bundle, dry_run):
    root, identity = bundle
    files = owner.publication_files(root, identity)
    existing = list(files[:-1])
    calls, gh, download = transport(existing)
    with patch.object(owner, "gh", gh), patch.object(owner.subprocess, "run", download):
        owner.upload_immutable(root, identity, "owner/repo", dry_run=dry_run)
    uploads = [c for c in calls if c[:2] == ("release", "upload")]
    assert len(uploads) == (0 if dry_run else 1)
    if uploads:
        assert uploads[0] == ("release", "upload", identity.tag, str(files[-1]), "--repo", "owner/repo")


@pytest.mark.parametrize("wrong_case,mismatch", [(True, False), (False, True)])
def test_collision_or_changed_bytes_prevents_every_upload(bundle, wrong_case, mismatch):
    root, identity = bundle
    files = list(owner.publication_files(root, identity))
    calls, gh, download = transport(files[1:], mismatch=mismatch, wrong_case=wrong_case)
    with patch.object(owner, "gh", gh), patch.object(owner.subprocess, "run", download):
        with pytest.raises(owner.ReleaseArtifactError):
            owner.upload_immutable(root, identity, "owner/repo")
    assert not any(c[:2] == ("release", "upload") for c in calls)


def test_recovery_attestation_failure_cannot_publish(bundle):
    root, identity = bundle
    calls, gh, download = transport([], attestation_failure=True)
    with patch.object(owner, "gh", gh), patch.object(owner.subprocess, "run", download):
        with pytest.raises(subprocess.CalledProcessError):
            owner.upload_immutable(root, identity, "owner/repo", recover=True)
    assert not any(c[:2] == ("release", "upload") for c in calls)


def test_recovery_bundle_cannot_substitute_another_product(bundle):
    root, identity = bundle
    (root / "RELEASE-SOURCES.json").write_text(json.dumps({
        "product_tag": identity.tag, "product_commit": "b" * 40,
    }))
    calls, gh, download = transport([])
    with patch.object(owner, "gh", gh), patch.object(owner.subprocess, "run", download):
        with pytest.raises(owner.ReleaseArtifactError, match="identity"):
            owner.upload_immutable(root, identity, "owner/repo", recover=True)
    assert not any(c[:2] == ("release", "upload") for c in calls)


@pytest.mark.parametrize("field,value", [
    ("head_sha", "b" * 40), ("head_branch", "main"), ("event", "pull_request"),
    ("path", ".github/workflows/unrelated.yml"), ("repository", {"full_name": "other/repo"}),
    ("build_conclusion", "failure"),
])
def test_recovery_requires_matching_successful_tagged_build(field, value):
    run = {"repository": {"full_name": "owner/repo"}, "event": "release",
           "path": ".github/workflows/release-artifacts.yml", "head_sha": "a" * 40,
           "head_branch": "v1.2.3"}
    if field != "build_conclusion":
        run[field] = value
    jobs = {"jobs": [{"name": "build", "conclusion": value if field == "build_conclusion" else "success"}]}
    with patch.object(owner, "gh", side_effect=[json.dumps(run).encode(), json.dumps(jobs).encode()]):
        with pytest.raises(owner.ReleaseArtifactError):
            owner.verify_publication_run("owner/repo", "123", "v1.2.3", "a" * 40)


def test_matching_successful_tagged_build_is_recoverable():
    run = {"repository": {"full_name": "owner/repo"}, "event": "release",
           "path": ".github/workflows/release-artifacts.yml", "head_sha": "a" * 40,
           "head_branch": "v1.2.3"}
    jobs = {"jobs": [{"name": "build", "conclusion": "success"}]}
    with patch.object(owner, "gh", side_effect=[json.dumps(run).encode(), json.dumps(jobs).encode()]):
        owner.verify_publication_run("owner/repo", "123", "v1.2.3", "a" * 40)


def test_symlink_cannot_add_publication_input(bundle, tmp_path):
    root, identity = bundle
    (root / "runtime/link").symlink_to(root / "SHA256SUMS")
    with pytest.raises(owner.ReleaseArtifactError, match="regular files"):
        owner.publication_files(root, identity)
