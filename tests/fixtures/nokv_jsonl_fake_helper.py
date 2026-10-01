from __future__ import annotations

import base64
import json
import sys


mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
blob: bytes | None = None
generation: int | None = None


def emit(value: object) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


first = json.loads(sys.stdin.readline())
emit({"request_id": first["request_id"], "status": "ready"})

for line in sys.stdin:
    request = json.loads(line)
    request_id = request["request_id"]
    if mode == "unresponsive":
        continue
    if mode == "disconnect":
        sys.stderr.write("provider-private-diagnostic=must-not-escape\n")
        sys.stderr.flush()
        raise SystemExit(17)
    if mode == "invalid":
        emit({"request_id": request_id, "status": "loaded", "generation": True})
        continue
    if mode == "oversized":
        emit({"request_id": request_id, "status": "missing", "padding": "x" * 4_096})
        continue
    operation = request["operation"]
    if operation == "store_identity":
        emit(
            {
                "request_id": request_id,
                "status": "available",
                "store_identity": f"nokv:{request['workbench']}:{'a' * 32}",
            }
        )
    elif operation == "read_blob":
        if blob is None:
            emit({"request_id": request_id, "status": "missing"})
        else:
            emit(
                {
                    "request_id": request_id,
                    "status": "loaded",
                    "bytes_base64": base64.b64encode(blob).decode("ascii"),
                    "generation": generation,
                }
            )
    elif operation == "cas_publish_blob":
        if request["expected_generation"] != generation:
            emit(
                {
                    "request_id": request_id,
                    "status": "conflict",
                    "current_generation": generation,
                }
            )
        else:
            blob = base64.b64decode(request["bytes_base64"], validate=True)
            generation = (generation or 0) + 1
            emit(
                {
                    "request_id": request_id,
                    "status": "applied",
                    "generation": generation,
                }
            )
    else:
        emit(
            {
                "request_id": request_id,
                "status": "failed",
                "reason_code": "unknown_operation",
                "reason": "unknown operation",
            }
        )
