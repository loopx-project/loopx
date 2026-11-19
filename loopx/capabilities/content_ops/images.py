"""Private image intents; TS owns admission, the optional provider owns effects."""

from __future__ import annotations

from pathlib import Path
import re
from uuid import uuid4

from ...extensions.runtime import execute_extension_runtime_binding
from ...file_lock import exclusive_file_lock
from .media import MediaWorkspace, PROTOCOL, _owner, _read, _safe_path, _save, _sha, now


class ImageWorkspace:
    def __init__(self, media: MediaWorkspace):
        self.media = media
        self.root = _safe_path(media.root, "images")

    def binding(self):
        return self.media.binding(permission="content_media_generate")

    def _bundle(self, ident: str) -> Path:
        if not isinstance(ident, str) or not re.fullmatch(r"img_[a-f0-9]{24}", ident):
            raise ValueError("Invalid image reference")
        return _safe_path(self.root, ident)

    def _record(self, ident: str) -> dict:
        return _read(_safe_path(self._bundle(ident), "record.json"))

    def _transition(self, record: dict, action: str, **values) -> dict:
        return _owner(
            "image_transition",
            {
                "state": record,
                "action": action,
                "expected_revision": record["revision"],
                "observed_at": now(),
                **values,
            },
        )["state"]

    def prepare(
        self, plan: dict, *, source_directory: Path, image_id: str | None = None
    ) -> dict:
        checked = _owner("image_plan", {"plan": plan})
        ident = image_id or f"img_{uuid4().hex[:24]}"
        bundle = self._bundle(ident)
        bundle.mkdir(mode=0o700, parents=True, exist_ok=True)
        with exclusive_file_lock(
            bundle / "record.json", operation="content-image-prepare"
        ):
            if (bundle / "record.json").exists():
                if self._record(ident)["plan_digest"] != checked["plan_digest"]:
                    raise ValueError(
                        "Image intents are immutable; prepare another for a changed brief"
                    )
            else:
                refs = [
                    {**ref, "id": str(i)}
                    for i, ref in enumerate(checked["plan"]["references"])
                ]
                sources = self.media.snapshot_sources(
                    bundle, refs, source_directory, max_each=4 * 1024 * 1024
                )
                _save(
                    bundle / "record.json",
                    {
                        **checked,
                        "image_id": ident,
                        "revision": 1,
                        "status": "prepared",
                        "sources": sources,
                        "result": None,
                        "error_code": None,
                        "updated_at": now(),
                    },
                )
        return self.inspect(ident)

    def inspect(self, ident: str) -> dict:
        record = self._record(ident)
        result, status = record.get("result"), record["status"]
        if result and any(
            not _safe_path(self._bundle(ident), f"output/{name}").is_file()
            or _sha(_safe_path(self._bundle(ident), f"output/{name}")) != meta["sha256"]
            for name, meta in result["files"].items()
        ):
            status = "damaged"
        plan = record["plan"]
        return {
            "ok": True,
            "image_id": ident,
            "revision": record["revision"],
            "status": status,
            **{
                key: plan[key]
                for key in ("title", "model", "prompt", "width", "height", "quote")
            },
            "references": [
                {k: ref[k] for k in ("sha256", "source_ref", "visibility")}
                for ref in plan["references"]
            ],
            "plan_digest": record["plan_digest"],
            "estimate": record["estimate"],
            "watermark_requested": plan["watermark"],
            "result": result,
            "error_code": record.get("error_code"),
            "updated_at": record["updated_at"],
            "reported_usage_estimate_cny": None
            if result is None
            else round(
                result["usage"]["generated_images"] * plan["quote"]["cny_per_image"], 4
            ),
            "provider": self.binding().public_readiness(),
            "visibility": "local_private",
            "publish_authorized": False,
        }

    def list(self) -> dict:
        paths = (
            sorted(
                self.root.glob("img_*/record.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if self.root.exists()
            else []
        )
        return {
            "ok": True,
            "images": [self.inspect(p.parent.name) for p in paths[:100]],
            "provider": self.binding().public_readiness(),
        }

    def _call(self, record: dict, action: str) -> dict:
        binding = self.binding()
        if binding.binding is None:
            raise ValueError("Enable the optional cloud media provider")
        return execute_extension_runtime_binding(
            binding.binding,
            request={
                "protocol": PROTOCOL,
                "operation": f"image.{action}",
                "plan": record["plan"],
                "plan_digest": record["plan_digest"],
                "sources": record["sources"],
                "directory": str(self._bundle(record["image_id"])),
            },
        )

    def operate(self, ident: str, action: str, *, expected_revision: int) -> dict:
        bundle = self._bundle(ident)
        with exclusive_file_lock(
            bundle / "record.json", operation="content-image-effect", timeout_seconds=1
        ):
            record = self._record(ident)
            if expected_revision != record["revision"]:
                raise ValueError("Read the current image revision")
            if (
                _owner("image_plan", {"plan": record["plan"]})["plan_digest"]
                != record["plan_digest"]
            ):
                raise ValueError("Image intent bytes changed")
            if action == "submit":
                if record["status"] != "prepared":
                    return self.inspect(ident)
                try:
                    probe = self._call(record, "probe")
                except (OSError, ValueError, RuntimeError):
                    probe = {}
                if not probe.get("credential_available"):
                    return {
                        **self.inspect(ident),
                        "ok": False,
                        "error_code": "image_provider_unavailable",
                    }
                record = self._transition(record, "submit")
                _save(bundle / "record.json", record)
            elif action != "collect" or record["status"] not in {
                "submitting",
                "submission_unknown",
                "ready",
            }:
                raise ValueError("No saved image response is available")
            elif self.inspect(ident)["status"] == "ready":
                return self.inspect(ident)
            try:
                response = self._call(record, action)
                if response.get("result"):
                    updated = self._transition(
                        record, "collect", result=response["result"]
                    )
                    for name, meta in updated["result"]["files"].items():
                        path = _safe_path(bundle, f"output/{name}")
                        if (
                            not path.is_file()
                            or path.stat().st_size != meta["bytes"]
                            or _sha(path) != meta["sha256"]
                        ):
                            raise ValueError("Image result bytes changed")
                    record = updated
                elif record["status"] == "submitting":
                    record = self._transition(
                        record,
                        "reject" if response.get("status") == "rejected" else "unknown",
                    )
                record["error_code"] = response.get("error_code") or (
                    None
                    if record["status"] == "ready"
                    else "image_response_unavailable"
                )
            except (OSError, ValueError, RuntimeError, KeyError):
                if record["status"] == "submitting":
                    record = self._transition(record, "unknown")
                record["error_code"] = "image_operation_unconfirmed"
            _save(bundle / "record.json", record)
        return {**self.inspect(ident), "ok": record.get("error_code") is None}

    def file(self, ident: str, name: str, *, expected_revision: int | None = None) -> Path:
        if name not in {"generated.png", "original.png"}:
            raise ValueError("Verified image unavailable")
        observed = self.inspect(ident)
        if expected_revision is not None and (type(expected_revision) is not int
                or expected_revision != observed["revision"]):
            raise ValueError("Artifact revision changed")
        if observed["status"] != "ready":
            raise ValueError("Verified image unavailable")
        return _safe_path(self._bundle(ident), f"output/{name}")
