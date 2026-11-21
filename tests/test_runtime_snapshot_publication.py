"""Exercise the Linux publisher against a bounded, filesystem-backed GitHub peer."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile

import pytest
import yaml
from loopx.cli_commands.update_artifact import (
    HISTORICAL_DEFAULT_CONTRACT,
    PUBLIC_DEFAULT_CONTRACT,
    validate_default_contract,
)

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40
PREVIOUS = "b" * 40
SNAPSHOT = f"runtime-main-{REVISION}"
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/runtime-snapshots.yml").read_text(encoding="utf-8"))
PUBLISH = next(step["run"] for step in WORKFLOW["jobs"]["publish"]["steps"] if "run" in step)
pytestmark = pytest.mark.skipif(os.name == "nt", reason="Publisher executes in Ubuntu Bash")

# Only the remote GitHub boundary is replaced. Bash, files, cmp and retry are real.
PEER = r'''import json
import os
from pathlib import Path
import sys
state_path = Path(os.environ["FAKE_GITHUB_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
state["calls"].append(args)
status = 0
try:
    if args[:2] == ["release", "view"]:
        status = int(args[2] not in state["releases"] or state.get("view_failure") == args[2])
    elif args[:2] == ["release", "create"]:
        assert "--prerelease" in args and "--latest=false" in args
        if args[2] in state["releases"]:
            status = 1
        else:
            state["releases"][args[2]] = {"sha": args[args.index("--target") + 1], "assets": {}}
    elif args[:2] == ["release", "download"]:
        assets = state["releases"][args[2]]["assets"]
        name = args[args.index("--pattern") + 1]
        if name not in assets:
            status = 1
        else:
            (Path(args[args.index("--dir") + 1]) / name).write_bytes(bytes.fromhex(assets[name]))
    elif args[:2] == ["release", "upload"]:
        assets = state["releases"][args[2]]["assets"]
        file = Path(args[3])
        pointer = file.name == "loopx-runtime.json"
        # gh --clobber deletes the old asset before POST, not atomically.
        if "--clobber" in args:
            assets.pop(file.name, None)
        if state.get("fail_upload") == file.name or (pointer and state.get("pointer_failure") == "after_delete"):
            status = 1
        elif file.name in assets and "--clobber" not in args:
            status = 1
        elif file.name not in assets and len(assets) >= 1000:
            print("file_count limited to 1000 assets per release", file=sys.stderr)
            status = 1
        else:
            assets[file.name] = file.read_bytes().hex()
            if pointer and state.get("pointer_failure") == "after_write":
                status = 1
    elif args[0] == "api":
        endpoint = next(arg for arg in args if arg.startswith("repos/"))
        if "/compare/" in endpoint:
            print(state["main_relation"] if endpoint.endswith("...main") else state["promotion"])
        elif "/commits/" in endpoint:
            print(state["releases"][endpoint.rsplit("/", 1)[1]]["sha"])
        elif "/git/refs/tags/" in endpoint:
            assert "PATCH" in args and "force=false" in args
            state["releases"]["runtime-main"]["sha"] = next(arg[4:] for arg in args if arg.startswith("sha="))
        else:
            raise AssertionError(args)
    else:
        raise AssertionError(args)
finally:
    state_path.write_text(json.dumps(state), encoding="utf-8")
sys.exit(status)
'''


@pytest.fixture
def publication(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for stem, suffix in (("loopx-runtime", ".tar.gz"), ("loopx-resources", ".tar.gz"),
                         ("runtime-inventory", ".json")):
        (runtime / f"{stem}-{REVISION}{suffix}").write_bytes(f"{stem}:immutable".encode())
    archive = runtime / f"loopx-runtime-{REVISION}.tar.gz"
    manifest = json.dumps({"source_revision": REVISION, "channel": "main", "default_contract": dict(PUBLIC_DEFAULT_CONTRACT),
        "size": archive.stat().st_size, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "urls": [
        f"https://github.com/example/loopx/releases/download/{SNAPSHOT}/loopx-runtime-{REVISION}.tar.gz"]})
    qualification = {"ok": True, "source_revision": REVISION, "channel": "main",
        "default_contract": dict(PUBLIC_DEFAULT_CONTRACT),
        "bytes": archive.stat().st_size, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "clean_install": True, "upgrade": True, "doctor": True, "skills": True,
        "frontend_bundle_contract": True, "frontend_http": True,
        "no_frontend_build_tools": True, "license_notices": True, "activation_failure_restored": True}
    (runtime / f"qualification-{REVISION}.json").write_text(json.dumps(qualification), encoding="utf-8")
    (runtime / "loopx-runtime.json").write_text(manifest, encoding="utf-8")
    (runtime / f"loopx-runtime-{REVISION}.json").write_text(manifest, encoding="utf-8")
    assets = {f"historical-{i}": b"historical".hex() for i in range(999)}
    assets["loopx-runtime.json"] = json.dumps({"source_revision": PREVIOUS}).encode().hex()
    state = {"calls": [], "main_relation": "identical", "promotion": "ahead",
             "releases": {"runtime-main": {"sha": PREVIOUS, "assets": assets}}}
    (tmp_path / "github.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "github.py").write_text(PEER, encoding="utf-8")
    return tmp_path


def read_state(root):
    return json.loads((root / "github.json").read_text(encoding="utf-8"))


def write_state(root, state):
    (root / "github.json").write_text(json.dumps(state), encoding="utf-8")


def publish(root):
    # The production workflow uses python from setup-python. Bind the same
    # interpreter as pytest without requiring a particular host PATH alias.
    functions = f"gh() {{ {shlex.quote(sys.executable)} github.py \"$@\"; }}\n"
    functions += f"python() {{ {shlex.quote(sys.executable)} \"$@\"; }}\n"
    return subprocess.run(["bash", "-c", functions + PUBLISH], cwd=root,
        env={**os.environ, "PYTHONPATH": str(ROOT), "GITHUB_REPOSITORY": "example/loopx", "FAKE_GITHUB_STATE": str(root / "github.json")},
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)


@pytest.mark.parametrize("failure", [
    "missing", "invalid_json", "failed", "incomplete", "wrong_revision", "wrong_channel",
    "wrong_size", "wrong_digest", "archive_changed", "manifest_changed", "frozen_manifest_changed", "truthy_status",
    "missing_contract", "unknown_contract", "historical_default", "mismatched_contract", "missing_observed_contract",
])
def test_unqualified_runtime_never_changes_remote_state(publication, failure):
    qualification = publication / "runtime" / f"qualification-{REVISION}.json"
    record = json.loads(qualification.read_text(encoding="utf-8"))
    if failure == "missing":
        qualification.unlink()
    elif failure == "invalid_json":
        qualification.write_text("{", encoding="utf-8")
    elif failure == "frozen_manifest_changed":
        (publication / "runtime" / f"loopx-runtime-{REVISION}.json").write_text("{}", encoding="utf-8")
    elif failure in {"missing_contract", "unknown_contract", "historical_default"}:
        path = publication / "runtime/loopx-runtime.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if failure == "missing_contract":
            data.pop("default_contract")
        else:
            profile = (HISTORICAL_DEFAULT_CONTRACT
                       if failure == "historical_default" else {"surface": "future"})
            data["default_contract"] = record["default_contract"] = profile
        # All engineering checks, identities and frozen bytes still agree.
        for name in ["loopx-runtime.json", f"loopx-runtime-{REVISION}.json"]:
            (publication / "runtime" / name).write_text(json.dumps(data), encoding="utf-8")
        qualification.write_text(json.dumps(record), encoding="utf-8")
    elif failure in {"mismatched_contract", "missing_observed_contract"}:
        if failure == "missing_observed_contract":
            record.pop("default_contract")
        else:
            record["default_contract"] = {"surface": "complete_turn"}
        qualification.write_text(json.dumps(record), encoding="utf-8")
    elif failure in {"archive_changed", "manifest_changed"}:
        if failure == "archive_changed":
            (publication / "runtime" / f"loopx-runtime-{REVISION}.tar.gz").write_bytes(b"unqualified bytes")
        else:
            manifest = publication / "runtime/loopx-runtime.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["channel"] = "stable"
            manifest.write_text(json.dumps(data), encoding="utf-8")
    else:
        if failure == "incomplete":
            del record["activation_failure_restored"]
        else:
            field, value = {"failed": ("ok", False), "wrong_revision": ("source_revision", PREVIOUS),
                "wrong_channel": ("channel", "stable"), "wrong_size": ("bytes", 1),
                "wrong_digest": ("sha256", "0" * 64), "truthy_status": ("clean_install", "true")}[failure]
            record[field] = value
        qualification.write_text(json.dumps(record), encoding="utf-8")
    before = read_state(publication)
    result = publish(publication)
    assert result.returncode != 0
    assert read_state(publication) == before


def test_full_legacy_release_keeps_history_and_publishes_then_retries(publication):
    history = read_state(publication)["releases"]["runtime-main"]["assets"].copy()
    result = publish(publication)
    assert result.returncode == 0, result.stderr
    state = read_state(publication)
    assert len(state["releases"]["runtime-main"]["assets"]) == 1000
    assert len(state["releases"][SNAPSHOT]["assets"]) == 5
    for name, content in history.items():
        if name != "loopx-runtime.json":
            assert state["releases"]["runtime-main"]["assets"][name] == content
    pointer = bytes.fromhex(state["releases"]["runtime-main"]["assets"]["loopx-runtime.json"])
    assert json.loads(pointer)["source_revision"] == REVISION
    uploads = [call for call in state["calls"] if call[:2] == ["release", "upload"]]
    assert [call[2] for call in uploads] == [SNAPSHOT] * 5 + ["runtime-main"]
    assert all("--clobber" not in call for call in uploads[:-1])
    state["calls"] = []
    state["promotion"] = "identical"
    write_state(publication, state)
    result = publish(publication)
    assert result.returncode == 0, result.stderr
    uploads = [call for call in read_state(publication)["calls"] if call[:2] == ["release", "upload"]]
    assert len(uploads) == 1 and uploads[0][2] == "runtime-main"


def test_partial_publication_resumes_without_advancing_the_pointer(publication):
    state = read_state(publication)
    state["fail_upload"] = f"qualification-{REVISION}.json"
    write_state(publication, state)
    assert publish(publication).returncode != 0
    state = read_state(publication)
    assert state["releases"]["runtime-main"]["sha"] == PREVIOUS
    assert 0 < len(state["releases"][SNAPSHOT]["assets"]) < 5
    del state["fail_upload"]
    write_state(publication, state)
    result = publish(publication)
    assert result.returncode == 0, result.stderr
    assert len(read_state(publication)["releases"][SNAPSHOT]["assets"]) == 5


@pytest.mark.parametrize("collision", ["bytes", "revision"])
def test_immutable_collision_cannot_advance_channel(publication, collision):
    state = read_state(publication)
    state["releases"][SNAPSHOT] = {"sha": PREVIOUS if collision == "revision" else REVISION,
        "assets": {} if collision == "revision" else {f"loopx-resources-{REVISION}.tar.gz": b"different".hex()}}
    write_state(publication, state)
    assert publish(publication).returncode != 0
    state = read_state(publication)
    assert state["releases"]["runtime-main"]["sha"] == PREVIOUS
    assert not any("--clobber" in call for call in state["calls"])


@pytest.mark.parametrize("main_relation,promotion,success", [
    ("ahead", "behind", True), ("diverged", "ahead", False), ("identical", "diverged", False),
])
def test_lineage_and_older_runs_never_replace_channel(publication, main_relation, promotion, success):
    state = read_state(publication)
    state.update(main_relation=main_relation, promotion=promotion)
    write_state(publication, state)
    assert (publish(publication).returncode == 0) == success
    state = read_state(publication)
    assert state["releases"]["runtime-main"]["sha"] == PREVIOUS
    assert not any("--clobber" in call for call in state["calls"])


@pytest.mark.parametrize("failure", ["after_delete", "after_write"])
def test_pointer_retry_recovers_deleted_or_unacknowledged_upload(publication, failure):
    state = read_state(publication)
    state["pointer_failure"] = failure
    write_state(publication, state)
    assert publish(publication).returncode != 0
    state = read_state(publication)
    channel = state["releases"]["runtime-main"]
    assert channel["sha"] == REVISION
    assert ("loopx-runtime.json" in channel["assets"]) == (failure == "after_write")
    assert len(state["releases"][SNAPSHOT]["assets"]) == 5
    del state["pointer_failure"]
    state["promotion"] = "identical"
    write_state(publication, state)
    result = publish(publication)
    assert result.returncode == 0, result.stderr
    channel = read_state(publication)["releases"]["runtime-main"]
    assert len(channel["assets"]) == 1000
    assert bytes.fromhex(channel["assets"]["loopx-runtime.json"]) == (publication / "runtime/loopx-runtime.json").read_bytes()


def test_first_snapshot_bootstraps_the_channel_after_immutable_uploads(publication):
    state = read_state(publication)
    state["releases"] = {}
    write_state(publication, state)
    result = publish(publication)
    assert result.returncode == 0, result.stderr
    state = read_state(publication)
    assert len(state["releases"]["runtime-main"]["assets"]) == 1
    assert len(state["releases"][SNAPSHOT]["assets"]) == 5


@pytest.mark.parametrize("tag", ["runtime-main", SNAPSHOT])
def test_failed_view_does_not_replace_an_existing_release(publication, tag):
    state = read_state(publication)
    state["releases"][SNAPSHOT] = {"sha": REVISION, "assets": {}}
    state["view_failure"] = tag
    write_state(publication, state)
    history = state["releases"]["runtime-main"].copy()
    assert publish(publication).returncode != 0
    state = read_state(publication)
    assert state["releases"]["runtime-main"] == history
    assert not any("--clobber" in call for call in state["calls"])


@pytest.mark.parametrize("channel", ["main", "stable"])
@pytest.mark.parametrize("supported", [True, False], ids=["supported-default", "unqualified-default"])
def test_builder_qualifies_committed_default_before_writing_artifacts(tmp_path, monkeypatch, channel, supported):
    from scripts import runtime_artifact as builder

    root = tmp_path / "source"
    root.mkdir()
    for name in builder.PATHS:
        path = root / name
        if name in {"loopx", "scripts", "skills", "docs", "man", "examples", "apps/presentation", ".github"}:
            path.mkdir(parents=True, exist_ok=True)
            (path / "fixture.txt").write_text("public fixture", encoding="utf-8")
        else:
            path.write_text("public fixture", encoding="utf-8")
    (root / "scripts/install-local.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    bundle = root / "loopx/web/chat"
    bundle.mkdir(parents=True)
    (bundle / "index.html").write_text("<p>fixture</p>", encoding="utf-8")
    (bundle / "bundle-manifest.json").write_text("{}", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "Fixture Author", "GIT_COMMITTER_NAME": "Fixture Author",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
    for args in (["init", "--quiet"], ["add", "--", *builder.PATHS], ["commit", "--quiet", "-m", "Synthetic runtime"]):
        subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True, timeout=15)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, encoding="utf-8").strip()
    (root / "loopx/fixture.txt").write_text("uncommitted runtime", encoding="utf-8")
    # Frontend qualification is independent; archive and descriptor creation
    # still execute through the real committed-source builder below.
    monkeypatch.setattr(builder, "validate_bundle", lambda *_a, **_kw: {
        "source_revision": revision, "source_files": {}, "files": {"index.html": "fixture"}})

    def observe(source: Path) -> dict:
        assert source != root
        assert (source / "loopx/fixture.txt").read_text(encoding="utf-8") == "public fixture"
        if supported:
            return dict(PUBLIC_DEFAULT_CONTRACT)
        return dict(HISTORICAL_DEFAULT_CONTRACT)

    monkeypatch.setattr(builder, "observe_default_contract", observe)
    output = tmp_path / "runtime"
    if not supported:
        output.mkdir()
        previous = output / "loopx-runtime.json"
        previous.write_bytes(b"previous descriptor")
        with pytest.raises(ValueError, match="unqualified for public adoption"):
            builder.build(root, output, repository="example/loopx", channel=channel)
        assert list(output.iterdir()) == [previous]
        assert previous.read_bytes() == b"previous descriptor"
        return

    descriptor = builder.build(root, output, repository="example/loopx", channel=channel)
    tag = f"runtime-main-{revision}" if channel == "main" else "v1.2.3"
    assert descriptor["urls"] == [f"https://github.com/example/loopx/releases/download/{tag}/loopx-runtime-{revision}.tar.gz"]
    assert json.loads((output / "loopx-runtime.json").read_text(encoding="utf-8")) == descriptor
    runtime = output / f"loopx-runtime-{revision}.tar.gz"
    assert runtime.stat().st_size == descriptor["size"]
    assert descriptor["default_contract"] == PUBLIC_DEFAULT_CONTRACT
    with tarfile.open(runtime, "r:gz") as archive:
        identity = archive.extractfile("runtime-artifact.json")
        assert identity is not None
        assert json.load(identity)["default_contract"] == descriptor["default_contract"]


def test_release_probe_observes_real_default_without_model_or_active_goal():
    from scripts.qualify_runtime_artifact import observe_default_contract

    observed = validate_default_contract(observe_default_contract(ROOT))
    assert observed == {"surface": "complete_turn", "schema_version": "loopx_turn_envelope_v1",
                        "interaction_schema_version": "loopx_interaction_contract_v0"}
