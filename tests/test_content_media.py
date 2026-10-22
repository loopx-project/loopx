"""Composition admission, optional effects and real loopback file boundaries."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import threading
import time
import wave

import pytest

from loopx.capabilities.content_ops import media
from loopx.capabilities.content_ops.item_lifecycle import apply_content_ops_item_event
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.effect_runtime import EffectRuntimeRejected


def brief():
    return {
        "schema_version": "content_ops_media_plan_v0",
        "title": "Return the result",
        "width": 640,
        "height": 360,
        "fps": 12,
        "shots": [
            {
                "id": "flow",
                "kind": "diagram",
                "seconds": 3,
                "title": "A complete handoff",
                "nodes": ["Intent", "Owner", "Evidence"],
                "source_ref": "synthetic:handoff",
                "boundary": "Mechanism illustration",
            }
        ],
        "captions": [
            {
                "start": 0,
                "end": 3,
                "text": "Return evidence to the requester.",
                "language": "en",
            }
        ],
        "narration": {"mode": "silent"},
    }


@pytest.fixture
def workspace(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
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
    return media.MediaWorkspace(
        registry_path=registry, goal_id="media-demo", runtime_root=runtime
    ), registry


def test_prepare_is_replayable_and_revision_invalidates_publication_approval(workspace):
    work, _ = workspace
    first = work.prepare(brief(), source_directory=work.project)
    same = work.prepare(
        brief(),
        source_directory=work.project,
        composition_id=first["composition_id"],
        expected_revision=1,
    )
    assert same["revision"] == 1
    assert first["provider"]["status"] != "ready"
    record = work._record(first["composition_id"])
    item = record["item"]
    for action, payload in [
        ("submit_review", {}),
        (
            "approve",
            {
                "approval_ref": "decision:owner",
                "content_digest": item["content_digest"],
                "revision": item["revision"],
                "effect_kind": "publish",
            },
        ),
    ]:
        item = apply_content_ops_item_event(
            item,
            {
                "event_id": action,
                "action": action,
                "payload": payload,
                "expected_state": item["state"],
                "expected_revision": item["revision"],
                "occurred_at": media.now(),
            },
        )["item"]
    record["item"] = item
    media._save(work._bundle(first["composition_id"]) / "record.json", record)
    changed = {**brief(), "title": "Revised claim"}
    with pytest.raises(ValueError, match="revision"):
        work.prepare(
            changed,
            source_directory=work.project,
            composition_id=first["composition_id"],
            expected_revision=0,
        )
    revised = work.prepare(
        changed,
        source_directory=work.project,
        composition_id=first["composition_id"],
        expected_revision=1,
    )
    assert revised["revision"] == 2 and revised["item_state"] == "draft"
    assert work._record(first["composition_id"])["item"]["approval"] is None
    assert revised["publish_authorized"] is False


def test_assets_are_immutable_hash_bound_and_cannot_escape_scope(workspace):
    work, _ = workspace
    source = work.project / "source.bin"
    source.write_bytes(b"synthetic source")
    asset = {
        "id": "source",
        "path": source.name,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "kind": "image",
        "source_ref": "synthetic:source",
        "visibility": "local_private",
    }
    plan = {**brief(), "assets": [asset]}
    prepared = work.prepare(plan, source_directory=work.project)
    snapshot = Path(work._record(prepared["composition_id"])["sources"]["source"])
    source.write_bytes(b"later source")
    assert snapshot.read_bytes() == b"synthetic source"
    with pytest.raises(ValueError, match="Source bytes changed"):
        work.prepare(plan, source_directory=work.project)
    (work.project / "link").symlink_to(source)
    for path in ("../outside", "link"):
        with pytest.raises((ValueError, EffectRuntimeRejected)):
            work.prepare(
                {**plan, "assets": [{**asset, "path": path}]},
                source_directory=work.project,
            )
    with pytest.raises(ValueError, match="composition reference"):
        work.inspect("../../outside")


def test_unsupported_caption_refusal_does_not_create_a_prepared_composition(workspace):
    work, _ = workspace
    for text in (
        "FIRST LINE\n\nSECOND LINE",
        "FIRST LINE\n \t\nSECOND LINE",
        "x < y",
        "<b>literal</b>",
        "&lt;literal&gt;",
    ):
        plan = brief()
        plan["captions"][0]["text"] = text
        with pytest.raises(EffectRuntimeRejected, match="Caption"):
            work.prepare(plan, source_directory=work.project)
    assert work.list()["compositions"] == []


def test_missing_provider_preserves_prepared_work_without_starting_an_attempt(
    workspace,
):
    work, _ = workspace
    first = work.prepare(brief(), source_directory=work.project)
    before = work._record(first["composition_id"])
    result = work.render(first["composition_id"], expected_revision=1)
    assert result["ok"] is False and result["status"] == "provider_unavailable"
    assert work._record(first["composition_id"]) == before
    assert work.list()["compositions"][0]["status"] == "prepared"


def test_concurrent_retry_cancel_and_interruption_preserve_one_effect(
    workspace, monkeypatch
):
    work, _ = workspace

    # This controlled effect tests the store race. The real renderer is exercised
    # separately; the stub does not certify any video output.
    class Binding:
        binding = object()

        def public_readiness(self):
            return {"status": "ready"}

    monkeypatch.setattr(work, "binding", Binding)
    started, release = threading.Event(), threading.Event()
    calls = []

    def effect(binding, *, request):
        calls.append(request["attempt_directory"])
        started.set()
        assert release.wait(5)
        raise ValueError("synthetic failure")

    monkeypatch.setattr(media, "execute_extension_runtime_binding", effect)
    first = work.prepare(brief(), source_directory=work.project)
    ident = first["composition_id"]
    work.render(ident, expected_revision=1, background=True)
    assert started.wait(5)
    assert work.render(ident, expected_revision=1)["status"] == "rendering"
    assert work.cancel(ident, expected_revision=1)["status"] == "cancellation_requested"
    assert work.inspect(ident)["status"] == "rendering", (
        "cancellation is a request, not premature completion"
    )
    release.set()
    for _ in range(100):
        if work.inspect(ident)["status"] != "rendering":
            break
        time.sleep(0.02)
    assert work.inspect(ident)["status"] == "cancelled" and len(calls) == 1
    record = work._record(ident)
    record.update(
        status="rendering",
        updated_at=(datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat(),
    )
    media._save(work._bundle(ident) / "record.json", record)
    assert work.inspect(ident)["status"] == "interrupted"
    assert work._record(ident)["status"] == "rendering", (
        "inspection must not restart or mutate a render"
    )
    assert work.render(ident, expected_revision=1)["status"] == "failed"
    assert len(calls) == 2


@pytest.fixture
def http_workspace(workspace):
    work, registry = workspace
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root = registry, work.runtime_root
    server.selected_goal_id, server.verbose = "media-demo", False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def exchange(method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=10)
        connection.request(
            method,
            path,
            json.dumps(body).encode() if body is not None else None,
            headers={
                "Origin": f"http://127.0.0.1:{server.server_address[1]}",
                "Content-Type": "application/json",
                **(headers or {}),
            },
        )
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    yield work, exchange
    server.shutdown()
    server.server_close()
    thread.join(2)


def test_real_http_admission_rejects_foreign_goal_origin_and_path_injection(
    http_workspace,
):
    work, exchange = http_workspace
    first = work.prepare(brief(), source_directory=work.project)
    endpoint = "/api/chat/content-media"
    status, _, body = exchange(
        "POST", endpoint, {"goal_id": "media-demo", "action": "list"}
    )
    assert (
        status == 200
        and json.loads(body)["compositions"][0]["composition_id"]
        == first["composition_id"]
    )
    assert (
        exchange("POST", endpoint, {"goal_id": "another-goal", "action": "list"})[0]
        == 400
    )
    assert (
        exchange(
            "POST",
            endpoint,
            {"goal_id": "media-demo", "action": "list", "source_directory": "outside"},
        )[0]
        == 400
    )
    assert (
        exchange(
            "POST",
            endpoint,
            {"goal_id": "media-demo", "action": "list"},
            {"Origin": "https://unrelated.example"},
        )[0]
        == 403
    )
    assert (
        exchange(
            "GET",
            endpoint + "/file?goal_id=media-demo&composition_id=..&file=video.mp4",
        )[0]
        == 404
    )
    assert b"source_directory" not in body and str(work.project).encode() not in body


def _install_source_renderer(work):
    import subprocess
    import sys
    import loopx_media.renderer as renderer

    executable = shutil.which("loopx-media")
    assert executable, (
        "Install packages/loopx-media in an isolated environment and put its executable on PATH"
    )
    root = Path(__file__).resolve().parents[1]
    assert (
        Path(renderer.__file__).resolve()
        == root / "packages/loopx-media/src/loopx_media/renderer.py"
    ), "Run source qualification with --with-editable ./packages/loopx-media"
    assert Path(executable).parent == Path(sys.executable).parent, (
        "Use the optional provider from the qualification interpreter's environment"
    )
    command = [sys.executable, "-m", "loopx.cli", "--format", "json", "extension"]
    state = work.runtime_root / "extensions/state.json"
    installed = subprocess.run(
        [
            *command,
            "install",
            "--manifest",
            str(root / "packages/loopx-media/extension.toml"),
            "--state-file",
            str(state),
            "--execute",
        ],
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    return root, command, state


def test_spatial_body_refusal_creates_no_prepared_item(workspace):
    work, _ = workspace
    root = Path(__file__).resolve().parents[1]
    plan = json.loads(
        (root / "packages/loopx-media/examples/blender-handoff.json").read_text()
    )
    for kind in ("excerpt", "blender"):
        invalid = deepcopy(plan)
        invalid["shots"][1].update(kind=kind, body="IMPORTANT SYNTHETIC QUALIFIER")
        if kind == "excerpt":
            del invalid["shots"][1]["scene"]
            invalid["shots"][1]["asset_id"] = "still"
            invalid["assets"] = [
                {
                    "id": "still",
                    "kind": "image",
                    "path": "source.png",
                    "sha256": "a" * 64,
                    "source_ref": "synthetic:still",
                    "visibility": "local_private",
                }
            ]
        with pytest.raises(EffectRuntimeRejected, match="do not support body text"):
            work.prepare(invalid, source_directory=work.project)
    assert work.list()["compositions"] == []


@pytest.mark.skipif(
    os.environ.get("LOOPX_MEDIA_RENDERER_TEST") != "1",
    reason="explicit optional local renderer qualification",
)
def test_real_optional_renderer_reuse_and_http_byte_readback(http_workspace):
    """No model calls: use the actual extension, encoder, store and HTTP route."""
    import subprocess
    import imageio_ffmpeg
    from urllib.parse import urlencode

    work, exchange = http_workspace
    _, command, state = _install_source_renderer(work)
    first_plan = brief()
    caption_text = 'FIRST & SECOND "quoted"\nReturn evidence; preserve both lines.'
    first_plan["captions"][0]["text"] = caption_text
    first = work.prepare(first_plan, source_directory=work.project)
    ident = first["composition_id"]
    ready = work.render(ident, expected_revision=1)
    assert ready["status"] == "ready", ready
    assert ready["result"]["qa"]["frames"] == 36
    record = work._record(ident)
    before = record["attempt"]
    assert work.render(ident, expected_revision=1)["status"] == "ready"
    assert work._record(ident)["attempt"] == before
    movie = work.file(ident, "video.mp4")
    query = urlencode(
        {"goal_id": "media-demo", "composition_id": ident, "file": "video.mp4"}
    )
    status, headers, data = exchange(
        "GET", "/api/chat/content-media/file?" + query, headers={"Range": "bytes=0-31"}
    )
    assert (
        status == 206
        and data == movie.read_bytes()[:32]
        and headers["Cache-Control"] == "no-store"
    )
    assert headers["Content-Disposition"] == 'inline; filename="video.mp4"'
    # Decode both actual subtitle containers through FFmpeg, independently of
    # the writer. ASS represents the visible newline as \N; ordinary ampersands
    # and quotes must round-trip without becoming escaped display text.
    for name in ("captions.vtt", "captions.srt"):
        decoded = subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-v",
                "error",
                "-i",
                str(work.file(ident, name)),
                "-f",
                "ass",
                "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        payloads = [
            line.split(",", 9)[9]
            for line in decoded.stdout.splitlines()
            if line.startswith("Dialogue:")
        ]
        assert payloads == [caption_text.replace("\n", r"\N")]
    # A second brief reuses the installed provider with a real video source and
    # supplied, explicitly synthetic audio rather than a new rendering script.
    shutil.copyfile(movie, work.project / "source.mp4")
    with wave.open(str(work.project / "voice.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(b"\0\0" * 44100 * 3)
    assets = [
        {
            "id": ident,
            "path": name,
            "kind": kind,
            "sha256": hashlib.sha256((work.project / name).read_bytes()).hexdigest(),
            "source_ref": "synthetic:" + ident,
            "visibility": "local_private",
        }
        for ident, name, kind in [
            ("movie", "source.mp4", "video"),
            ("voice", "voice.wav", "audio"),
        ]
    ]
    second = {
        **brief(),
        "title": "Review another brief",
        "assets": assets,
        "narration": {"mode": "supplied", "asset_id": "voice"},
        "shots": [
            {
                "id": "view",
                "kind": "excerpt",
                "seconds": 3,
                "title": "Earlier synthetic render",
                "asset_id": "movie",
                "source_ref": "synthetic:movie",
                "boundary": "Synthetic excerpt; no live product run",
            }
        ],
    }
    prepared = work.prepare(second, source_directory=work.project)
    # Actual provider boundary: a stale or direct caller cannot silently lose
    # already-accepted text even if it bypasses current typed preparation.
    from loopx.extensions.runtime import execute_extension_runtime_binding

    record = work._record(prepared["composition_id"])
    checked = media._owner("plan", {"plan": record["plan"]})
    invalid = deepcopy(record["plan"])
    invalid["shots"][0]["body"] = "IMPORTANT SYNTHETIC QUALIFIER"
    refused = work.project / "refused-body"
    with pytest.raises(RuntimeError):
        execute_extension_runtime_binding(
            work.binding().binding,
            request={
                "protocol": media.PROTOCOL,
                "plan": invalid,
                "layout_style": checked["layout_style"],
                "plan_digest": record["plan_digest"],
                "frame_count": record["frame_count"],
                "sources": record["sources"],
                "attempt_directory": str(refused),
                "cancel_file": str(work.project / "not-cancelled"),
            },
        )
    assert not refused.exists()
    rendered = work.render(prepared["composition_id"], expected_revision=1)
    assert (
        rendered["status"] == "ready"
        and rendered["result"]["qa"]["narration"]["mode"] == "supplied"
    )
    assert rendered["result"]["qa"]["narration"]["peak_dbfs"] is None
    # Failed decode never produces a ready movie; a repaired revision recovers.
    broken = deepcopy(second)
    broken["shots"][0]["start_seconds"] = 60
    revised = work.prepare(
        broken,
        source_directory=work.project,
        composition_id=prepared["composition_id"],
        expected_revision=1,
    )
    assert (
        work.render(revised["composition_id"], expected_revision=2)["status"]
        == "failed"
    )
    fixed = work.prepare(
        second,
        source_directory=work.project,
        composition_id=revised["composition_id"],
        expected_revision=2,
    )
    assert (
        work.render(fixed["composition_id"], expected_revision=3)["status"] == "ready"
    )
    # A damaged artifact cannot be downloaded or silently treated as ready.
    movie.write_bytes(b"damaged")
    assert work.inspect(ident)["status"] == "damaged"
    assert exchange("GET", "/api/chat/content-media/file?" + query)[0] == 404
    disabled = subprocess.run(
        [*command, "disable", "loopx-media", "--state-file", str(state), "--execute"],
        capture_output=True,
        text=True,
    )
    assert disabled.returncode == 0
    assert work.render(ident, expected_revision=1)["status"] == "provider_unavailable"


@pytest.mark.skipif(
    os.environ.get("LOOPX_MEDIA_BLENDER_TEST") != "1",
    reason="explicit optional Blender source qualification",
)
def test_real_blender_project_passes_reopen_and_http_download(http_workspace):
    import io
    import subprocess
    import sys
    import zipfile
    from urllib.parse import urlencode
    from PIL import Image, ImageChops

    work, exchange = http_workspace
    root, _, _ = _install_source_renderer(work)
    plan = json.loads(
        (root / "packages/loopx-media/examples/blender-handoff.json").read_text()
    )
    plan["shots"][1]["framing"] = "full_frame"
    prepared = work.prepare(plan, source_directory=work.project)
    ident = prepared["composition_id"]
    ready = work.render(ident, expected_revision=1)
    assert ready["status"] == "ready", ready
    result = ready["result"]
    assert result["qa"]["frames"] == 60
    assert result["qa"]["full_frame_shots"] == ["route"]
    spatial = result["qa"]["blender_scenes"][0]
    assert spatial["id"] == "route" and spatial["frames"] == 36
    assert spatial["rgba_frames"] == spatial["depth_frames"] == 36
    assert spatial["project_reopened"] is True
    query = urlencode(
        {
            "goal_id": "media-demo",
            "composition_id": ident,
            "file": "blender-project.zip",
        }
    )
    status, headers, data = exchange("GET", "/api/chat/content-media/file?" + query)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert headers["Content-Type"] == "application/zip"
    assert (
        hashlib.sha256(data).hexdigest()
        == result["files"]["blender-project.zip"]["sha256"]
    )
    directory = work.project / "independent"
    directory.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        assert all(
            not Path(name).is_absolute() and ".." not in Path(name).parts
            for name in names
        )
        assert {
            "scene-builder.py",
            "plan.json",
            "route.json",
            "route/scene.blend",
        } <= set(names)
        for kind in ("rgba", "depth"):
            assert (
                len([name for name in names if name.startswith(f"route/{kind}/")]) == 36
            )
        (directory / "scene.blend").write_bytes(archive.read("route/scene.blend"))
        with Image.open(
            io.BytesIO(archive.read("route/rgba/frame-0019.png"))
        ) as original:
            original.load()
            expected = original.copy()
            assert original.mode == "RGBA" and original.getchannel(
                "A"
            ).getextrema() == (0, 255)
        with Image.open(
            io.BytesIO(archive.read("route/depth/frame-0019.png"))
        ) as depth:
            assert (
                depth.size == (640, 360)
                and depth.getextrema()[0] < depth.getextrema()[1]
            )
    # Independent consumer: open only the persisted blend, inspect real bpy
    # state and re-render the middle frame without using the provider builder.
    verified = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import bpy, json, pathlib, sys
p = pathlib.Path(sys.argv[1])
bpy.ops.wm.open_mainfile(filepath=str(p / 'scene.blend'), load_ui=False, use_scripts=False)
s = bpy.context.scene
assert s.render.engine == 'CYCLES' and s.render.fps == 12
assert s.frame_start == 1 and s.frame_end == 36
assert s.view_layers[0].use_pass_z and s.render.film_transparent
assert len([o for o in s.objects if o.type == 'LIGHT']) == 2
s.frame_set(1)
assert abs(s.objects['artifact'].location.x + 2) < 0.0001
camera_start = tuple(s.camera.location)
s.frame_set(19)
assert abs(s.objects['artifact'].location.x - 2) < 0.0001
s.frame_set(36)
assert abs(s.objects['artifact'].location.x + 2) < 0.0001
assert tuple(s.camera.location) != camera_start
s.frame_set(19)
s.render.filepath = str(p / 'middle.png')
for node in s.node_tree.nodes:
    if node.type == 'OUTPUT_FILE':
        node.base_path = str(p / 'depth')
bpy.ops.render.render(write_still=True)
(p / 'verified.json').write_text(json.dumps({'version': bpy.app.version_string, 'frame': s.frame_current}))
""",
            str(directory),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert verified.returncode == 0, verified.stderr[-2000:]
    with Image.open(directory / "middle.png") as reopened:
        assert ImageChops.difference(
            expected, reopened.convert("RGBA")
        ).getextrema() == ((0, 0), (0, 0), (0, 0), (0, 0))
    assert json.loads((directory / "verified.json").read_text())["frame"] == 19
    # Reuse is stateful and exact; it does not re-encode the same 3D scene.
    attempt = work._record(ident)["attempt"]
    assert work.render(ident, expected_revision=1)["status"] == "ready"
    assert work._record(ident)["attempt"] == attempt


@pytest.mark.skipif(
    os.environ.get("LOOPX_MEDIA_RENDERER_TEST") != "1",
    reason="explicit optional local renderer qualification",
)
@pytest.mark.parametrize("kind", ["image", "video"])
def test_full_frame_retains_source_edges_and_boundary_through_real_renderer(http_workspace, kind):
    """A four-corner source falsifies silent crop, old thumbnail and overlay loss."""
    import subprocess
    import imageio_ffmpeg
    from PIL import Image, ImageDraw

    work, exchange = http_workspace
    _install_source_renderer(work)
    source = Image.new("RGB", (1200, 600), "#203050")
    draw = ImageDraw.Draw(source)
    corners = [(0, 0, 99, 99), (1100, 0, 1199, 99), (0, 500, 99, 599), (1100, 500, 1199, 599)]
    colors = [(220, 30, 30), (30, 220, 30), (30, 30, 220), (220, 190, 30)]
    for box, color in zip(corners, colors):
        draw.rectangle(box, fill=color)
    source.save(work.project / "source.png")
    name = "source.png"
    if kind == "video":
        name = "source.mp4"
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-loop", "1", "-i", str(work.project / "source.png"),
                        "-t", "3", "-r", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(work.project / name)], check=True)
    plan = {**brief(), "assets": [{"id": "picture", "path": name, "kind": kind,
            "sha256": hashlib.sha256((work.project / name).read_bytes()).hexdigest(),
            "source_ref": "synthetic:four-corner-source", "visibility": "public_safe"}],
            "shots": [{"id": "view", "kind": "excerpt", "seconds": 3, "title": "Title remains in the timeline",
                       "asset_id": "picture", "source_ref": "synthetic:four-corner-source", "boundary": "Synthetic source"}]}
    original = work.prepare(plan, source_directory=work.project)
    ident = original["composition_id"]
    legacy = work.render(ident, expected_revision=1)
    assert legacy["status"] == "ready"
    with Image.open(work.file(ident, "poster.png")) as image:
        legacy_pixels = list(image.convert("RGB").getdata())
    plan["shots"][0]["framing"] = "full_frame"
    changed = work.prepare(plan, source_directory=work.project, composition_id=ident, expected_revision=1)
    assert changed["revision"] == 2 and changed["status"] == "prepared"
    assert changed["shots"][0]["framing"] == "full_frame"
    with pytest.raises(ValueError, match="revision"):
        work.render(ident, expected_revision=1)
    framed = work.render(ident, expected_revision=2)
    assert framed["status"] == "ready", framed
    assert framed["result"]["qa"]["full_frame_shots"] == ["view"]
    with Image.open(work.file(ident, "poster.png")) as image:
        picture = image.convert("RGB")
        pixels = list(picture.getdata())
    def close(pixel, color):
        return max(abs(a - b) for a, b in zip(pixel, color)) < 20
    for color in colors:
        # Each 100px source corner should occupy more area when the inset is removed.
        assert sum(close(p, color) for p in pixels) > 2 * sum(close(p, color) for p in legacy_pixels)
    # Full-width source corners remain at the correct sides, including both bottom edges.
    for color, right, lower in zip(colors, [False, True, False, True], [False, False, True, True]):
        points = [(i % 640, i // 640) for i, p in enumerate(pixels) if close(p, color)]
        assert points and (min(x for x, _ in points) > 570 if right else max(x for x, _ in points) < 70)
        assert (min(y for _, y in points) > 250 if lower else max(y for _, y in points) < 70)
    # The attribution strip is outside the source, with visible dark text; no crop/overlay.
    assert any(max(p) < 100 for p in pixels[640 * 330:])
    from urllib.parse import urlencode
    query = urlencode({"goal_id": "media-demo", "composition_id": ident, "file": "video.mp4"})
    status, _, movie = exchange("GET", "/api/chat/content-media/file?" + query)
    assert status == 200 and hashlib.sha256(movie).hexdigest() == framed["result"]["files"]["video.mp4"]["sha256"]


@pytest.mark.skipif(
    os.environ.get("LOOPX_MEDIA_RENDERER_TEST") != "1",
    reason="explicit optional local renderer qualification",
)
def test_full_frame_video_preserves_display_aspect_before_raw_pixel_conversion(http_workspace):
    import subprocess
    import imageio_ffmpeg
    from PIL import Image

    work, _ = http_workspace
    _install_source_renderer(work)
    source = work.project / "anamorphic.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i",
                    "color=c=0x203050:s=320x160:r=12:d=1", "-vf", "setsar=2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(source)], check=True)
    plan = {**brief(), "captions": [], "assets": [{"id": "movie", "path": source.name, "kind": "video",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "source_ref": "synthetic:anamorphic",
            "visibility": "public_safe"}], "shots": [{"id": "view", "kind": "excerpt", "seconds": 1,
            "title": "Anamorphic source", "asset_id": "movie", "framing": "full_frame",
            "source_ref": "synthetic:anamorphic", "boundary": "Synthetic source"}]}
    prepared = work.prepare(plan, source_directory=work.project)
    ready = work.render(prepared["composition_id"], expected_revision=1)
    assert ready["status"] == "ready", ready
    with Image.open(work.file(prepared["composition_id"], "poster.png")) as image:
        rows = [y for y in range(360) if max(abs(a-b) for a,b in zip(image.getpixel((320,y)), (32,48,80))) < 8]
    # 320x160 with SAR=2 displays at 4:1: a full 640px width needs 160px height.
    assert 158 <= len(rows) <= 162
