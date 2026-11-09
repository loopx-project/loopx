"""Read one solver-visible native result and bind it to current research evidence.

This benchmark adapter owns no scoring, admission, Goal mutation, or decision.
The installed run scope comes from the native runner before the solver starts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tarfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import zlib

from benchmark.runtime.layout import _LOOPX_RUNTIME, _REGISTRY

_SCOPE_FILE = Path("/etc/loopx-benchmark/native-result-scope.json")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SHA = re.compile(r"[a-f0-9]{40}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_MAX_SCOPE = 8192
_MAX_RESULT = 8 * 1024 * 1024


def _endpoint(url: str) -> str:
    if not isinstance(url, str) or any(char.isspace() for char in url):
        raise ValueError("Native feedback endpoint is invalid")
    address = urlsplit(url)
    if (address.scheme not in {"http", "https"} or not address.hostname
            or address.query or address.fragment or address.username is not None
            or address.password is not None
            or re.fullmatch(r"/api/v1/feedback/[A-Za-z0-9_-]{1,128}/?", address.path) is None):
        raise ValueError("Native feedback endpoint is invalid")
    return url.rstrip("/")


@dataclass(frozen=True)
class NativeResultScope:
    relay: str
    run: str
    task: str
    goal: str
    agent: str
    task_sha256: str
    scorer_commit: str
    higher_is_better: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "relay", _endpoint(self.relay))
        if (any(not isinstance(value, str) or _TOKEN.fullmatch(value) is None
                for value in (self.run, self.task, self.goal, self.agent))
                or not isinstance(self.task_sha256, str) or _SHA256.fullmatch(self.task_sha256) is None
                or not isinstance(self.scorer_commit, str) or _SHA.fullmatch(self.scorer_commit) is None
                or type(self.higher_is_better) is not bool):
            raise ValueError("Native result scope is invalid")

    def install_bytes(self, judge_url: str) -> bytes:
        endpoint, judge = urlsplit(self.relay), urlsplit(judge_url)
        if (endpoint.scheme, endpoint.netloc) != (judge.scheme, judge.netloc) or judge.path not in ("", "/"):
            raise ValueError("Native relay differs from the configured judge")
        return (json.dumps(asdict(self), sort_keys=True) + "\n").encode("utf-8")


def read_installed_scope(path: Path | None = None) -> NativeResultScope:
    """Reject a replaced, writable, non-regular, or non-root-owned descriptor."""
    route = path or _SCOPE_FILE
    with os.fdopen(os.open(route, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("Native result scope is not owned by the runner")
        raw = stream.read(_MAX_SCOPE + 1)
    if len(raw) > _MAX_SCOPE:
        raise ValueError("Native result scope is oversized")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != set(NativeResultScope.__dataclass_fields__):
        raise ValueError("Native result scope is invalid")
    return NativeResultScope(**value)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def read_public_result(scope: NativeResultScope, submission_id: str) -> tuple[int, dict]:
    if not _TOKEN.fullmatch(submission_id):
        raise ValueError("A native submission id is required")
    request = Request(f"{scope.relay}/result/{submission_id}?include_receipt=true",
                      headers={"Cache-Control": "no-cache"}, method="GET")
    # Follow the native path, never an ambient proxy or a redirected host.
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        response = opener.open(request, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        status, raw = response.code, response.read(_MAX_RESULT + 1)
    if len(raw) > _MAX_RESULT:
        raise ValueError("Native result is oversized")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        if status != 200:
            return status, {}
        raise
    if not isinstance(value, dict):
        if status != 200:
            return status, {}
        raise ValueError("Native result is not a JSON object")
    return status, value


def _measurement(scope, native, submission_id, *, observation, snapshot, archive, generated_files,
                 todo_id, local_finding_id, registry_path, runtime_root):
    from benchmark.runtime.sforge_candidate_binding import verify_binding_from_native
    from loopx.agent_registry import require_registered_agent_id
    from loopx.capabilities.explore.result_log import (
        build_explore_result_projection, explore_result_log_path, load_explore_result_events_strict,
    )
    from loopx.control_plane.todos.list_readback import list_goal_todos

    receipt, report = native.get("receipt"), native.get("report")
    if not isinstance(receipt, dict) or not isinstance(report, dict):
        raise ValueError("The native result lacks source evidence")
    score = report.get("score")
    if (native.get("submission_id") != submission_id or receipt.get("task_id") != scope.task
            or report.get("submission_id") != submission_id or report.get("task_id") != scope.task
            or native.get("error") is not None or report.get("valid") is not True
            or report.get("timed_out") is not False or type(score) not in (int, float)
            or not math.isfinite(score)):
        raise ValueError("The native result is not a valid outcome for this task")
    require_registered_agent_id(registry_path=registry_path, goal_id=scope.goal, agent_id=scope.agent)
    read = list_goal_todos(registry_path=registry_path, runtime_root_arg=str(runtime_root),
                           goal_id=scope.goal, agent_id=scope.agent, role="agent", status="open", todo_id=todo_id)
    task = read.get("todo")
    if (read.get("ok") is not True or not isinstance(task, dict) or task.get("todo_id") != todo_id
            or task.get("status") != "open" or task.get("claimed_by") != scope.agent):
        raise ValueError("The selected Todo is not available to this worker")
    events = load_explore_result_events_strict(explore_result_log_path(runtime_root, scope.goal),
                                              goal_id=scope.goal)
    history = [row for row in events
               if row.get("event_kind") == "finding" and row.get("result_id") == local_finding_id]
    view = build_explore_result_projection(history, goal_id=scope.goal, finding_limit=1)
    findings = [item for item in view["findings"] if item["status"] == "confirmed"
                and item["node_id"] in task.get("explore_result_node_refs", [])]
    if len(findings) != 1:
        raise ValueError("The local finding is not linked to the selected Todo")
    binding = verify_binding_from_native(observation, snapshot, native, archive, generated_files)
    expected = {binding["candidate_ref"], binding["submitted_file_set_ref"]}
    candidates = {item["candidate_ref"] for item in findings[0].get("measurements", [])
                  if item["candidate_ref"] in expected and item["input_revision"] == binding["input_revision"]
                  and item["value"] is not None}
    if not candidates:
        raise ValueError("The finding does not measure the submitted candidate")
    candidate = (binding["submitted_file_set_ref"] if binding["submitted_file_set_ref"] in candidates
                 else binding["candidate_ref"])
    digest = hashlib.sha256(json.dumps([scope.run, scope.task, submission_id, native],
                                       sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    group = hashlib.sha256(json.dumps([scope.run, scope.task], separators=(",", ":")).encode()).hexdigest()
    return {
        "todo_id": todo_id,
        "node_id": findings[0]["node_id"],
        "local_finding_id": local_finding_id,
        "binding": binding,
        "native_measurement": {
            "metric": "native_score",
            "unit": "score",
            "value": score,
            "higher_is_better": scope.higher_is_better,
            "comparison_scope": "native:" + group,
            "input_revision": "task:" + scope.task_sha256,
            "evaluation_ref": "sforge:" + scope.scorer_commit,
            "candidate_ref": candidate,
            "evidence_ref": "result:" + digest,
        },
    }


def read_candidate_comparison(scope, submission_id, *, observation, snapshot, archive, generated_files,
                              todo_id, local_finding_id, registry_path=None, runtime_root=None) -> dict:
    status, native = read_public_result(scope, submission_id)
    result = {"native": native, "native_http_status": status, "comparison": None}
    if status != 200 or native.get("status") != "completed":
        return result
    try:
        result["comparison"] = _measurement(
            scope, native, submission_id, observation=observation, snapshot=snapshot, archive=archive,
            generated_files=generated_files, todo_id=todo_id, local_finding_id=local_finding_id,
            registry_path=registry_path or Path(_REGISTRY), runtime_root=runtime_root or Path(_LOOPX_RUNTIME),
        )
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, EOFError, OverflowError,
            tarfile.TarError, zlib.error):
        # Native feedback remains available even when optional pairing is
        # unavailable. Do not copy exception paths or evaluator details here.
        result["comparison_unavailable"] = True
    return result
