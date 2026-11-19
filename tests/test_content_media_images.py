"""Synchronous image boundaries with synthetic bytes; never a paid CI call."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import http.client
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import threading

from PIL import Image
import pytest

from loopx.capabilities.content_ops.images import ImageWorkspace
from loopx.capabilities.content_ops.media import MediaWorkspace
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler


@pytest.fixture
def work(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, registry = tmp_path / "runtime", tmp_path / "registry.json"
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
    return ImageWorkspace(
        MediaWorkspace(
            registry_path=registry, goal_id="media-demo", runtime_root=runtime
        )
    ), registry


@pytest.fixture
def provider(monkeypatch):
    path = (
        Path(__file__).resolve().parents[1]
        / "packages/loopx-media/src/loopx_media/images.py"
    )
    monkeypatch.syspath_prepend(str(path.parents[1]))
    spec = importlib.util.spec_from_file_location("loopx_media.images", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def brief():
    return {
        "schema_version": "content_ops_image_plan_v0",
        "title": "A returned artifact",
        "model": "doubao-seedream-5-0-pro-260628",
        "prompt": "Three fixed stations. One artifact returns. No text.",
        "width": 1280,
        "height": 720,
        "references": [],
        "watermark": True,
        "quote": {
            "cny_per_image": 0.3,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "source_url": "https://ark.volcengine.com/docs/model-pricing",
            "max_estimated_cny": 0.4,
        },
    }


def connect(monkeypatch, work, provider):
    ws, _ = work
    monkeypatch.setattr(
        ImageWorkspace,
        "_call",
        lambda self, record, action: provider.operate(
            {
                "protocol": "content_ops_media_renderer_v0",
                "operation": "image." + action,
                "plan": record["plan"],
                "plan_digest": record["plan_digest"],
                "sources": record["sources"],
                "directory": str(self._bundle(record["image_id"])),
            }
        ),
    )
    monkeypatch.setenv("ARK_IMAGE_API_KEY", "synthetic-not-a-real-credential")
    data = BytesIO()
    Image.new("RGB", (1280, 720), "white").save(data, format="PNG")
    calls = []

    def api(payload, *_):
        calls.append(payload)
        return {
            "model": provider.MODEL,
            "created": 1,
            "data": [
                {
                    "b64_json": base64.b64encode(data.getvalue()).decode(),
                    "size": "1280x720",
                }
            ],
            "usage": {"generated_images": 1, "output_tokens": 3600},
        }

    monkeypatch.setattr(provider, "api", api)
    return ws, calls


def test_image_immutable_source_snapshot_is_scoped_and_reads_make_no_cloud_call(
    work, monkeypatch
):
    ws, _ = work
    source = ws.media.project / "references"
    source.mkdir()
    image = source / "ref.png"
    Image.new("RGBA", (1280, 720), (0, 0, 0, 0)).save(image)
    ref = {
        "path": "ref.png",
        "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "source_ref": "synthetic:review-return",
        "visibility": "public_safe",
    }
    plan = {**brief(), "references": [ref]}
    monkeypatch.setattr(
        ImageWorkspace, "_call", lambda *a: pytest.fail("read must not call cloud")
    )
    item = ws.prepare(plan, source_directory=source)
    ident = item["image_id"]
    assert "path" not in item["references"][0]
    assert ws.list()["images"][0]["status"] == "prepared"
    with pytest.raises(ValueError):
        ws.prepare(
            {**plan, "prompt": "different meaning"},
            source_directory=source,
            image_id=ident,
        )
    image.write_bytes(b"changed")
    with pytest.raises(ValueError):
        ws.prepare(plan, source_directory=source)


def test_image_credentials_are_separate_from_video_and_unavailable_does_not_submit(
    work, provider, monkeypatch
):
    ws, calls = connect(monkeypatch, work, provider)
    monkeypatch.delenv("ARK_IMAGE_API_KEY")
    monkeypatch.setenv("ARK_API_KEY", "synthetic-video-only-key")
    item = ws.prepare(brief(), source_directory=ws.media.project)
    result = ws.operate(item["image_id"], "submit", expected_revision=1)
    assert not result["ok"] and result["status"] == "prepared"
    assert (
        calls == []
        and not (ws._bundle(item["image_id"]) / "image-submission.json").exists()
    )


def test_image_submit_once_retains_pixels_and_collects_damaged_local_output_without_new_post(
    work, provider, monkeypatch
):
    ws, calls = connect(monkeypatch, work, provider)
    item = ws.prepare(brief(), source_directory=ws.media.project)
    ident = item["image_id"]
    ready = ws.operate(ident, "submit", expected_revision=1)
    assert (
        ready["ok"]
        and ready["status"] == "ready"
        and ready["result"]["usage"]["generated_images"] == 1
    )
    assert (
        calls[0]["layer_decomposition"] is False and calls[0]["output_format"] == "png"
    )
    assert "stream" not in calls[0] and "sequential_image_generation" not in calls[0]
    assert (
        ws.operate(ident, "submit", expected_revision=1)["status"] == "ready"
        and len(calls) == 1
    )
    original = ws.file(ident, "original.png").read_bytes()
    ws.file(ident, "generated.png").unlink()
    assert ws.inspect(ident)["status"] == "damaged"
    assert (
        ws.operate(ident, "collect", expected_revision=1)["status"] == "ready"
        and len(calls) == 1
    )
    assert ws.file(ident, "original.png").read_bytes() == original
    with Image.open(ws.file(ident, "generated.png")) as image:
        image.load()
        assert image.size == (1280, 720)


def test_windows_image_journal_retains_cached_pixels_and_no_duplicate_post(
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
    ws, calls = connect(monkeypatch, work, provider)
    ident = ws.prepare(brief(), source_directory=ws.media.project)["image_id"]
    assert ws.operate(ident, "submit", expected_revision=1)["status"] == "ready"
    original = ws.file(ident, "original.png").read_bytes()
    saved = json.loads((ws._bundle(ident) / "image-submission.json").read_text())
    assert saved["plan_digest"] == ws.inspect(ident)["plan_digest"]
    ws.operate(ident, "submit", expected_revision=1)
    ws.file(ident, "generated.png").unlink()
    assert ws.operate(ident, "collect", expected_revision=1)["status"] == "ready"
    assert ws.file(ident, "original.png").read_bytes() == original
    assert len(calls) == 1 and len(synced) >= 2


def test_lost_provider_stdout_recovers_only_saved_response_and_ambiguous_post_never_repeats(
    work, provider, monkeypatch
):
    ws, calls = connect(monkeypatch, work, provider)
    call = ImageWorkspace._call

    def lost(self, record, action):
        result = call(self, record, action)
        if action == "submit":
            raise RuntimeError("caller lost after response saved")
        return result

    monkeypatch.setattr(ImageWorkspace, "_call", lost)
    ident = ws.prepare(brief(), source_directory=ws.media.project)["image_id"]
    assert (
        ws.operate(ident, "submit", expected_revision=1)["status"]
        == "submission_unknown"
    )
    assert (
        ws.operate(ident, "submit", expected_revision=1)["status"]
        == "submission_unknown"
        and len(calls) == 1
    )
    assert (
        ws.operate(ident, "collect", expected_revision=1)["status"] == "ready"
        and len(calls) == 1
    )
    monkeypatch.setattr(ImageWorkspace, "_call", call)

    def timeout(payload, *_):
        calls.append(payload)
        raise TimeoutError("unknown remote effect")

    monkeypatch.setattr(provider, "api", timeout)
    other = ws.prepare(brief(), source_directory=ws.media.project)["image_id"]
    assert (
        ws.operate(other, "submit", expected_revision=1)["status"]
        == "submission_unknown"
    )
    assert not ws.operate(other, "collect", expected_revision=1)["ok"]
    assert (
        ws.operate(other, "submit", expected_revision=1)["status"]
        == "submission_unknown"
        and len(calls) == 2
    )


@pytest.mark.parametrize(
    "malformed",
    [
        {"data": None},
        {"data": "unexpected"},
        {"data": [None]},
        {"data": [{}], "usage": []},
        {"data": [{}], "usage": {"generated_images": True}},
        {"data": [{"b64_json": "invalid"}], "usage": {"generated_images": 1}},
    ],
)
def test_malformed_paid_response_stays_unknown_and_cannot_trigger_a_second_post(
    malformed, work, provider, monkeypatch
):
    ws, calls = connect(monkeypatch, work, provider)

    def malformed_api(payload, *_):
        calls.append(payload)
        return {"model": provider.MODEL, **malformed}

    monkeypatch.setattr(provider, "api", malformed_api)
    ident = ws.prepare(brief(), source_directory=ws.media.project)["image_id"]
    result = ws.operate(ident, "submit", expected_revision=1)
    assert result["status"] == "submission_unknown"
    journal = json.loads((ws._bundle(ident) / "image-submission.json").read_text())
    assert journal["state"] == "submission_unknown"
    assert not ws.operate(ident, "collect", expected_revision=1)["ok"]
    assert (
        ws.operate(ident, "submit", expected_revision=1)["status"]
        == "submission_unknown"
    )
    assert len(calls) == 1


@pytest.mark.parametrize("invalid_json", [False, True])
def test_exact_http_response_survives_interpretation_failure_without_a_second_post(
    invalid_json, work, provider, monkeypatch
):
    real_api = provider.api
    ws, _ = connect(monkeypatch, work, provider)
    pixels = BytesIO()
    Image.new("RGB", (1280, 720), "white").save(pixels, format="PNG")
    body = (
        b"not JSON"
        if invalid_json
        else json.dumps(
            {
                "model": provider.MODEL,
                "data": [{"b64_json": base64.b64encode(pixels.getvalue()).decode()}],
                "usage": {"generated_images": 1, "output_tokens": 3600},
            }
        ).encode()
    )
    opened = []

    class Response(BytesIO):
        status = 200
        headers = {"x-tt-logid": "synthetic-request-id"}

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == provider.BASE_URL + "/images/generations"
            assert request.method == "POST" and timeout <= 70
            opened.append(request.full_url)
            return Response(body)

    monkeypatch.setattr(provider, "build_opener", lambda *_: Opener())
    monkeypatch.setattr(provider, "api", real_api)
    cache_response = provider.cache_image_response

    def interrupted(*_):
        raise OSError("interrupted after raw response persistence")

    monkeypatch.setattr(provider, "cache_image_response", interrupted)
    ident = ws.prepare(brief(), source_directory=ws.media.project)["image_id"]
    assert (
        ws.operate(ident, "submit", expected_revision=1)["status"]
        == "submission_unknown"
    )
    root = ws._bundle(ident)
    assert (root / "image-api-response.body").read_bytes() == body
    metadata = json.loads((root / "image-http-response.json").read_text())
    assert metadata["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert metadata["request_id"] == "synthetic-request-id"
    assert metadata["plan_digest"] == ws.inspect(ident)["plan_digest"]
    assert not (root / "image-response.json").exists()
    monkeypatch.setattr(provider, "cache_image_response", cache_response)
    result = ws.operate(ident, "collect", expected_revision=1)
    assert result["status"] == ("submission_unknown" if invalid_json else "ready")
    assert len(opened) == 1
    if not invalid_json:
        assert ws.file(ident, "original.png").read_bytes() == pixels.getvalue()


def test_transport_diagnostic_keeps_error_classes_without_echoing_secrets(
    work, provider, monkeypatch
):
    from urllib.error import URLError

    real_api = provider.api
    ws, _ = connect(monkeypatch, work, provider)

    class Opener:
        def open(self, *_args, **_kwargs):
            raise URLError(OSError("synthetic-not-a-real-credential"))

    monkeypatch.setattr(provider, "api", real_api)
    monkeypatch.setattr(provider, "build_opener", lambda *_: Opener())
    ident = ws.prepare(brief(), source_directory=ws.media.project)["image_id"]
    assert (
        ws.operate(ident, "submit", expected_revision=1)["status"]
        == "submission_unknown"
    )
    text = (ws._bundle(ident) / "image-submission.json").read_text()
    journal = json.loads(text)
    assert journal["error_type"] == "URLError" and journal["reason_type"] == "OSError"
    assert "synthetic-not-a-real-credential" not in text


def test_real_image_loopback_rejects_foreign_goal_revision_and_mixed_file_identity(
    work, provider, monkeypatch
):
    ws, calls = connect(monkeypatch, work, provider)
    ident = ws.prepare(brief(), source_directory=ws.media.project)["image_id"]
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root = work[1], ws.media.runtime_root
    server.selected_goal_id, server.verbose = "media-demo", False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def post(body):
        client = http.client.HTTPConnection(*server.server_address)
        client.request(
            "POST",
            "/api/chat/content-media",
            json.dumps(body),
            {"Content-Type": "application/json"},
        )
        result = client.getresponse()
        data = json.loads(result.read())
        client.close()
        return result.status, data

    try:
        assert (
            post({"goal_id": "media-demo", "action": "image_list"})[1]["images"][0][
                "image_id"
            ]
            == ident
        )
        base = {
            "goal_id": "media-demo",
            "action": "image_submit",
            "image_id": ident,
            "expected_revision": 1,
        }
        for change in [
            {"goal_id": "foreign"},
            {"expected_revision": True},
            {"path": "private"},
            {"generation_id": "other"},
        ]:
            assert post({**base, **change})[0] == 400
        code, result = post(base)
        assert code == 200 and result["status"] == "ready" and len(calls) == 1
        client = http.client.HTTPConnection(*server.server_address)
        client.request(
            "GET",
            f"/api/chat/content-media/file?goal_id=media-demo&image_id={ident}&file=generated.png",
            headers={"Range": "bytes=0-7"},
        )
        response = client.getresponse()
        assert response.status == 206 and response.read() == b"\x89PNG\r\n\x1a\n"
        client.close()
        # Saved artifact links bind both the prepared version and exact bytes.
        # These are HTTP reads, including partial video/image previews, not
        # another generation or an acceptance decision.
        from urllib.parse import urlencode

        original = ws.file(ident, "generated.png").read_bytes()
        reference = {
            "goal_id": "media-demo", "image_id": ident, "file": "generated.png",
            "revision": "1", "sha256": hashlib.sha256(original).hexdigest(),
        }

        def get_reference(values, suffix=""):
            client = http.client.HTTPConnection(*server.server_address)
            client.request("GET", "/api/chat/content-media/file?" + urlencode(values) + suffix,
                           headers={"Range": "bytes=0-7"})
            response = client.getresponse()
            answer = response.status, response.read()
            client.close()
            return answer

        assert get_reference(reference) == (206, original[:8])
        for changed in [
            {**reference, "revision": "2"},
            {**reference, "revision": "0"},
            {**reference, "revision": "true"},
            {**reference, "sha256": "0" * 64},
            {key: value for key, value in reference.items() if key != "sha256"},
            {key: value for key, value in reference.items() if key != "revision"},
            {**reference, "generation_id": "other"},
            {**reference, "goal_id": "foreign"},
        ]:
            assert get_reference(changed)[0] == 404
        assert get_reference(reference, "&revision=1")[0] == 404
        assert get_reference(reference, "&revision=")[0] == 404
        assert get_reference(reference, "&sha256=")[0] == 404
        assert len(calls) == 1
        replacement = ws.prepare({**brief(), "title": "A changed brief"}, source_directory=ws.media.project)
        assert replacement["image_id"] != ident
        assert get_reference(reference) == (206, original[:8]), "Immutable old image remains readable"
        assert len(calls) == 1, "Preparing/reading a replacement must not resubmit generation"
        assert str(ws.media.project) not in json.dumps(result)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
