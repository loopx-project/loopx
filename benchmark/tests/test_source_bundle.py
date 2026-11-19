"""Pinned archives admit prepared assets, never arbitrary ignored host files."""
from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile

import pytest

from benchmark.runtime.source import archive_source, checked_revision
from loopx.presentation.chat_bundle import MANIFEST, source_inputs, validate_bundle


@pytest.fixture
def prepared_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for args in [("init",), ("config", "user.name", "Fixture"),
                 ("config", "user.email", "fixture@example.invalid")]:
        subprocess.run(["git", "-C", str(source), *args], check=True,
                       capture_output=True)
    package = source / "apps/presentation/dashboard/package.json"
    package.parent.mkdir(parents=True)
    package.write_text("{}")
    (source / ".gitignore").write_text("loopx/web/chat/\nprivate/\n")
    subprocess.run(["git", "-C", str(source), "add", "apps", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-m", "source"], check=True,
                   capture_output=True)
    revision = checked_revision(source)
    bundle = source / "loopx/web/chat"
    (bundle / "assets").mkdir(parents=True)
    files = {"index.html": '<script src="/chat/assets/main.js"></script>',
             "manifest.webmanifest": "{}", "assets/main.js": "export const ready = true;",
             "asset-retention.json": json.dumps({"schema_version": "loopx_chat_asset_retention_v1",
                                                "generations": [["assets/main.js"]]})}
    for name, data in files.items():
        (bundle / name).write_text(data)
    (bundle / MANIFEST).write_text(json.dumps({
        "schema_version": "loopx_chat_bundle_v1", "source_revision": revision,
        "source_files": source_inputs(source), "current_assets": ["assets/main.js"],
        "files": {name: hashlib.sha256(data.encode()).hexdigest() for name, data in files.items()},
    }))
    (source / "private").mkdir()
    (source / "private/notes.txt").write_text("not an archive input")
    return source, revision, bundle


def test_complete_archive_preserves_validated_delivery_without_private_files(prepared_source, tmp_path):
    source, revision, bundle = prepared_source
    output = tmp_path / "source.tar"
    archive_source(source, revision, output)
    extracted = tmp_path / "extracted"
    with tarfile.open(output) as archive:
        assert not any(name.startswith("private/") for name in archive.getnames())
        archive.extractall(extracted, filter="data")
    validate_bundle(extracted / "loopx/web/chat", source_root=extracted)
    assert (extracted / "loopx/web/chat/assets/main.js").read_bytes() == (bundle / "assets/main.js").read_bytes()


@pytest.mark.parametrize("problem", ["corrupt", "stale", "extra", "symlink", "root_symlink", "uncommitted_input"])
def test_invalid_prepared_delivery_fails_closed(prepared_source, tmp_path, problem):
    source, revision, bundle = prepared_source
    if problem == "corrupt":
        (bundle / "assets/main.js").write_text("corrupt")
    elif problem == "stale":
        (source / "apps/presentation/dashboard/package.json").write_text('{"changed": true}')
    elif problem == "extra":
        (bundle / "private.txt").write_text("not a declared asset")
    elif problem == "symlink":
        asset = bundle / "assets/main.js"
        asset.unlink()
        asset.symlink_to(source / "private/notes.txt")
    elif problem == "root_symlink":
        moved = bundle.rename(tmp_path / "bundle")
        bundle.symlink_to(moved, target_is_directory=True)
    else:
        untracked = source / "apps/presentation/dashboard/src/private.ts"
        untracked.parent.mkdir(parents=True)
        untracked.write_text("export const privateInput = true;")
        witness = bundle / MANIFEST
        manifest = json.loads(witness.read_text())
        manifest["source_files"] = source_inputs(source)
        witness.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError):
        archive_source(source, revision, tmp_path / "source.tar")


def test_partial_archive_ignores_even_invalid_product_assets(prepared_source, tmp_path):
    source, revision, bundle = prepared_source
    (bundle / "private.txt").write_text("invalid delivery")
    output = tmp_path / "partial.tar"
    archive_source(source, revision, output, ("apps",))
    with tarfile.open(output) as archive:
        assert all(name.startswith("apps") for name in archive.getnames())


@pytest.mark.parametrize("case", [
    "warm_stale", "warm_corrupt", "warm_valid", "cold_corrupt",
    "warm_split_valid", "warm_split_missing",
])
def test_checkpoint_launcher_qualifies_source_before_native_activation(
    prepared_source, tmp_path, monkeypatch, case,
):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench import run

    source, revision, bundle = prepared_source
    if case == "warm_stale":
        # A clean pinned checkout can still carry an older ignored delivery.
        package = source / "apps/presentation/dashboard/package.json"
        package.write_text('{"changed": true}')
        subprocess.run(["git", "-C", str(source), "add", str(package)], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-m", "new source"],
                       check=True, capture_output=True)
        revision = checked_revision(source)
    elif case.endswith("corrupt"):
        (bundle / "assets/main.js").write_text("corrupt")
    monkeypatch.setenv("LOOPX_SRC_DIR", str(source))
    monkeypatch.setenv("LOOPX_EXPECTED_COMMIT", revision)
    monkeypatch.delenv("LOOPX_RUNNER_SRC_DIR", raising=False)
    monkeypatch.delenv("LOOPX_EXPECTED_RUNNER_COMMIT", raising=False)
    if case.startswith("warm_split"):
        runner = tmp_path / "runner"
        subprocess.run(["git", "clone", "--quiet", str(source), str(runner)], check=True)
        if case == "warm_split_valid":
            module = runner / "benchmark/runtime/worker.py"
            module.parent.mkdir(parents=True)
            module.write_text("# synthetic runner overlay\n")
            for args in [("config", "user.name", "Fixture"),
                         ("config", "user.email", "fixture@example.invalid"),
                         ("add", "benchmark"), ("commit", "-m", "runner overlay")]:
                subprocess.run(["git", "-C", str(runner), *args], check=True, capture_output=True)
        monkeypatch.setenv("LOOPX_RUNNER_SRC_DIR", str(runner))
        monkeypatch.setenv("LOOPX_EXPECTED_RUNNER_COMMIT", checked_revision(runner))
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr(run, "load_benchmark", lambda *args: None)

    class TaskBoundary(Exception):
        pass

    def materialize(*args):
        raise TaskBoundary("passed source admission")

    monkeypatch.setattr(run, "make_task_spec", materialize)
    logs = tmp_path / "logs"
    warm = case.startswith("warm")
    argv = ["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(logs),
            "--run-id", "receiver", "--worker", "heartbeat-explore" if warm else "official",
            "--model", "fixture", "--effort", "high", "--judge-url", "http://localhost:1"]
    if warm:
        request = tmp_path / "request.json"
        request.write_text(json.dumps({"destination": "/logs/agent/checkpoints", "point_metadata": {},
            "components": [{"id": "code", "source_path": "/task"}], "trigger": "fresh_replan",
            "native_session": True, "loopx_state": True, "evaluation_opportunity": True,
            "controller_handoff": True}))
        argv += ["--checkpoint-request", str(request),
            "--checkpoint-controller-file", str(tmp_path / "controller.json"),
            "--restore-checkpoint", str(tmp_path / "point"), "--restore-sha256", "a" * 64,
            "--restore-config", str(tmp_path / "config.toml"), "--restore-goal", "retained-goal",
            "--restore-agent", "retained-agent", "--restore-source-todo", "retained-todo"]
    if case in {"warm_stale", "warm_corrupt", "warm_split_missing"}:
        failure = subprocess.CalledProcessError if case == "warm_split_missing" else RuntimeError
        with pytest.raises(failure):
            run.main(argv)
        assert not (logs / "runs/receiver").exists()
        assert not list(logs.rglob("restored-phase-deadline"))
    else:
        # Ordinary controls keep their existing setup path. A valid warm
        # delivery can continue to task preparation without a live judge.
        with pytest.raises(TaskBoundary):
            run.main(argv)
