"""Goal-local footage I/O. TS owns admission and state; the optional provider
performs one bounded API operation. A local read never submits or polls a job.
"""

from __future__ import annotations

from pathlib import Path
import re
from uuid import uuid4

from ...extensions.runtime import execute_extension_runtime_binding
from ...file_lock import exclusive_file_lock
from .media import MediaWorkspace, PROTOCOL, _owner, _read, _safe_path, _save, _sha, now


class GenerationWorkspace:
    def __init__(self, workspace: MediaWorkspace):
        self.media = workspace
        self.root = _safe_path(workspace.root, "generations")

    def binding(self):
        return self.media.binding(permission="content_media_generate")

    def _bundle(self, ident: str) -> Path:
        if not isinstance(ident, str) or not re.fullmatch(r"gen_[a-f0-9]{24}", ident):
            raise ValueError("Invalid footage reference")
        return _safe_path(self.root, ident)

    def _record(self, ident: str) -> dict:
        return _read(_safe_path(self._bundle(ident), "record.json"))

    def _transition(self, record: dict, action: str, **values) -> dict:
        return _owner(
            "generation_transition",
            {
                "state": record,
                "action": action,
                "expected_revision": record["revision"],
                "observed_at": now(),
                **values,
            },
        )["state"]

    def prepare(
        self, plan: dict, *, source_directory: Path, generation_id: str | None = None
    ) -> dict:
        checked = _owner("generation_plan", {"plan": plan})
        ident = generation_id or f"gen_{uuid4().hex[:24]}"
        bundle = self._bundle(ident)
        bundle.mkdir(mode=0o700, parents=True, exist_ok=True)
        with exclusive_file_lock(
            bundle / "record.json", operation="content-footage-prepare"
        ):
            if (bundle / "record.json").exists():
                if self._record(ident)["plan_digest"] != checked["plan_digest"]:
                    raise ValueError(
                        "Footage intents are immutable; prepare a new intent for a changed brief"
                    )
            else:
                sources = self.media.snapshot_sources(
                    bundle,
                    checked["plan"]["references"],
                    source_directory,
                    key="role",
                    max_each=2 * 1024 * 1024,
                )
                _save(
                    bundle / "record.json",
                    {
                        **checked,
                        "generation_id": ident,
                        "revision": 1,
                        "status": "prepared",
                        "sources": sources,
                        "task_id": None,
                        "observation": None,
                        "result": None,
                        "error_code": None,
                        "updated_at": now(),
                    },
                )
        return self.inspect(ident)

    def inspect(self, ident: str) -> dict:
        record = self._record(ident)
        result = record.get("result")
        status = record["status"]
        if result and any(
            not _safe_path(self._bundle(ident), f"output/{name}").is_file()
            or _sha(_safe_path(self._bundle(ident), f"output/{name}")) != item["sha256"]
            for name, item in result["files"].items()
        ):
            status = "damaged"
        observation = record.get("observation")
        tokens = observation.get("completion_tokens") if observation else None
        return {
            "ok": True,
            "generation_id": ident,
            "revision": record["revision"],
            "status": status,
            "title": record["plan"]["title"],
            "model": record["plan"]["model"],
            "seconds": record["plan"]["seconds"],
            "resolution": record["plan"]["resolution"],
            "prompt": record["plan"]["prompt"],
            "references": [
                {k: ref[k] for k in ("role", "sha256", "source_ref", "visibility")}
                for ref in record["plan"]["references"]
            ],
            "plan_digest": record["plan_digest"],
            "estimate": record["estimate"],
            "quote": record["plan"]["quote"],
            "watermark_requested": record["plan"]["watermark"],
            "generate_audio": record["plan"]["generate_audio"],
            "task_id": record.get("task_id"),
            "observation": observation,
            "result": result,
            "reported_usage_estimate_cny": None
            if tokens is None
            else round(
                tokens * record["plan"]["quote"]["cny_per_million_tokens"] / 1e6, 4
            ),
            "error_code": record.get("error_code"),
            "updated_at": record["updated_at"],
            "provider": self.binding().public_readiness(),
            "visibility": "local_private",
            "publish_authorized": False,
        }

    def list(self) -> dict:
        paths = (
            sorted(
                self.root.glob("gen_*/record.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if self.root.exists()
            else []
        )
        return {
            "ok": True,
            "generations": [self.inspect(p.parent.name) for p in paths[:100]],
            "provider": self.binding().public_readiness(),
        }

    def _call(self, record: dict, operation: str) -> dict:
        binding = self.binding()
        if binding.binding is None:
            raise ValueError(
                "Enable a content-media provider with explicit cloud-generation permission"
            )
        return execute_extension_runtime_binding(
            binding.binding,
            request={
                "protocol": PROTOCOL,
                "operation": f"generation.{operation}",
                "plan": record["plan"],
                "plan_digest": record["plan_digest"],
                "sources": record["sources"],
                "directory": str(self._bundle(record["generation_id"])),
                "task_id": record.get("task_id"),
            },
        )

    def operate(self, ident: str, action: str, *, expected_revision: int) -> dict:
        bundle = self._bundle(ident)
        # One short API operation per writer. No poll loop and no automatic POST
        # retry; a host crash preserves the intent and provider's task-id journal.
        with exclusive_file_lock(
            bundle / "record.json",
            operation="content-footage-effect",
            timeout_seconds=1,
        ):
            record = self._record(ident)
            if record["revision"] != expected_revision:
                raise ValueError("Read the current footage revision")
            checked = _owner("generation_plan", {"plan": record["plan"]})
            if checked["plan_digest"] != record["plan_digest"]:
                raise ValueError("Footage intent bytes changed")
            if action == "submit":
                if record["status"] != "prepared":
                    return self.inspect(ident)
                try:
                    readiness = self._call(record, "probe")
                except (OSError, ValueError, RuntimeError):
                    readiness = {"credential_available": False}
                if not readiness.get("credential_available"):
                    return {
                        **self.inspect(ident),
                        "ok": False,
                        "error_code": "generation_provider_unavailable",
                    }
                record = self._transition(record, "submit")
                _save(bundle / "record.json", record)
                try:
                    response = self._call(record, "submit")
                except (OSError, ValueError, RuntimeError):
                    response = {"status": "submission_unknown"}
                if response.get("status") in {"rejected", "submission_unknown"}:
                    record = self._transition(
                        record,
                        "reject" if response["status"] == "rejected" else "unknown",
                    )
                    record["error_code"] = (
                        response.get("error_code") or response["status"]
                    )
                else:
                    record = self._transition(
                        record, "acknowledge", task_id=response["task_id"]
                    )
            elif action in {"check", "cancel", "collect"}:
                if record["status"] in {"prepared", "rejected"}:
                    raise ValueError("No cloud task is available for this intent")
                if record["status"] == "ready":
                    if self.inspect(ident)["status"] == "ready" or action != "collect":
                        return self.inspect(ident)
                    record = self._transition(
                        record, "repair", artifacts_verified=False
                    )
                try:
                    response = self._call(record, action)
                    if response.get("observation"):
                        record = self._transition(
                            record, "observe", observation=response["observation"]
                        )
                    if action == "collect" and response.get("result"):
                        updated = self._transition(
                            record, "collect", result=response["result"]
                        )
                        for name, entry in updated["result"]["files"].items():
                            path = _safe_path(bundle, f"output/{name}")
                            if (
                                not path.is_file()
                                or path.stat().st_size != entry["bytes"]
                                or _sha(path) != entry["sha256"]
                            ):
                                raise ValueError("Generated output readback failed")
                        record = updated
                    record["error_code"] = response.get("error_code")
                except (OSError, ValueError, RuntimeError, KeyError):
                    record["error_code"] = (
                        "collection_failed"
                        if action == "collect"
                        else "observation_unavailable"
                    )
                    if record["status"] == "submitting":
                        record = self._transition(record, "unknown")
            else:
                raise ValueError("Unsupported footage operation")
            _save(bundle / "record.json", record)
        return {**self.inspect(ident), "ok": record.get("error_code") is None}

    def file(self, ident: str, name: str, *, expected_revision: int | None = None) -> Path:
        if name not in {"generated.mp4", "poster.png", "last-frame.jpg"}:
            raise ValueError("Verified footage file unavailable")
        record = self._record(ident)
        observed = self.inspect(ident)
        if expected_revision is not None and (type(expected_revision) is not int
                or expected_revision != record["revision"] or expected_revision != observed["revision"]):
            raise ValueError("Artifact revision changed")
        if (
            observed["status"] != "ready"
            or name not in record["result"]["files"]
        ):
            raise ValueError("Verified footage file unavailable")
        return _safe_path(self._bundle(ident), f"output/{name}")
