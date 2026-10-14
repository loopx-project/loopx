"""SForge checkpoint read transport, included in the existing worker payload."""
from __future__ import annotations

import json
import hashlib
import math
import time
from urllib.request import Request, urlopen
from pathlib import Path

SCHEMA = "sforge_checkpoint_opportunity_v0"  # Local provider artifact, not a LoopX authority contract.
ROUTE = "/api/v1/checkpoint/opportunity"
ARCHIVE_ROUTE = "/api/v1/checkpoint/incumbent"


def capture_opportunity(env, *, timeout, incumbent_path: Path):
    """Read only this worker's registration; retain its original deadline."""
    deadline = float(env["LOOPX_PHASE_DEADLINE_EPOCH"])
    if not math.isfinite(deadline) or deadline <= time.time() or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("checkpoint requires an open original trial deadline")
    stop = min(deadline, time.time() + timeout)
    request = Request(env["SFORGE_JUDGE_URL"].rstrip("/") + ROUTE,
        data=json.dumps({"token": env["SFORGE_TOKEN"]}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=max(.1, stop - time.time())) as response:
        packet = json.load(response)
    if (packet.get("schema_version") != SCHEMA or packet.get("execution_authority_granted") is not False or
            packet.get("outstanding_evaluations") != 0):
        raise ValueError("native opportunity checkpoint readback is unavailable")
    if "native_incumbent" not in packet or not packet.get("capture_binding_sha256"):
        raise ValueError("native opportunity checkpoint omits incumbent coverage")
    incumbent = packet["native_incumbent"]
    if incumbent is not None:
        if (not isinstance(incumbent, dict) or type(incumbent.get("archive_bytes")) is not int or
                incumbent["archive_bytes"] < 0 or not isinstance(incumbent.get("archive_sha256"), str)):
            raise ValueError("native incumbent descriptor is invalid")
        incumbent_path = Path(incumbent_path)
        if not incumbent_path.is_absolute() or incumbent_path != incumbent_path.resolve():
            raise ValueError("incumbent destination must be a normalized physical private file")
        if time.time() >= stop:
            raise ValueError("checkpoint capture window closed before incumbent transport")
        request = Request(env["SFORGE_JUDGE_URL"].rstrip("/") + ARCHIVE_ROUTE,
            data=json.dumps({"token": env["SFORGE_TOKEN"],
                             "capture_binding_sha256": packet["capture_binding_sha256"]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        created = False
        try:
            with urlopen(request, timeout=max(.1, stop - time.time())) as response:
                # Never overwrite a prior participant or follow its symlink.
                with incumbent_path.open("xb") as target:
                    created = True
                    incumbent_path.chmod(0o600)
                    digest, size = hashlib.sha256(), 0
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > incumbent["archive_bytes"] or time.time() >= stop:
                            raise ValueError("native incumbent transport exceeded its bound")
                        digest.update(chunk)
                        target.write(chunk)
            if size != incumbent["archive_bytes"] or digest.hexdigest() != incumbent["archive_sha256"]:
                raise ValueError("native incumbent bytes differ from the captured result")
        except Exception:
            if created:
                incumbent_path.unlink()
            raise
    observed = time.time()
    if observed >= stop:
        if incumbent is not None:
            incumbent_path.unlink()
        raise ValueError("checkpoint capture window closed during opportunity transport")
    return {**packet, "source_trial_deadline_epoch": deadline,
            "client_observed_at_epoch": observed,
            "remaining_trial_seconds": max(0, deadline - observed),
            "continuation_qualified": False}
