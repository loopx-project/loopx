"""Read one solver-visible native result and verify its submitted candidate.

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

_SCOPE_FILE = Path("/etc/loopx-benchmark/native-result-scope.json")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
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
    benchmark_sha256: str
    higher_is_better: bool
    evaluation_context: dict

    def __post_init__(self) -> None:
        object.__setattr__(self, "relay", _endpoint(self.relay))
        if (any(not isinstance(value, str) or _TOKEN.fullmatch(value) is None
                for value in (self.run, self.task, self.goal, self.agent))
                or not isinstance(self.task_sha256, str) or _SHA256.fullmatch(self.task_sha256) is None
                or not isinstance(self.benchmark_sha256, str) or _SHA256.fullmatch(self.benchmark_sha256) is None
                or type(self.higher_is_better) is not bool):
            raise ValueError("Native result scope is invalid")
        context = normalize_evaluation_context(self.evaluation_context)
        conditions = context["evaluation_conditions"]
        if (context["run_id"] != self.run or context["task_id"] != self.task
                or conditions["task_sha256"] != self.task_sha256
                or conditions["benchmark_sha256"] != self.benchmark_sha256
                or (conditions["score_direction"] == "maximize") != self.higher_is_better):
            raise ValueError("Native evaluation conditions differ from the selected task or run")
        object.__setattr__(self, "evaluation_context", context)

    @property
    def evaluation_ref(self) -> str:
        raw = json.dumps(self.evaluation_context["evaluation_conditions"], sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode()
        return "sforge:" + hashlib.sha256(raw).hexdigest()

    def install_bytes(self, judge_url: str) -> bytes:
        endpoint, judge = urlsplit(self.relay), urlsplit(judge_url)
        if (endpoint.scheme, endpoint.netloc) != (judge.scheme, judge.netloc) or judge.path not in ("", "/"):
            raise ValueError("Native relay differs from the configured judge")
        return (json.dumps({**asdict(self), "evaluation_ref": self.evaluation_ref}, sort_keys=True) + "\n").encode("utf-8")


def normalize_evaluation_context(value: object) -> dict:
    """Accept only service-owned immutable grading conditions, not local labels."""
    fields = {"schema_version", "task_id", "run_id", "registration_id", "evaluation_conditions"}
    if (not isinstance(value, dict) or set(value) != fields
            or value.get("schema_version") != "sforge_run_evaluation_context_v1"
            or any(not isinstance(value.get(key), str) or _TOKEN.fullmatch(value[key]) is None
                   for key in ("task_id", "run_id", "registration_id"))):
        raise ValueError("Native run evaluation context is unavailable or invalid")
    conditions = value["evaluation_conditions"]
    hashes = {"task_sha256", "benchmark_sha256", "task_spec_sha256", "native_source_sha256",
              "execution_config_sha256"}
    if (not isinstance(conditions, dict)
            or set(conditions) != hashes | {"schema_version", "judge_image_id", "score_direction", "selection"}
            or conditions.get("schema_version") != "sforge_evaluation_conditions_v1"
            or any(not isinstance(conditions.get(key), str) or _SHA256.fullmatch(conditions[key]) is None
                   for key in hashes)
            or not isinstance(conditions.get("judge_image_id"), str)
            or re.fullmatch(r"sha256:[a-f0-9]{64}", conditions["judge_image_id"]) is None
            or conditions.get("score_direction") not in {"maximize", "minimize"}
            or not isinstance(conditions.get("selection"), str)
            or _TOKEN.fullmatch(conditions["selection"]) is None):
        raise ValueError("Native evaluator conditions are unavailable or invalid")
    return {**value, "evaluation_conditions": dict(conditions)}


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
    if not isinstance(value, dict) or set(value) != set(NativeResultScope.__dataclass_fields__) | {"evaluation_ref"}:
        raise ValueError("Native result scope is invalid")
    reference = value.pop("evaluation_ref")
    scope = NativeResultScope(**value)
    if reference != scope.evaluation_ref:
        raise ValueError("Native evaluation reference differs from its service conditions")
    return scope


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def read_public_result(scope: NativeResultScope, submission_id: str) -> tuple[int, dict]:
    if not _TOKEN.fullmatch(submission_id):
        raise ValueError("A native submission id is required")
    return _read_public_json(f"{scope.relay}/result/{submission_id}?include_receipt=true", _MAX_RESULT)


def read_run_evaluation_context(relay: str) -> dict:
    status, context = _read_public_json(_endpoint(relay) + "/context", _MAX_SCOPE)
    if status != 200:
        raise ValueError("Native run evaluation context is unavailable")
    return normalize_evaluation_context(context)


def _read_public_json(url: str, limit: int) -> tuple[int, dict]:
    request = Request(url,
                      headers={"Cache-Control": "no-cache"}, method="GET")
    # Follow the native path, never an ambient proxy or a redirected host.
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        response = opener.open(request, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        status, raw = response.code, response.read(limit + 1)
    if len(raw) > limit:
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


def _measurement(scope, native, submission_id, *, archive=None, **_legacy_local_evidence):
    from benchmark.runtime.sforge_candidate_binding import native_archive_references

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
    if normalize_evaluation_context(receipt.get("evaluation_context")) != scope.evaluation_context:
        raise ValueError("Native result conditions differ from the installed registration")
    input_revision = "task:" + scope.task_sha256
    binding = native_archive_references(native)
    digest = hashlib.sha256(json.dumps([scope.run, scope.task, submission_id, native],
                                       sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    group = hashlib.sha256(json.dumps([scope.run, scope.task], separators=(",", ":")).encode()).hexdigest()
    result = {
        "candidate_binding": binding,
        "native_measurement": {
            "metric": "native_score",
            "unit": "score",
            "value": score,
            "higher_is_better": scope.higher_is_better,
            "comparison_scope": "native:" + group,
            "input_revision": input_revision,
            "evaluation_ref": scope.evaluation_ref,
            "candidate_ref": binding["archive_ref"],
            "evidence_ref": "result:" + digest,
        },
    }

    if archive is not None:
        try:
            result["candidate_binding"] = native_archive_references(native, archive)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, EOFError, OverflowError,
                tarfile.TarError, zlib.error):
            # Extra content evidence cannot erase the independently sourced measurement,
            # change its subject, or turn the same result into another evaluation.
            result["native_content_evidence_unavailable"] = True
    return result


def formal_measurement(scope, submission_id, *, archive=None) -> dict:
    """Read one existing result; the original service receipt supplies its subject.

    Local execution, retained upload bytes and content-level pairing are optional
    evidence. The exact archive subject and original result reference stay stable.
    """
    status, native = read_public_result(scope, submission_id)
    result = {"native": native, "native_http_status": status, "native_measurement": None}
    if status != 200 or native.get("status") != "completed":
        return result
    try:
        result.update(_measurement(scope, native, submission_id, archive=archive))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, EOFError, OverflowError,
            tarfile.TarError, zlib.error):
        # Native feedback remains available when optional attribution fails.
        result["native_measurement_unavailable"] = True
    return result


def read_candidate_measurement(scope, submission_id, *, archive=None,
                               observation=None, snapshot=None, generated_files=()) -> dict:
    """Compatibility entrypoint; legacy local artifacts no longer gate formal facts."""
    return formal_measurement(scope, submission_id, archive=archive)
