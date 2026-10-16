"""Private file rendezvous while the owned native process remains interrupted.

Only the external controller can freeze all worker writers and capture native
evaluator authority. This transport grants neither capability to the worker.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid


# One shared budget for explicitly enabled full controller capture, including complete
# native/research preparation and the controller's live-source export. Mature
# histories can exhaust 30 seconds before the writer barrier even starts. The
# original phase still caps this clock; handoff/retries cannot renew it.
CHECKPOINT_CAPTURE_SECONDS = 90
CONTROLLER_SEAL_SECONDS = 30


def controller_completion_deadline(capture_deadline, phase_deadline=None):
    """Bound private sealing separately without extending live-source capture.

    This is the benchmark mailbox's transport budget, not a new trial budget or
    artifact admission rule. Both peers use the original absolute capture clock;
    starting or retrying a reader cannot renew the completion allowance.
    """
    if not math.isfinite(capture_deadline):
        raise ValueError("controller capture deadline must be finite")
    completion = capture_deadline + CONTROLLER_SEAL_SECONDS
    if phase_deadline is not None:
        if not math.isfinite(phase_deadline):
            raise ValueError("controller phase deadline must be finite")
        completion = min(completion, phase_deadline)
    return completion


def await_controller_capture(root, components, metadata, probe, deadline, *, phase_deadline=None):
    completion_deadline = controller_completion_deadline(deadline, phase_deadline)
    directory = root.parent / "checkpoint-controller"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.resolve() != directory:
        raise ValueError("checkpoint controller mailbox must be physical")
    nonce = uuid.uuid4().hex
    ready = directory / "ready.json"
    reply = directory / (nonce + ".json")
    value = {"capture_id": nonce, "components": components,
             "point_metadata": metadata, "source_sha256": probe["source_sha256"],
             "source_witness": probe["source_witness"],
             "deadline_epoch": deadline}
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    # A surviving older request is an explicit recovery problem, not authority
    # to replace a missed point with this later one.
    pending = directory / (nonce + ".request")
    try:
        with pending.open("xb") as file:
            os.chmod(pending, 0o600)
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.link(pending, ready)  # Atomic publication without replacing an old request.
    finally:
        pending.unlink()
    try:
        # The request still carries the original capture deadline. Only the
        # controller can attest that its barrier/native capture finished in it.
        # Its sealed reply can arrive after source release, within this separate
        # bounded window. Do not silently discard that successful publication.
        while time.time() < completion_deadline:
            if reply.exists():
                if reply.is_symlink():
                    raise ValueError("checkpoint controller reply must be physical")
                result = json.loads(reply.read_bytes())
                if result.get("request_sha256") != hashlib.sha256(raw).hexdigest():
                    raise ValueError("checkpoint controller reply belongs to another boundary")
                if result.get("ok") is not True:
                    raise ValueError("controller checkpoint failed: " + str(result.get("error_kind")))
                return result["capture"]
            time.sleep(.05)
        raise TimeoutError("controller did not publish a sealed checkpoint before the completion deadline")
    finally:
        # The controller must reread this exact file under its pause barrier.
        # Once absent, a late controller cannot capture a resumed source.
        ready.unlink()
