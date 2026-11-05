"""Exercise the Linux publisher against a bounded, filesystem-backed GitHub peer."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
import yaml

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
                         ("runtime-inventory", ".json"), ("qualification", ".json")):
        (runtime / f"{stem}-{REVISION}{suffix}").write_bytes(f"{stem}:immutable".encode())
    manifest = json.dumps({"source_revision": REVISION, "urls": [
        f"https://github.com/example/loopx/releases/download/{SNAPSHOT}/loopx-runtime-{REVISION}.tar.gz"]})
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
        env={**os.environ, "GITHUB_REPOSITORY": "example/loopx", "FAKE_GITHUB_STATE": str(root / "github.json")},
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)


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
def test_builder_freezes_correct_archive_url_before_publication(tmp_path, monkeypatch, channel):
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
    # Frontend qualification is independent; archive and descriptor creation
    # still execute through the real committed-source builder below.
    monkeypatch.setattr(builder, "validate_bundle", lambda *_a, **_kw: {
        "source_revision": revision, "source_files": {}, "files": {"index.html": "fixture"}})
    output = tmp_path / "runtime"
    descriptor = builder.build(root, output, repository="example/loopx", channel=channel)
    tag = f"runtime-main-{revision}" if channel == "main" else "v1.2.3"
    assert descriptor["urls"] == [f"https://github.com/example/loopx/releases/download/{tag}/loopx-runtime-{revision}.tar.gz"]
    assert json.loads((output / "loopx-runtime.json").read_text(encoding="utf-8")) == descriptor
    assert (output / f"loopx-runtime-{revision}.tar.gz").stat().st_size == descriptor["size"]
