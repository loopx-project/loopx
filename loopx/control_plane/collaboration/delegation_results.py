"""Version-bound result use; host IO around the typed adoption decision.

Only the requester's existing operations are addressable. These observations do
not promote a Goal or attest to a model's reasoning or the truth of an artifact.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from collections import deque

from .inbox import _entry, _read, _write
from .peers import input_readiness, require_operation_id
from ..effect_runtime import effect_runtime_result, EffectRuntimeRemoteError
from ...file_lock import exclusive_file_lock


def _artifacts(binding):
    """Bounded host IO; the typed owner decides version/check relationships."""
    workspace = Path(binding["workspace"]).resolve()
    artifacts = []
    for ref in binding["output_refs"]:
        path = workspace / ref
        if not path.resolve().is_relative_to(workspace) or path.is_symlink() or not path.is_file():
            raise ValueError("delegation artifact unavailable or outside workspace")
        if path.stat().st_size > 128_000:
            raise ValueError("delegation artifact exceeds bounded return size")
        with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                             | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("delegation artifact must be a regular file")
            content = stream.read(128_001)
        if len(content) > 128_000:
            raise ValueError("delegation artifact exceeds bounded return size")
        artifacts.append({"ref": ref, "sha256": hashlib.sha256(content).hexdigest(),
                          "text": content.decode("utf-8")})
    if len(json.dumps(artifacts).encode()) > 64_000:
        raise ValueError("delegation aggregate return exceeds limit")
    return artifacts


def accepted_result(service, binding):
    before = _artifacts(binding)
    validation = service._validate(binding)  # Executes the current canonical rules.
    after = _artifacts(binding)
    observation = effect_runtime_result("collaboration.delegation.checked_artifacts", {
        "binding": binding, "plan": validation["plan"],
        "before": [{"ref": row["ref"], "sha256": row["sha256"]} for row in before],
        "after": [{"ref": row["ref"], "sha256": row["sha256"]} for row in after],
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })
    return {"artifacts": after, "validation": observation}


def operation_brief(service, row):
    identity = row["identity"]
    return _entry(service.root, service.goal_id, identity["binding"]["agent_id"], identity["request_id"])["brief"]


class _ResultUseRead:
    """One admission's authorized host IO, with no eligibility decision cache."""

    def __init__(self, service):
        self.service = service
        self.limits = effect_runtime_result("collaboration.delegation.result_use_limits", {})
        self.deadline = time.monotonic() + self.limits["max_seconds"]
        self.nodes = {}
        self.truncated = False

    def inputs(self, binding, brief):
        inputs = [item for item in brief["inputs"] if "delegation" in item]
        if not inputs:
            return []
        materials = input_readiness(
            self.service.registry, self.service.goal_id, {"inputs": inputs},
            workspace=binding["workspace"], configured_workspace=True,
        )
        return [{**item["delegation"], "sha256": item["sha256"], "input_ref": item["ref"],
                 "input_available": material["status"] == "available"}
                for item, material in zip(inputs, materials, strict=True)]

    def collect(self, roots):
        pending = deque((item["operation_id"], 1) for item in roots)
        while pending:
            operation, depth = pending.popleft()
            if operation in self.nodes:
                continue
            if (len(self.nodes) >= self.limits["max_operations"]
                    or depth > self.limits["max_depth"] or time.monotonic() >= self.deadline):
                self.truncated = True
                continue
            # path/_bound enforce the requester's existing operation grant.
            node = {"operation_id": operation, "accepted": False, "artifacts": [], "inputs": []}
            self.nodes[operation] = node
            try:
                source = self.service._read_current(operation)
                row = _read(self.service.path(operation))
                binding = self.service._bound(row)
                node.update(accepted=source["status"] == "accepted",
                            artifacts=[{"ref": item["ref"], "sha256": item["sha256"]}
                                       for item in source.get("artifacts", [])],
                            inputs=self.inputs(binding, operation_brief(self.service, row)))
                pending.extend((item["operation_id"], depth + 1) for item in node["inputs"])
            except (OSError, ValueError, KeyError, EffectRuntimeRemoteError):
                # Only the typed owner interprets unavailable observations.
                # Private paths, validator output and exception text never escape.
                node.update(accepted=False, artifacts=[], inputs=[])
        if time.monotonic() > self.deadline:
            self.truncated = True

    def qualify(self, roots):
        return effect_runtime_result("collaboration.delegation.result_use", {
            "roots": roots, "nodes": list(self.nodes.values()), "truncated": self.truncated,
        })


def _dependency_observation(service, binding, brief):
    if not any("delegation" in item for item in brief["inputs"]):
        return [], None  # Ordinary reads preserve their existing wire shape.
    current = _ResultUseRead(service)
    roots = current.inputs(binding, brief)
    current.collect(roots)
    links = []
    for item in roots:
        decision = current.qualify([item])
        links.append({key: value for key, value in item.items() if key != "input_available"}
                     | {"state": decision["state"]})
    # Account for time spent qualifying links before the admission decision.
    current.truncated |= time.monotonic() >= current.deadline
    aggregate = current.qualify(roots)
    if current.truncated:
        for link in links:
            link["state"] = aggregate["state"]
    return links, aggregate


def require_dependencies(service, binding, brief):
    _, aggregate = _dependency_observation(service, binding, brief)
    if aggregate is not None and aggregate["state"] != "current":
        raise ValueError("delegation input version unavailable; reconcile source and receiver input")


def adoption_evidence(service, operation_id, consumer_operation_id):
    require_operation_id(operation_id)
    require_operation_id(consumer_operation_id)
    source = service._read_current(operation_id)
    consumer = service._read_current(consumer_operation_id)
    row = _read(service.path(consumer_operation_id))
    binding = service._bound(row)
    brief = operation_brief(service, row)
    links, aggregate = _dependency_observation(service, binding, brief)
    return effect_runtime_result("collaboration.delegation.adoption", {
        "source": source, "consumer": consumer, "inputs": brief["inputs"],
        "inputs_current": bool(links) and aggregate["state"] == "current",
    })


def adopt_result(service, operation_id, consumer_operation_id):
    path = service.path(require_operation_id(operation_id))
    # Accepted rows are terminal; serialize with the original worker and other decisions.
    with exclusive_file_lock(path):
        evidence = adoption_evidence(service, operation_id, consumer_operation_id)
        row = _read(path)
        service._bound(row, require_active=True)
        records = row.setdefault("adoptions", [])
        previous = next((item for item in records if item["consumer_operation_id"] == consumer_operation_id), None)
        if previous is not None and previous != evidence:
            raise ValueError("adoption evidence changed; reconcile original executions")
        if previous is None:
            if len(records) >= 12:
                raise ValueError("delegation adoption record limit reached")
            records.append(evidence)
            _write(path, row)
    return service.read(operation_id)


def result_relationships(service, operation_id):
    row = _read(service.path(operation_id))
    binding = service._bound(row)
    links, current_use = _dependency_observation(service, binding, operation_brief(service, row))
    adoptions = []
    for recorded in row.get("adoptions", []):
        current = False
        try:
            current = adoption_evidence(service, operation_id, recorded["consumer_operation_id"]) == recorded
        except (OSError, ValueError, KeyError, EffectRuntimeRemoteError):
            pass
        adoptions.append({**recorded, "requester_agent_id": service.agent_id,
                          "state": "current" if current else "unavailable"})
    # Preserve feature-off shape; ordinary reads never gain an inferred relationship.
    return {**({"dependencies": links, "current_use": current_use} if links else {}),
            **({"adoptions": adoptions} if adoptions else {})}
