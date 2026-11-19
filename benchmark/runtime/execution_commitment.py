"""Read a scoped commitment observation from the selected native controller.

No clock, scheduler or continuation policy lives here. The native provider
supplies execution evidence; Core owns Goal acceptance and subsequent work.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


def validate_feedback_endpoint(endpoint: str) -> str:
    url = urlsplit(endpoint)
    if (
        url.scheme not in {"http", "https"}
        or not url.netloc
        or url.query
        or url.fragment
        or url.username
        or url.password
        or not url.path.startswith("/api/v1/feedback/")
        or not url.path.removeprefix("/api/v1/feedback/").strip("/")
    ):
        raise ValueError("A scoped native feedback endpoint is required")
    return endpoint.rstrip("/")


def observe(task_id: str, endpoint: str | None = None) -> bool | None:
    endpoint = validate_feedback_endpoint(
        endpoint if endpoint is not None else os.environ.get("SFORGE_FEEDBACK_URL", "")
    )
    if not task_id:
        raise ValueError("A native task identity is required")
    request = Request(
        endpoint.rstrip("/") + "/execution-commitment",
        headers={"Cache-Control": "no-cache"},
    )
    # Native judge traffic already has its own admitted network boundary. Do
    # not forward its scoped relay capability through the model API proxy.
    with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
        data = response.read(8193)
    if len(data) > 8192:
        raise ValueError("Execution observation is oversized")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Execution observation must be an object")
    observed = value.get("observed_at_epoch")
    if (
        value.get("schema_version") != "sforge_execution_commitment_observation_v0"
        or value.get("task_id") != task_id
        or type(observed) not in (int, float)
        or not math.isfinite(observed)
        or abs(time.time() - observed) > 30
        or value.get("fulfilled") is not None
        and type(value["fulfilled"]) is not bool
    ):
        raise ValueError(
            "Execution observation is unavailable or belongs to another task"
        )
    return value["fulfilled"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("probe", "check"))
    parser.add_argument("--task-id", required=True)
    parser.add_argument(
        "--feedback-url", help="Host-pinned native relay; not model API routing"
    )
    args = parser.parse_args(argv)
    try:
        fulfilled = observe(args.task_id, args.feedback_url)
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"available": False}))
        return 1
    if args.action == "probe":
        print(json.dumps({"available": fulfilled is not None}))
        return 0 if fulfilled is not None else 1
    print(json.dumps({"commitment_fulfilled": fulfilled is True}))
    return 0 if fulfilled is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
