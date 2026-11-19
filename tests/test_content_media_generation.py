"""Footage intent semantics with deterministic cloud facts, never paid CI calls."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import threading
from urllib.error import HTTPError

from PIL import Image
import pytest

from loopx.capabilities.content_ops import generation, media
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler


@pytest.fixture
def work(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "registry.json"
    runtime = tmp_path / "runtime"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(runtime),
                "goals": [
                    {"id": "media-demo", "repo": str(project), "status": "active"}
                ],
            }
        )
    )
    return generation.GenerationWorkspace(
        media.MediaWorkspace(
            registry_path=registry, goal_id="media-demo", runtime_root=runtime
        )
    ), registry


def brief():
    return {
        "schema_version": "content_ops_generation_plan_v0",
        "title": "A complete return",
        "model": "doubao-seedance-2-5-260628",
        "prompt": "One gold artifact returns to its requester. No text.",
        "seconds": 5,
        "resolution": "720p",
        "references": [],
        "watermark": True,
        "generate_audio": False,
        "return_last_frame": True,
        "quote": {
            "cny_per_million_tokens": 70,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "source_url": "https://console.volcengine.com/ark/region:cn-beijing/docs/model-pricing",
            "max_estimated_cny": 8,
        },
    }


@pytest.fixture
def provider(monkeypatch):
    path = (
        Path(__file__).resolve().parents[1]
        / "packages/loopx-media/src/loopx_media/generation.py"
    )
    monkeypatch.syspath_prepend(str(path.parents[1]))
    spec = importlib.util.spec_from_file_location("loopx_media.generation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def connect(monkeypatch, work, provider):
    monkeypatch.setattr(
        generation.GenerationWorkspace,
        "_call",
        lambda self, record, op: provider.operate(
            {
                "protocol": "content_ops_media_renderer_v0",
                "operation": "generation." + op,
                "plan": record["plan"],
                "plan_digest": record["plan_digest"],
                "sources": record["sources"],
                "directory": str(self._bundle(record["generation_id"])),
                "task_id": record.get("task_id"),
            }
        ),
    )
    monkeypatch.setenv("ARK_API_KEY", "synthetic-test-only")
    monkeypatch.delenv("ARK_BASE_URL", raising=False)


def test_prepare_is_immutable_hash_bound_and_cloud_read_free(work, monkeypatch):
    ws, _ = work
    image = ws.media.project / "frame.png"
    Image.new("RGBA", (768, 432), (30, 60, 120, 200)).save(image)
    source = {
        "role": "first_frame",
        "path": image.name,
        "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "source_ref": "synthetic:frame",
        "visibility": "public_safe",
    }
    plan = {**brief(), "references": [source]}
    monkeypatch.setattr(
        generation.GenerationWorkspace,
        "_call",
        lambda *a: pytest.fail("local reads must not call cloud"),
    )
    prepared = ws.prepare(plan, source_directory=ws.media.project)
    ident = prepared["generation_id"]
    assert (
        ws.prepare(plan, source_directory=ws.media.project, generation_id=ident)[
            "plan_digest"
        ]
        == prepared["plan_digest"]
    )
    with pytest.raises(ValueError, match="immutable"):
        ws.prepare(
            {**plan, "prompt": "changed"},
            source_directory=ws.media.project,
            generation_id=ident,
        )
    image.write_bytes(b"changed")
    assert ws.inspect(ident)["references"][0]["sha256"] == source["sha256"]
    with pytest.raises(ValueError, match="Source bytes changed"):
        ws.prepare(plan, source_directory=ws.media.project)
    assert ws.list()["generations"][0]["publish_authorized"] is False
    for invalid in ["../secret", "/secret", "gen_not-a-valid-id"]:
        with pytest.raises(ValueError):
            ws.inspect(invalid)


def test_missing_credential_has_no_paid_effect_and_retains_prepared_input(
    work, provider, monkeypatch
):
    ws, _ = work
    connect(monkeypatch, ws, provider)
    monkeypatch.delenv("ARK_API_KEY")
    monkeypatch.setattr(
        provider, "api", lambda *a: pytest.fail("no credential must not call API")
    )
    ident = ws.prepare(brief(), source_directory=ws.media.project)["generation_id"]
    result = ws.operate(ident, "submit", expected_revision=1)
    assert result["ok"] is False and result["status"] == "prepared"
    assert not (ws._bundle(ident) / "submission.json").exists()


def test_duplicate_paid_submission_and_crash_recovery_keep_one_provider_task(
    work, provider, monkeypatch
):
    ws, _ = work
    connect(monkeypatch, ws, provider)
    calls = []

    def api(method, suffix, payload=None):
        calls.append((method, suffix))
        if method == "POST":
            return {"id": "cgt-synthetic-001"}
        return {
            "id": "cgt-synthetic-001",
            "model": provider.MODEL,
            "status": "running",
            "duration": 5,
            "usage": {"completion_tokens": 0},
        }

    monkeypatch.setattr(provider, "api", api)
    ident = ws.prepare(brief(), source_directory=ws.media.project)["generation_id"]
    submitted = ws.operate(ident, "submit", expected_revision=1)
    assert submitted["status"] == "submitted" and submitted["observation"] is None
    ws.operate(ident, "submit", expected_revision=1)
    assert [method for method, _ in calls].count("POST") == 1
    # A stopped parent missed stdout after the provider journal was fsynced.
    record = ws._record(ident)
    record.update(status="submitting", task_id=None)
    media._save(ws._bundle(ident) / "record.json", record)
    recovered = ws.operate(ident, "check", expected_revision=1)
    assert (
        recovered["task_id"] == "cgt-synthetic-001" and recovered["status"] == "running"
    )
    assert [method for method, _ in calls].count("POST") == 1
    with pytest.raises(ValueError, match="revision"):
        ws.operate(ident, "check", expected_revision=2)


def test_windows_journal_keeps_file_durability_and_submits_once(
    work, provider, monkeypatch
):
    from types import SimpleNamespace

    journal = importlib.import_module("loopx_media.journal")
    real_os, synced = journal.os, []

    def sync(fd):
        synced.append(fd)
        real_os.fsync(fd)

    def unsupported_directory(*_):
        raise PermissionError(13, "Windows CRT cannot open a directory")

    monkeypatch.setattr(journal, "os", SimpleNamespace(
        name="nt", fsync=sync, open=unsupported_directory,
        O_RDONLY=real_os.O_RDONLY, close=real_os.close, chmod=real_os.chmod,
    ))
    ws, _ = work
    connect(monkeypatch, ws, provider)
    calls = []

    def api(method, *_):
        calls.append(method)
        return {"id": "cgt-windows-journal"}

    monkeypatch.setattr(provider, "api", api)
    ident = ws.prepare(brief(), source_directory=ws.media.project)["generation_id"]
    assert ws.operate(ident, "submit", expected_revision=1)["status"] == "submitted"
    saved = json.loads((ws._bundle(ident) / "submission.json").read_text())
    assert saved["task_id"] == "cgt-windows-journal"
    assert saved["plan_digest"] == ws.inspect(ident)["plan_digest"]
    ws.operate(ident, "submit", expected_revision=1)
    assert calls == ["POST"] and len(synced) >= 2


def test_posix_journal_directory_sync_failure_is_not_silently_ignored(
    tmp_path, provider, monkeypatch
):
    from types import SimpleNamespace

    journal = importlib.import_module("loopx_media.journal")
    real_os = journal.os

    def unavailable_directory(*_):
        raise PermissionError(13, "Synthetic POSIX durability failure")

    monkeypatch.setattr(journal, "os", SimpleNamespace(
        name="posix", fsync=real_os.fsync, open=unavailable_directory,
        O_RDONLY=real_os.O_RDONLY, close=real_os.close,
    ))
    with pytest.raises(PermissionError, match="durability failure"):
        provider.save(tmp_path / "journal.json", {"task_id": "cgt-preserved"})
    assert json.loads((tmp_path / "journal.json").read_text())["task_id"] == "cgt-preserved"


def test_ambiguous_post_is_durable_unknown_and_never_reposted(
    work, provider, monkeypatch
):
    ws, _ = work
    connect(monkeypatch, ws, provider)
    calls = []

    def timeout(*args):
        calls.append(args[0])
        raise TimeoutError("synthetic lost response")

    monkeypatch.setattr(provider, "api", timeout)
    ident = ws.prepare(brief(), source_directory=ws.media.project)["generation_id"]
    assert (
        ws.operate(ident, "submit", expected_revision=1)["status"]
        == "submission_unknown"
    )
    ws.operate(ident, "submit", expected_revision=1)
    read = ws.operate(ident, "check", expected_revision=1)
    assert read["error_code"] == "submission_requires_reconciliation"
    assert calls == ["POST"]
    assert (
        "synthetic-test-only" not in (ws._bundle(ident) / "submission.json").read_text()
    )


def test_known_rejection_and_cancellation_race_cannot_erase_a_finished_task(
    work, provider, monkeypatch
):
    ws, _ = work
    connect(monkeypatch, ws, provider)

    def rejected(*args):
        raise HTTPError("https://synthetic.invalid", 403, "refused", {}, None)

    monkeypatch.setattr(provider, "api", rejected)
    ident = ws.prepare(brief(), source_directory=ws.media.project)["generation_id"]
    assert ws.operate(ident, "submit", expected_revision=1)["status"] == "rejected"
    calls = []

    def api(method, suffix, payload=None):
        calls.append(method)
        if method == "POST":
            return {"id": "cgt-synthetic-002"}
        return {"id": "cgt-synthetic-002", "model": provider.MODEL, "status": "queued"}

    monkeypatch.setattr(provider, "api", api)
    ident = ws.prepare(brief(), source_directory=ws.media.project)["generation_id"]
    ws.operate(ident, "submit", expected_revision=1)
    cancelled = ws.operate(ident, "cancel", expected_revision=1)
    assert (
        cancelled["status"] == "queued"
        and cancelled["error_code"] == "atomic_cancellation_unavailable"
    )
    assert "DELETE" not in calls


def test_reference_preprocessing_retains_both_digests_and_disallows_arbitrary_download_origins(
    work, provider
):
    ws, _ = work
    image = ws.media.project / "frame.png"
    Image.new("RGBA", (768, 432), (0, 0, 0, 0)).save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    request = {
        "plan": {"references": [{"role": "first_frame", "sha256": digest}]},
        "sources": {"first_frame": str(image)},
    }
    content, refs = provider.reference_content(request, ws.media.project)
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert refs[0]["original_sha256"] == digest and refs[0]["sent_sha256"] != digest
    with Image.open(ws.media.project / "first_frame.png") as sent:
        assert sent.mode == "RGB" and sent.getpixel((0, 0)) == (255, 255, 255)
    for url in [
        "http://example.volces.com/x",
        "https://127.0.0.1/x",
        "https://volces.com.evil.invalid/x",
        "file:///secret",
        "https://user:pass@x.volces.com/x",
    ]:
        with pytest.raises(ValueError):
            provider._asset_url(url)


def test_real_loopback_transport_rejects_foreign_goal_paths_and_mixed_file_ids(
    work, monkeypatch
):
    ws, registry = work
    ident = ws.prepare(brief(), source_directory=ws.media.project)["generation_id"]
    monkeypatch.setattr(
        generation.GenerationWorkspace,
        "_call",
        lambda *a: pytest.fail("list must not call cloud"),
    )
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root = registry, ws.media.runtime_root
    server.selected_goal_id, server.verbose = "media-demo", False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def post(body):
        client = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
        client.request(
            "POST",
            "/api/chat/content-media",
            json.dumps(body),
            {
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{server.server_address[1]}",
            },
        )
        response = client.getresponse()
        data = response.read()
        client.close()
        return response.status, json.loads(data)

    try:
        status, data = post({"goal_id": "media-demo", "action": "generation_list"})
        assert status == 200 and data["generations"][0]["generation_id"] == ident
        for body in [
            {"goal_id": "foreign", "action": "generation_list"},
            {
                "goal_id": "media-demo",
                "action": "generation_submit",
                "generation_id": ident,
                "expected_revision": True,
            },
            {
                "goal_id": "media-demo",
                "action": "generation_submit",
                "generation_id": ident,
                "expected_revision": 1,
                "path": "/secret",
            },
        ]:
            assert post(body)[0] == 400
        assert str(ws.media.project).encode() not in json.dumps(data).encode()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_collection_reuses_only_task_and_digest_bound_verified_bytes(
    work, provider, monkeypatch
):
    from types import SimpleNamespace
    import subprocess
    import sys
    import time

    ws, _ = work
    root = ws.media.project / "collection"
    output = root / "output"
    output.mkdir(parents=True)
    (output / "generated.mp4").write_bytes(b"synthetic movie bytes")
    Image.new("RGB", (1280, 720), "white").save(output / "last-frame.jpg")
    files = {
        name: {
            "sha256": provider.sha(output / name),
            "bytes": (output / name).stat().st_size,
        }
        for name in ["generated.mp4", "last-frame.jpg"]
    }
    receipt = {
        "task_id": "cgt-collection-001",
        "plan_digest": "sha256:" + "a" * 64,
        "files": files,
    }
    provider.save(root / "collection.json", receipt)
    request = {
        "plan_digest": receipt["plan_digest"],
        "plan": {"return_last_frame": True},
    }
    remote = {
        "id": receipt["task_id"],
        "content": {
            "video_url": "https://asset.volces.com/movie",
            "last_frame_url": "https://asset.volces.com/last",
        },
    }
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: "synthetic-ffmpeg"),
    )

    def decode(argv, **kwargs):
        if "-frames:v" in argv:
            Image.new("RGB", (1280, 720), "white").save(argv[-1])
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return subprocess.CompletedProcess(
            argv,
            0,
            b"frame=120\nout_time_us=5000000\n",
            b"Metadata title: Audio: text, Video: 1x1\n  Stream #0:0 Video: synthetic, 1280x720, 24 fps",
        )

    monkeypatch.setattr(provider.subprocess, "run", decode)
    downloads = []

    def download(url, destination, maximum, *, deadline):
        downloads.append(destination.name)
        if destination.suffix == ".mp4":
            destination.write_bytes(b"replacement synthetic movie")
        else:
            Image.new("RGB", (1280, 720), "white").save(destination)

    monkeypatch.setattr(provider, "download", download)
    result = provider.collect(request, remote, root, deadline=time.monotonic() + 5)
    assert downloads == [] and result["fps"] == 24 and result["has_audio"] is False
    # A changed local file cannot inherit a prior source receipt.
    (output / "generated.mp4").write_bytes(b"damaged")
    provider.collect(request, remote, root, deadline=time.monotonic() + 5)
    assert downloads == ["generated.mp4"]
    # A receipt from a different remote task cannot license either cached file.
    receipt["task_id"] = "cgt-other"
    provider.save(root / "collection.json", receipt)
    downloads.clear()
    provider.collect(request, remote, root, deadline=time.monotonic() + 5)
    assert downloads == ["generated.mp4", "last-frame.jpg"]
