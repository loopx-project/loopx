"""Version-bound result use; host IO around the typed adoption decision.

Only the requester's existing operations are addressable. These observations do
not promote a Goal or attest to a model's reasoning or the truth of an artifact.
"""
from __future__ import annotations

from .inbox import _entry, _read, _write
from .peers import input_readiness, require_operation_id
from ..effect_runtime import effect_runtime_result, EffectRuntimeRemoteError
from ...file_lock import exclusive_file_lock


def operation_brief(service, row):
    identity = row["identity"]
    return _entry(service.root, service.goal_id, identity["binding"]["agent_id"], identity["request_id"])["brief"]


def dependencies(service, binding, brief):
    inputs = [item for item in brief["inputs"] if "delegation" in item]
    if not inputs:
        return []
    materials = input_readiness(service.registry, service.goal_id, {"inputs": inputs}, workspace=binding["workspace"])
    result = []
    for item, material in zip(inputs, materials, strict=True):
        link = item["delegation"]
        current = False
        try:
            source = service._read_current(link["operation_id"])
            current = source["status"] == "accepted" and material["status"] == "available" and any(
                artifact["ref"] == link["ref"] and artifact["sha256"] == item["sha256"]
                for artifact in source.get("artifacts", []))
        except (OSError, ValueError, KeyError, EffectRuntimeRemoteError):
            pass  # Preserve the reference, never a saved success or private exception text.
        result.append({**link, "sha256": item["sha256"], "input_ref": item["ref"],
                       "state": "current" if current else "unavailable"})
    return result


def require_dependencies(service, binding, brief):
    if any(row["state"] != "current" for row in dependencies(service, binding, brief)):
        raise ValueError("delegation input version unavailable; reconcile source and receiver input")


def adoption_evidence(service, operation_id, consumer_operation_id):
    require_operation_id(operation_id)
    require_operation_id(consumer_operation_id)
    source = service._read_current(operation_id)
    consumer = service._read_current(consumer_operation_id)
    row = _read(service.path(consumer_operation_id))
    binding = service._bound(row)
    brief = operation_brief(service, row)
    links = dependencies(service, binding, brief)
    return effect_runtime_result("collaboration.delegation.adoption", {
        "source": source, "consumer": consumer, "inputs": brief["inputs"],
        "inputs_current": bool(links) and all(link["state"] == "current" for link in links),
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
    links = dependencies(service, binding, operation_brief(service, row))
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
    return {**({"dependencies": links} if links else {}), **({"adoptions": adoptions} if adoptions else {})}
