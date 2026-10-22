"""Private composition I/O and optional renderer execution.

The shared TS owner validates plans and render transitions. Content publication
continues to use item_lifecycle; an encoded movie never grants publication.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from uuid import uuid4

from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.projects.registry_codec import load_registry
from ...extensions.runtime import default_extension_state_file, execute_extension_runtime_binding, resolve_optional_capability_binding
from ...file_lock import exclusive_file_lock, write_json_atomic
from ...registry import find_registry_goal
from .item_lifecycle import apply_content_ops_item_event, build_content_ops_item

PROTOCOL = "content_ops_media_renderer_v0"
RESULT_FILES = {"video.mp4", "poster.png", "captions.vtt", "captions.srt", "transcript.txt", "narration.wav", "manifest.json", "blender-project.zip"}
MAX_ASSET_BYTES = 64 * 1024 * 1024


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner(operation: str, request: dict) -> dict:
    return effect_runtime_result(f"content_media.{operation}", request)


def _safe_path(root: Path, relative: str) -> Path:
    if root.is_symlink(): raise ValueError("Composition directory must not be a symlink")
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in {"..", "."} for p in parts):
        raise ValueError("File is outside the selected composition scope")
    current = root
    for part in parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Symlink sources and outputs are not supported")
    if not current.resolve().is_relative_to(root.resolve()):
        raise ValueError("File is outside the selected composition scope")
    return current


def _read(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Invalid saved composition")
    with path.open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError("Invalid saved composition")
    return value


def _save(path: Path, value: dict) -> None:
    write_json_atomic(path, value, mode=0o600, directory_mode=0o700, fsync_directory=True)


def _sha(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class MediaWorkspace:
    """Goal-scoped owner-local files. The browser never supplies filesystem paths."""

    def __init__(self, *, registry_path: Path, goal_id: str, runtime_root: Path):
        goal = find_registry_goal(load_registry(registry_path), goal_id)
        if goal is None or not isinstance(goal.get("repo"), str):
            raise ValueError("Select a registered local Goal")
        self.project = Path(goal["repo"]).expanduser().resolve(strict=True)
        scope = hashlib.sha256(goal_id.encode()).hexdigest()[:24]
        self.root = _safe_path(self.project, f".local/content-media/{scope}")
        self.runtime_root = runtime_root

    def binding(self):
        return resolve_optional_capability_binding(state_file=default_extension_state_file(self.runtime_root),
            capability_id="content-ops", protocol=PROTOCOL, permission="content_media_write", extension_id="loopx-media")

    def _bundle(self, composition_id: str) -> Path:
        if not isinstance(composition_id, str) or not re.fullmatch(r"media_[a-f0-9]{24}", composition_id):
            raise ValueError("Invalid composition reference")
        return _safe_path(self.root, composition_id)

    def _record(self, composition_id: str) -> dict:
        return _read(_safe_path(self._bundle(composition_id), "record.json"))

    def prepare(self, plan: dict, *, source_directory: Path, composition_id: str | None = None, expected_revision: int | None = None) -> dict:
        validated = _owner("plan", {"plan": plan})
        composition_id = composition_id or f"media_{uuid4().hex[:24]}"
        bundle = self._bundle(composition_id)
        bundle.mkdir(mode=0o700, parents=True, exist_ok=True)
        with exclusive_file_lock(bundle / "record.json", operation="content-media-prepare"):
            previous = self._record(composition_id) if (bundle / "record.json").exists() else None
            if previous and (expected_revision != previous["revision"] or previous["status"] == "rendering"):
                raise ValueError("Inspect the current revision before changing this composition")
            if previous and previous["plan_digest"] == validated["plan_digest"]:
                return self.inspect(composition_id)
            source_root = source_directory.expanduser().resolve(strict=True)
            if not source_root.is_dir():
                raise ValueError("Choose an explicit source directory")
            snapshots, total = {}, 0
            inputs = _safe_path(bundle, "sources")
            inputs.mkdir(mode=0o700, exist_ok=True)
            for asset in validated["plan"]["assets"]:
                original = _safe_path(source_root, asset["path"])
                descriptor = os.open(original, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(descriptor, "rb") as source:
                    info = os.fstat(source.fileno())
                    total += info.st_size
                    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ASSET_BYTES or total > 128 * 1024 * 1024:
                        raise ValueError("Sources exceed the local composition budget")
                    content = source.read(MAX_ASSET_BYTES + 1)
                    if len(content) != info.st_size or hashlib.sha256(content).hexdigest() != asset["sha256"]:
                        raise ValueError("Source bytes changed; check the source digest before preparing")
                path = _safe_path(inputs, asset["sha256"])
                if path.exists():
                    if _sha(path) != asset["sha256"]: raise ValueError("Source snapshot is damaged")
                else:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as output: output.write(content)
                snapshots[asset["id"]] = str(path)
            item = previous["item"] if previous else build_content_ops_item(
                item_id=composition_id, item_kind="post", channel="local-media", content_digest=validated["plan_digest"],
                content_ref=f"content-media:{composition_id}", source_refs=[f"source:{a['sha256']}" for a in validated["plan"]["assets"]], created_at=now())
            if previous:
                item = apply_content_ops_item_event(item, {"event_id": f"revise_{uuid4().hex}", "action": "revise",
                    "expected_state": item["state"], "expected_revision": item["revision"], "occurred_at": now(),
                    "payload": {"content_digest": validated["plan_digest"], "content_ref": f"content-media:{composition_id}",
                                "source_refs": [f"source:{a['sha256']}" for a in validated["plan"]["assets"]]}})["item"]
            record = {**validated, "composition_id": composition_id, "revision": previous["revision"] + 1 if previous else 1, "status": "prepared",
                      "item": item, "sources": snapshots, "updated_at": now(), "attempt": None, "result": None,
                      "previous_result": previous.get("result") if previous else None}
            _save(bundle / "record.json", record)
        return self.inspect(composition_id)

    def inspect(self, composition_id: str) -> dict:
        record = self._record(composition_id)
        # A stopped host is recoverable on explicit render, never restarted by GET.
        status = record["status"]
        if status == "rendering":
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(record["updated_at"])).total_seconds()
            if age > 150: status = "interrupted"
        result = record.get("result")
        if result:
            for name, entry in result["files"].items():
                path = _safe_path(self._bundle(composition_id), f"{record['attempt']}/{name}")
                if not path.is_file() or _sha(path) != entry["sha256"]:
                    status = "damaged"; break
        return {"ok": True, "composition_id": composition_id, "revision": record["revision"], "status": status,
                "title": record["plan"]["title"], "duration_seconds": record["duration_seconds"],
                "plan_digest": record["plan_digest"], "shots": [{k: shot[k] for k in ("title", "seconds", "kind", "boundary", "source_ref", "framing") if k in shot} for shot in record["plan"]["shots"]],
                "updated_at": record["updated_at"], "result": result, "item_state": record["item"]["state"],
                "error_code": record.get("error_code"), "provider": self.binding().public_readiness(),
                "visibility": "local_private", "publish_authorized": False}

    def list(self) -> dict:
        paths = sorted(self.root.glob("media_*/record.json"), key=lambda path: path.stat().st_mtime, reverse=True) if self.root.exists() else []
        if len(paths) > 100: paths = paths[:100]
        return {"ok": True, "compositions": [self.inspect(path.parent.name) for path in paths],
                "provider": self.binding().public_readiness(), "visibility": "local_private"}

    def _transition(self, record: dict, action: str) -> dict:
        return _owner("transition", {"state": record, "action": action, "expected_revision": record["revision"], "observed_at": now()})["state"]

    def render(self, composition_id: str, *, expected_revision: int, background: bool = False) -> dict:
        bundle = self._bundle(composition_id)
        # The run lock spans the real provider effect; a second process cannot
        # steal a timed-out UI request's writer while that effect is still alive.
        with exclusive_file_lock(bundle / "record.json", operation="content-media-start"):
            record = self._record(composition_id)
            if record["revision"] != expected_revision:
                raise ValueError("Composition revision changed; inspect before rendering")
            binding = self.binding()
            if binding.binding is None:
                return {"ok": False, "status": "provider_unavailable", "provider": binding.public_readiness()}
            if record["status"] == "ready" and self.inspect(composition_id)["status"] == "ready":
                return self.inspect(composition_id)
            if record["status"] == "rendering":
                if self.inspect(composition_id)["status"] != "interrupted":
                    return self.inspect(composition_id)
                record = self._transition(record, "interrupt")
            # Validate persisted data again before every effect, not only import.
            checked = _owner("plan", {"plan": record["plan"]})
            if checked["plan_digest"] != record["plan_digest"]:
                raise ValueError("Composition bytes changed; prepare a new revision")
            record = self._transition(record, "start")
            record["attempt"] = f"attempt_{uuid4().hex}"
            record["result"] = None
            record["error_code"] = None
            _save(bundle / "record.json", record)
        if background:
            threading.Thread(target=self._execute, args=(composition_id, record["attempt"]), daemon=True).start()
            return self.inspect(composition_id)
        self._execute(composition_id, record["attempt"])
        return self.inspect(composition_id)

    def _execute(self, composition_id: str, attempt: str) -> None:
        bundle = self._bundle(composition_id)
        with exclusive_file_lock(bundle / "render", operation="content-media-render", timeout_seconds=1):
            record = self._record(composition_id)
            if record["attempt"] != attempt or record["status"] != "rendering": return
            cancel = _safe_path(bundle, f"{attempt}.cancel")
            try:
                binding = self.binding()
                if binding.binding is None: raise ValueError("Provider unavailable")
                checked = _owner("plan", {"plan": record["plan"]})
                result = execute_extension_runtime_binding(binding.binding, request={"protocol": PROTOCOL,
                    "plan": record["plan"], "layout_style": checked["layout_style"],
                    "plan_digest": record["plan_digest"], "frame_count": record["frame_count"],
                    "sources": record["sources"], "attempt_directory": str(_safe_path(bundle, attempt)), "cancel_file": str(cancel)})
                spatial = [{"id": shot["id"], "frames": round(shot["seconds"] * record["plan"]["fps"])}
                           for shot in record["plan"]["shots"] if shot["kind"] == "blender"]
                full_frame = [shot["id"] for shot in record["plan"]["shots"] if shot.get("framing") == "full_frame"]
                result = _owner("result", {"result": result, "plan_digest": record["plan_digest"],
                    "frame_count": record["frame_count"], "narration_mode": record["plan"]["narration"]["mode"],
                    **({"blender_shots": spatial} if spatial else {}),
                    **({"full_frame_shots": full_frame} if full_frame else {}),
                    **{k: record["plan"][k] for k in ("width", "height", "fps")}})
                required = {"video.mp4", "poster.png", "captions.vtt", "captions.srt", "transcript.txt"}
                if not required <= set(result["files"]) or set(result["files"]) - RESULT_FILES:
                    raise ValueError("Renderer output is incomplete")
                for name, entry in result["files"].items():
                    path = _safe_path(bundle, f"{attempt}/{name}")
                    if not path.is_file() or path.stat().st_size != entry["bytes"] or _sha(path) != entry["sha256"]:
                        raise ValueError("Renderer output readback failed")
                if result["qa"]["frames"] != record["frame_count"] or result["qa"]["full_decode"] != "passed":
                    raise ValueError("Renderer did not qualify the full timeline")
                record["result"] = result
                action = "cancel" if cancel.exists() else "success"
                if action == "success":
                    # Approval is bound to the actual output bytes as well as
                    # the plan. A re-encode cannot inherit an earlier approval.
                    artifact_digest = "sha256:" + hashlib.sha256(json.dumps(
                        {"plan_digest": record["plan_digest"], "files": result["files"]}, sort_keys=True).encode()).hexdigest()
                    item = record["item"]
                    record["item"] = apply_content_ops_item_event(item, {"event_id": f"render_{attempt}", "action": "revise",
                        "expected_state": item["state"], "expected_revision": item["revision"], "occurred_at": now(),
                        "payload": {"content_digest": artifact_digest, "content_ref": f"content-media:{composition_id}"}})["item"]
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                action = "cancel" if cancel.exists() else "failure"
                record["result"] = None
                record["error_code"] = "cancelled" if action == "cancel" else "render_failed"
            with exclusive_file_lock(bundle / "record.json", operation="content-media-finish"):
                current = self._record(composition_id)
                if current["attempt"] == attempt and current["revision"] == record["revision"]:
                    _save(bundle / "record.json", self._transition(record, action))

    def cancel(self, composition_id: str, *, expected_revision: int) -> dict:
        bundle, record = self._bundle(composition_id), self._record(composition_id)
        if record["revision"] != expected_revision or record["status"] != "rendering":
            raise ValueError("No matching render is running")
        path = _safe_path(bundle, f"{record['attempt']}.cancel")
        path.touch(mode=0o600, exist_ok=True)
        return {"ok": True, "status": "cancellation_requested", "composition_id": composition_id}

    def file(self, composition_id: str, name: str) -> Path:
        if name not in RESULT_FILES: raise ValueError("Unsupported result file")
        record = self._record(composition_id)
        if record["status"] != "ready" or self.inspect(composition_id)["status"] != "ready":
            raise ValueError("A verified result is not available")
        if name == "manifest.json": raise ValueError("Use the path-free result projection")
        if name not in record["result"]["files"]: raise ValueError("Result file is not available")
        return _safe_path(self._bundle(composition_id), f"{record['attempt']}/{name}")
