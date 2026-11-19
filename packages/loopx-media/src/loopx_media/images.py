"""One PNG generation, durable response recovery, no automatic paid retry."""

from __future__ import annotations

import base64
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image

from .journal import save as save_journal

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
MODEL = "doubao-seedream-5-0-pro-260628"
MAXIMUM = 32 * 1024 * 1024


def sha(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def save(path: Path, value: dict) -> None:
    save_journal(path, value, private=True, ensure_ascii=True)


def available() -> bool:
    return (
        bool(os.environ.get("ARK_IMAGE_API_KEY"))
        and os.environ.get("ARK_IMAGE_BASE_URL", BASE_URL).rstrip("/") == BASE_URL
    )


def api(payload: dict, root: Path, plan_digest: str) -> dict:
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError("Unexpected image API redirect")

    if not available():
        raise ValueError("Image credential unavailable")
    request = Request(
        BASE_URL + "/images/generations",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + os.environ["ARK_IMAGE_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    deadline = time.monotonic() + 90
    chunks, size = [], 0
    with build_opener(NoRedirect).open(request, timeout=70) as response:
        observed = {
            "plan_digest": plan_digest,
            "http_status": response.status,
            "request_id": response.headers.get("x-tt-logid", "")[:200],
            "observed_at": time.time(),
        }
        save(root / "image-http-response.json", observed)
        while data := response.read(65536):
            size += len(data)
            if size > MAXIMUM or time.monotonic() > deadline:
                raise ValueError("Image response exceeds the operation budget")
            chunks.append(data)
    body = b"".join(chunks)
    temporary = root / "image-api-response.partial"
    if temporary.is_symlink() or (root / "image-api-response.body").is_symlink():
        raise ValueError("Invalid image response cache")
    with temporary.open("wb") as output:
        os.chmod(temporary, 0o600)
        output.write(body)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(root / "image-api-response.body")
    save(
        root / "image-http-response.json",
        {
            **observed,
            "body_bytes": len(body),
            "body_sha256": hashlib.sha256(body).hexdigest(),
        },
    )
    result = json.loads(body)
    if not isinstance(result, dict):
        raise ValueError("Invalid image response")
    return result


def cache_image_response(response: dict, request: dict, root: Path) -> None:
    items, usage = response.get("data"), response.get("usage")
    if (
        response.get("error")
        or not isinstance(items, list)
        or len(items) != 1
        or not isinstance(items[0], dict)
        or items[0].get("error")
        or response.get("model") != MODEL
        or not isinstance(usage, dict)
        or type(usage.get("generated_images")) is not int
        or usage["generated_images"] != 1
    ):
        raise ValueError("Image result identity, schema or count changed")
    data = base64.b64decode(items[0]["b64_json"], validate=True)
    if not data or len(data) > MAXIMUM:
        raise ValueError("Image bytes exceed the operation budget")
    save(
        root / "image-response.json",
        {
            "plan_digest": request["plan_digest"],
            "model": MODEL,
            "created": response.get("created"),
            "b64_json": items[0]["b64_json"],
            "sha256": hashlib.sha256(data).hexdigest(),
            "usage": {
                key: usage[key]
                for key in ("generated_images", "output_tokens")
                if key in usage
            },
        },
    )


def reference(request: dict, root: Path) -> tuple[list[str], list[dict]]:
    urls, provenance = [], []
    for i, ref in enumerate(request["plan"]["references"]):
        path = Path(request["sources"][str(i)])
        if (
            path.is_symlink()
            or path.stat().st_size > 4 * 1024 * 1024
            or sha(path) != ref["sha256"]
        ):
            raise ValueError("Image reference bytes changed")
        with Image.open(path) as image:
            if (
                image.format not in {"PNG", "JPEG"}
                or not 196 <= image.width * image.height <= 36000000
                or not 1 / 16 <= image.width / image.height <= 16
            ):
                raise ValueError("Unsupported image reference")
            image.load()
            rgba = image.convert("RGBA")
            flat = Image.new("RGBA", rgba.size, "white")
            flat.alpha_composite(rgba)
            encoded = BytesIO()
            flat.convert("RGB").save(encoded, format="PNG")
        data = encoded.getvalue()
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("Prepared reference exceeds the upload budget")
        urls.append("data:image/png;base64," + base64.b64encode(data).decode("ascii"))
        provenance.append(
            {
                "original_sha256": ref["sha256"],
                "sent_sha256": hashlib.sha256(data).hexdigest(),
                "preprocessing": "alpha_composite_white_rgb_png",
            }
        )
    return urls, provenance


def collect(request: dict, root: Path) -> dict:
    cache = root / "image-response.json"
    if not cache.exists():
        metadata = root / "image-http-response.json"
        raw = root / "image-api-response.body"
        if (
            metadata.is_symlink()
            or metadata.stat().st_size > 65536
            or raw.is_symlink()
            or raw.stat().st_size > MAXIMUM
        ):
            raise ValueError("Invalid original image response")
        saved_http = json.loads(metadata.read_text())
        if (
            saved_http.get("plan_digest") != request["plan_digest"]
            or saved_http.get("body_bytes") != raw.stat().st_size
            or saved_http.get("body_sha256") != sha(raw)
        ):
            raise ValueError("Original image response changed")
        response = json.loads(raw.read_bytes())
        if not isinstance(response, dict):
            raise ValueError("Invalid original image response")
        cache_image_response(response, request, root)
    if cache.is_symlink() or cache.stat().st_size > MAXIMUM:
        raise ValueError("Invalid saved image response")
    saved = json.loads(cache.read_text())
    if saved["plan_digest"] != request["plan_digest"] or saved["model"] != MODEL:
        raise ValueError("Image response belongs to a different intent")
    data = base64.b64decode(saved["b64_json"], validate=True)
    if (
        not data
        or len(data) > MAXIMUM
        or hashlib.sha256(data).hexdigest() != saved["sha256"]
    ):
        raise ValueError("Saved image bytes changed")
    output = root / "output"
    if output.is_symlink():
        raise ValueError("Invalid image output directory")
    output.mkdir(mode=0o700, exist_ok=True)
    for name in ["original.png", "generated.png"]:
        if (output / name).is_symlink():
            raise ValueError("Invalid image output path")
    with Image.open(BytesIO(data)) as image:
        plan = request["plan"]
        if image.format != "PNG" or image.size != (plan["width"], plan["height"]):
            raise ValueError(
                "Returned image dimensions or format differ from the request"
            )
        image.load()
        rgba = image.convert("RGBA")
        flat = Image.new("RGBA", rgba.size, "white")
        flat.alpha_composite(rgba)
        temporary = output / "generated.partial.png"
        if temporary.is_symlink():
            raise ValueError("Invalid image temporary output")
        flat.convert("RGB").save(temporary, format="PNG")
        temporary.replace(output / "generated.png")
    original = output / "original.png"
    with original.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return {
        "ok": True,
        "result": {
            "plan_digest": request["plan_digest"],
            "model": MODEL,
            "files": {
                name: {
                    "sha256": sha(output / name),
                    "bytes": (output / name).stat().st_size,
                }
                for name in ["original.png", "generated.png"]
            },
            "width": plan["width"],
            "height": plan["height"],
            "full_decode": "passed",
            "usage": saved["usage"],
        },
    }


def operate(request: dict) -> dict:
    if request.get("protocol") != "content_ops_media_renderer_v0":
        raise ValueError("Unsupported image protocol")
    action = request.get("operation")
    if action == "image.probe":
        return {"ok": True, "credential_available": available()}
    root = Path(request["directory"])
    if (
        not root.is_absolute()
        or root.is_symlink()
        or not root.is_dir()
        or request["plan"]["model"] != MODEL
    ):
        raise ValueError("Invalid image request")
    if action == "image.collect":
        return collect(request, root)
    if action != "image.submit":
        raise ValueError("Unsupported image operation")
    journal = root / "image-submission.json"
    if journal.exists() or journal.is_symlink():
        raise ValueError(
            "A submission was already attempted; inspect its saved response"
        )
    plan = request["plan"]
    try:
        images, provenance = reference(request, root)
    except (OSError, ValueError, Image.DecompressionBombError):
        return {
            "ok": True,
            "status": "rejected",
            "error_code": "image_reference_invalid",
        }
    payload = {
        "model": MODEL,
        "prompt": plan["prompt"],
        "size": f"{plan['width']}x{plan['height']}",
        "watermark": plan["watermark"],
        "response_format": "b64_json",
        "output_format": "png",
        "layer_decomposition": False,
        "background": "opaque",
        "optimize_prompt_options": {"mode": "standard"},
    }
    if images:
        payload["image"] = images[0]
    saved = {
        "plan_digest": request["plan_digest"],
        "state": "sending",
        "references": provenance,
        "started_at": time.time(),
    }
    save(journal, saved)
    try:
        response = api(payload, root, request["plan_digest"])
        items = response.get("data")
        if response.get("error"):
            saved["state"] = "rejected"
            save(journal, saved)
            return {
                "ok": True,
                "status": "rejected",
                "error_code": "image_request_rejected",
            }
        if (
            isinstance(items, list)
            and len(items) == 1
            and isinstance(items[0], dict)
            and items[0].get("error")
        ):
            saved["state"] = "rejected"
            save(journal, saved)
            return {
                "ok": True,
                "status": "rejected",
                "error_code": "image_request_rejected",
            }
        cache_image_response(response, request, root)
    except HTTPError as error:
        rejected = error.code in {400, 401, 403, 404, 413, 422, 429}
        saved.update(
            state="rejected" if rejected else "submission_unknown",
            http_status=error.code,
            request_id=(
                error.headers.get("x-tt-logid", "")[:200] if error.headers else ""
            ),
        )
        save(journal, saved)
        return {
            "ok": True,
            "status": saved["state"],
            "error_code": "image_request_rejected"
            if rejected
            else "image_submission_unknown",
        }
    except (OSError, URLError, ValueError, KeyError, TypeError) as error:
        saved.update(state="submission_unknown", error_type=type(error).__name__)
        if isinstance(error, URLError):
            saved["reason_type"] = type(error.reason).__name__
        save(journal, saved)
        return {
            "ok": True,
            "status": "submission_unknown",
            "error_code": "image_submission_unknown",
        }
    saved.update(state="response_saved", returned_at=time.time())
    save(journal, saved)
    return collect(request, root)
