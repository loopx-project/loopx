"""Ark video transport.

Each call does one bounded operation. Paid POST is never retried. The durable
submission journal allows a stopped caller to recover a known task id; an
ambiguous request without an id remains unknown and requires reconciliation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener

from PIL import Image

from .journal import save

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
MODEL = "doubao-seedance-2-5-260628"
REMOTE_STATES = {"queued", "running", "succeeded", "failed", "cancelled", "expired"}


def sha(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected API redirect")


def api(method: str, suffix: str, payload: dict | None = None) -> dict:
    if os.environ.get("ARK_BASE_URL", BASE_URL).rstrip("/") != BASE_URL:
        raise ValueError("Unsupported Ark endpoint")
    key = os.environ.get("ARK_API_KEY")
    if not key:
        raise ValueError("Cloud credential unavailable")
    request = Request(
        BASE_URL + "/contents/generations/tasks" + suffix,
        data=None if payload is None else json.dumps(payload).encode(),
        method=method,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with build_opener(NoRedirect).open(request, timeout=20) as response:
        body = response.read(256 * 1024 + 1)
    if len(body) > 256 * 1024:
        raise ValueError("Cloud response exceeds the read budget")
    value = json.loads(body) if body else {}
    if not isinstance(value, dict):
        raise ValueError("Cloud response is not an object")
    return value


def task_id(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"cgt-[a-zA-Z0-9-]{1,90}", value):
        raise ValueError("Invalid cloud task reference")
    return value


def read_observation(value: dict, ident: str) -> dict:
    if (
        task_id(value.get("id")) != ident
        or value.get("model") != MODEL
        or value.get("status") not in REMOTE_STATES
    ):
        raise ValueError("Cloud task differs from the selected intent")
    return {
        "task_id": ident,
        "model": MODEL,
        "status": value["status"],
        "completion_tokens": (value.get("usage") or {}).get("completion_tokens"),
        "duration_seconds": value.get("duration"),
        "created_at": value.get("created_at"),
        "updated_at": value.get("updated_at"),
    }


def reference_content(request: dict, root: Path) -> tuple[list[dict], list[dict]]:
    content, provenance = [], []
    for ref in request["plan"]["references"]:
        path = Path(request["sources"][ref["role"]])
        if (
            path.is_symlink()
            or path.stat().st_size > 2 * 1024 * 1024
            or sha(path) != ref["sha256"]
        ):
            raise ValueError("Reference snapshot changed")
        with Image.open(path) as original:
            width, height = original.size
            if (
                original.format not in {"PNG", "JPEG"}
                or not (300 <= width <= 1920 and 300 <= height <= 1080)
                or abs(width / height - 16 / 9) > 0.01
            ):
                raise ValueError("This bounded provider requires 16:9 reference frames")
            original.load()
            # Deterministic, disclosed preprocessing. No crop or generated text.
            rgba = original.convert("RGBA")
            flat = Image.new("RGBA", rgba.size, "white")
            flat.alpha_composite(rgba)
            encoded = root / (ref["role"] + ".png")
            flat.convert("RGB").save(encoded, format="PNG")
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,"
                    + base64.b64encode(encoded.read_bytes()).decode("ascii")
                },
                "role": ref["role"],
            }
        )
        provenance.append(
            {
                "role": ref["role"],
                "original_sha256": ref["sha256"],
                "sent_sha256": sha(encoded),
                "preprocessing": "alpha_composite_white_rgb_png",
                "width": width,
                "height": height,
            }
        )
    return content, provenance


def _asset_url(value: str) -> str:
    parsed = urlparse(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or not any(
            host.endswith(suffix) for suffix in (".volces.com", ".volcengine.com")
        )
    ):
        raise ValueError("Unsupported cloud artifact origin")
    return value


def download(url: str, destination: Path, maximum: int, *, deadline: float) -> None:
    class ArtifactRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return super().redirect_request(
                req, fp, code, msg, headers, _asset_url(newurl)
            )

    temp = destination.with_suffix(destination.suffix + ".partial")
    started = time.monotonic()
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Collection deadline reached")
        with (
            build_opener(ArtifactRedirect).open(
                _asset_url(url), timeout=min(15, remaining)
            ) as response,
            temp.open("wb") as output,
        ):
            total = 0
            while True:
                data = response.read(65536)
                if not data:
                    break
                total += len(data)
                if (
                    total > maximum
                    or time.monotonic() - started > 45
                    or time.monotonic() >= deadline
                ):
                    raise ValueError("Cloud asset exceeds the collection budget")
                output.write(data)
            output.flush()
            os.fsync(output.fileno())
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def collect(request: dict, remote: dict, root: Path, *, deadline: float) -> dict:
    import imageio_ffmpeg

    output = root / "output"
    if output.is_symlink():
        raise ValueError("Invalid footage output directory")
    output.mkdir(mode=0o700, exist_ok=True)
    movie = output / "generated.mp4"
    previous = {}
    receipt = root / "collection.json"
    if (
        receipt.is_file()
        and not receipt.is_symlink()
        and receipt.stat().st_size <= 256 * 1024
    ):
        previous = json.loads(receipt.read_text(encoding="utf-8"))

    def asset(name: str, url: str, maximum: int) -> None:
        path = output / name
        entry = previous.get("files", {}).get(name, {})
        reusable = (
            previous.get("task_id") == remote["id"]
            and previous.get("plan_digest") == request["plan_digest"]
        )
        if (
            reusable
            and path.is_file()
            and not path.is_symlink()
            and path.stat().st_size == entry.get("bytes")
            and sha(path) == entry.get("sha256")
        ):
            return
        download(url, path, maximum, deadline=deadline)

    # Read the current URL from GET each time; URLs expire and are never the
    # durable artifact or included in the caller-visible projection.
    asset("generated.mp4", remote["content"]["video_url"], 64 * 1024 * 1024)
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    info = subprocess.run(
        [
            ff,
            "-v",
            "info",
            "-nostats",
            "-progress",
            "pipe:1",
            "-i",
            str(movie),
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=max(0.1, min(20, deadline - time.monotonic())),
        check=True,
    )
    frames = re.findall(rb"^frame=(\d+)$", info.stdout, re.M)
    micros = re.findall(rb"^out_time_us=(\d+)$", info.stdout, re.M)
    if not frames or not micros or int(frames[-1]) < 1:
        raise ValueError("Generated video did not fully decode")
    seconds = int(micros[-1]) / 1e6
    streams = [
        line
        for line in info.stderr.splitlines()
        if re.match(rb"\s*Stream #\d+:\d+", line)
    ]
    video_stream = next((line for line in streams if b"Video:" in line), b"")
    dims = re.search(rb"Video:.*? (\d{3,4})x(\d{3,4})", video_stream)
    fps = re.search(rb"Video:.*? (\d+(?:\.\d+)?) fps", video_stream)
    if (
        not fps
        or not dims
        or not (300 <= int(dims[1]) <= 1920 and 300 <= int(dims[2]) <= 1080)
    ):
        raise ValueError("Generated video has unsupported dimensions")
    poster = output / "poster.png"
    subprocess.run(
        [ff, "-v", "error", "-y", "-i", str(movie), "-frames:v", "1", str(poster)],
        capture_output=True,
        check=True,
        timeout=max(0.1, min(10, deadline - time.monotonic())),
    )
    files = {
        name: {"sha256": sha(output / name), "bytes": (output / name).stat().st_size}
        for name in ("generated.mp4", "poster.png")
    }
    last = (remote.get("content") or {}).get("last_frame_url")
    if request["plan"]["return_last_frame"] and last:
        path = output / "last-frame.jpg"
        asset("last-frame.jpg", last, 4 * 1024 * 1024)
        with Image.open(path) as image:
            image.verify()
        files[path.name] = {"sha256": sha(path), "bytes": path.stat().st_size}
    result = {
        "task_id": remote["id"],
        "plan_digest": request["plan_digest"],
        "files": files,
        "full_decode": "passed",
        "frames": int(frames[-1]),
        "measured_seconds": seconds,
        "width": int(dims[1]),
        "height": int(dims[2]),
        "fps": float(fps[1]),
        "has_audio": any(b"Audio:" in line for line in streams),
    }
    save(root / "collection.json", result)
    return result


def operate(request: dict) -> dict:
    deadline = time.monotonic() + 100
    if request.get("protocol") != "content_ops_media_renderer_v0":
        raise ValueError("Unsupported footage protocol")
    operation = request["operation"]
    if operation == "generation.probe":
        return {
            "ok": True,
            "credential_available": bool(os.environ.get("ARK_API_KEY"))
            and os.environ.get("ARK_BASE_URL", BASE_URL).rstrip("/") == BASE_URL,
        }
    root = Path(request["directory"])
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("Invalid footage directory")
    if request["plan"]["model"] != MODEL:
        raise ValueError("Unsupported video model")
    journal = root / "submission.json"
    if operation == "generation.submit":
        if journal.exists():
            raise ValueError(
                "Submission journal already exists; check the existing task"
            )
        try:
            frames, provenance = reference_content(request, root)
        except (OSError, ValueError):
            return {"ok": True, "status": "rejected", "error_code": "reference_invalid"}
        plan = request["plan"]
        payload = {
            "model": MODEL,
            "content": [{"type": "text", "text": plan["prompt"]}, *frames],
            "duration": plan["seconds"],
            "resolution": plan["resolution"],
            "ratio": "adaptive" if frames else "16:9",
            "watermark": plan["watermark"],
            "generate_audio": plan["generate_audio"],
            "return_last_frame": plan["return_last_frame"],
            "execution_expires_after": 3600,
            "output_format": "mp4",
            "draft": False,
        }
        # Persist sending before POST. Exclude data URLs and credentials.
        saved = {
            "plan_digest": request["plan_digest"],
            "state": "sending",
            "task_id": None,
            "references": provenance,
            "parameters": {k: v for k, v in payload.items() if k != "content"},
            "request_started_at": time.time(),
        }
        save(journal, saved)
        try:
            remote = api("POST", "", payload)
            ident = task_id(remote.get("id"))
        except HTTPError as error:
            # 5xx and request timeouts are ambiguous. No blind paid retry.
            rejected = error.code in {400, 401, 403, 404, 413, 422, 429}
            saved["state"] = "rejected" if rejected else "submission_unknown"
            saved["http_status"] = error.code
            save(journal, saved)
            return {"ok": True, "status": saved["state"]}
        except (OSError, URLError, ValueError):
            saved["state"] = "submission_unknown"
            save(journal, saved)
            return {"ok": True, "status": "submission_unknown"}
        saved.update(state="submitted", task_id=ident, request_returned_at=time.time())
        save(journal, saved)
        return {"ok": True, "task_id": ident}
    if operation not in {"generation.check", "generation.cancel", "generation.collect"}:
        raise ValueError("Unsupported footage operation")
    saved = json.loads(journal.read_text(encoding="utf-8"))
    if saved["plan_digest"] != request["plan_digest"]:
        raise ValueError("Task journal belongs to a different intent")
    if not saved.get("task_id"):
        return {"ok": True, "error_code": "submission_requires_reconciliation"}
    ident = task_id(saved["task_id"])
    if request.get("task_id") and request["task_id"] != ident:
        raise ValueError("Task journal disagrees with the selected intent")
    remote = api("GET", "/" + ident)
    observation = read_observation(remote, ident)
    response = {"ok": True, "observation": observation}
    if operation == "generation.cancel":
        # Ark DELETE cancels queued tasks but *deletes records* of finished
        # ones. There is no documented atomic queued-only precondition. A GET
        # followed by DELETE can erase a task that finishes between requests.
        # Keep it intact; stopping local observation does not cancel billing.
        response["error_code"] = "atomic_cancellation_unavailable"
    elif operation == "generation.collect":
        if remote["status"] != "succeeded":
            response["error_code"] = "cloud_result_not_ready"
        else:
            response["result"] = collect(request, remote, root, deadline=deadline)
    return response
