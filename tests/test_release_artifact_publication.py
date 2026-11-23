"""Publication may resume, but must never replace already published bytes."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from loopx.cli_commands import update_artifact
from loopx.cli_commands.update_artifact import (
    HISTORICAL_DEFAULT_CONTRACT,
    PUBLIC_DEFAULT_CONTRACT,
    QUALIFIED_DEFAULT_CONTRACT_FIELD,
    RUNTIME_ARTIFACT_SCHEMA_VERSION,
)

SPEC = importlib.util.spec_from_file_location(
    "publication_artifacts", Path(__file__).parents[1] / "scripts/release_artifacts.py"
)
assert SPEC and SPEC.loader
owner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = owner
SPEC.loader.exec_module(owner)


@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize("metadata", ["valid", "mismatch", "malformed"])
def test_colocated_artifact_cli_never_initializes_the_candidate(tmp_path, isolated, metadata):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "loopx/control_plane/testing").mkdir(parents=True)
    shutil.copyfile(
        root / "loopx/control_plane/testing/release_metadata.py",
        tmp_path / "loopx/control_plane/testing/release_metadata.py",
    )
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts/release_artifacts.py"
    shutil.copyfile(root / "scripts/release_artifacts.py", script)
    sentinel = tmp_path / "loopx/initialized"
    (tmp_path / "loopx/__init__.py").write_text(
        '__version__: str = "1.2.3"\nfrom pathlib import Path\n'
        'Path(__file__).with_name("initialized").write_text("executed")\n'
        'raise RuntimeError("candidate package executed")\n', encoding="utf-8")
    version = "9.9.9" if metadata == "mismatch" else "1.2.3"
    project = f'[project]\nname = "loopx"\nversion = "{version}"\n'
    if metadata == "malformed":
        project = "[project\n"
    (tmp_path / "pyproject.toml").write_text(project, encoding="utf-8")
    result = subprocess.run([sys.executable, *(["-I", "-S"] if isolated else []),
                             str(script), "expected-tag"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert not sentinel.exists(), (result.stdout, result.stderr)
    assert result.returncode == (0 if metadata == "valid" else 2), result.stderr
    if metadata == "valid":
        assert result.stdout.strip() == "v1.2.3"
    else:
        assert result.stderr and "Traceback" not in result.stderr
        if metadata == "mismatch":
            assert "version mismatch" in result.stderr


def stable_runtime(root, identity, *, current):
    revision = "a" * 40
    profile = PUBLIC_DEFAULT_CONTRACT if current else HISTORICAL_DEFAULT_CONTRACT
    archive = root / "runtime" / f"loopx-runtime-{revision}.tar.gz"
    archive.write_bytes(b"qualified runtime bytes")
    manifest = {
        "schema_version": RUNTIME_ARTIFACT_SCHEMA_VERSION,
        "source_revision": revision,
        "package_version": identity.version,
        "channel": "stable",
        "size": archive.stat().st_size,
        "sha256": owner.sha256(archive),
        "urls": [f"https://github.com/owner/repo/releases/download/{identity.tag}/{archive.name}"],
        (QUALIFIED_DEFAULT_CONTRACT_FIELD if current else "default_contract"): profile,
    }
    (archive.parent / "loopx-runtime.json").write_text(json.dumps(manifest))
    qualification = dict.fromkeys((
        "ok", "clean_install", "upgrade", "doctor", "skills", "frontend_bundle_contract",
        "frontend_http", "no_frontend_build_tools", "license_notices", "activation_failure_restored",
    ), True)
    qualification.update(
        source_revision=revision, bytes=manifest["size"], sha256=manifest["sha256"],
        channel="stable", default_contract=profile,
    )
    (archive.parent / "qualification.json").write_text(json.dumps(qualification))


@pytest.fixture
def bundle(tmp_path):
    identity = owner.ReleaseIdentity("loopx", "1.2.3")
    (tmp_path / "packages").mkdir()
    (tmp_path / "runtime").mkdir()
    for name in ["loopx-1.2.3-py3-none-any.whl", "loopx-1.2.3.tar.gz"]:
        (tmp_path / "packages" / name).write_bytes(name.encode())
    stable_runtime(tmp_path, identity, current=False)
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


def test_engineering_qualified_c1_cannot_reach_unqualified_stable_release(bundle):
    root, identity = bundle
    stable_runtime(root, identity, current=True)
    calls, gh, download = transport([])
    with patch.object(owner, "gh", gh), patch.object(owner.subprocess, "run", download):
        with pytest.raises(owner.ReleaseArtifactError, match="pending public channel qualification"):
            owner.upload_immutable(root, identity, "owner/repo")
    assert not calls


def test_adoption_qualified_c1_can_publish_stable_runtime(bundle, monkeypatch):
    root, identity = bundle
    stable_runtime(root, identity, current=True)
    monkeypatch.setitem(update_artifact._ARTIFACT_DEFAULT_CONTRACTS, "public_channel", "current")
    calls, gh, download = transport([])
    with patch.object(owner, "gh", gh), patch.object(owner.subprocess, "run", download):
        owner.upload_immutable(root, identity, "owner/repo")
    uploads = [call for call in calls if call[:2] == ("release", "upload")]
    assert len(uploads) == 1 and uploads[0][2] == identity.tag
    assert str(root / "runtime/loopx-runtime.json") in uploads[0]


def test_stable_runtime_refuses_changed_bytes_before_github(bundle):
    root, identity = bundle
    (root / "runtime" / ("loopx-runtime-" + "a" * 40 + ".tar.gz")).write_bytes(b"changed")
    calls, gh, download = transport([])
    with patch.object(owner, "gh", gh), patch.object(owner.subprocess, "run", download):
        with pytest.raises(owner.ReleaseArtifactError, match="bytes differ"):
            owner.upload_immutable(root, identity, "owner/repo")
    assert not calls


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



@pytest.mark.parametrize("failure", ["policy-held", "missing", "changed", "draft", None])
def test_named_release_preserves_old_latest_until_qualified_bundle_readback(bundle, failure):
    root, identity = bundle
    if failure == "policy-held":
        stable_runtime(root, identity, current=True)
    files = owner.publication_files(root, identity)
    asset_files = files[:-1] if failure == "missing" else files
    old = {"id": 41, "tag_name": "v1.2.2"}
    release = {"id": 42, "tag_name": identity.tag, "draft": failure == "draft", "prerelease": False}
    latest = old
    notes_file = root / "NOTES.md"
    notes_file.write_text("Release notes", encoding="utf-8")
    calls, read_remote, download = transport(asset_files, mismatch=failure == "changed")

    def github(*args):
        nonlocal latest
        if args[:2] == ("release", "create"):
            assert args[2] == identity.tag and "--verify-tag" in args
            if "--latest=false" not in args:
                latest = release
            return b""
        if args == ("api", f"repos/owner/repo/releases/tags/{identity.tag}"):
            return json.dumps(release).encode()
        if args == ("api", "repos/owner/repo/releases/latest"):
            return json.dumps(latest).encode()
        if args == ("api", "--method", "PATCH", "repos/owner/repo/releases/42", "-f", "make_latest=true"):
            latest = release
            return b""
        return read_remote(*args)

    with patch.object(owner, "gh", github), patch.object(owner.subprocess, "run", download):
        owner.create_named_release(identity, "owner/repo", notes_file, "LoopX 1.2.3")
        assert latest == old
        if failure is not None:
            with pytest.raises(owner.ReleaseArtifactError):
                owner.promote_latest(root, identity, "owner/repo", execute=True)
            assert latest == old
        else:
            owner.promote_latest(root, identity, "owner/repo", execute=False)
            assert latest == old
            owner.promote_latest(root, identity, "owner/repo", execute=True)
            assert latest == release

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
