"""Opt-in native Codex diagnostic IO; no token attribution or authority."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def read_native_trace_file(path: Path, *, tail: bool = False) -> bytes:
    from ..effect_runtime import MAX_REQUEST_BYTES

    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or (metadata.st_size > MAX_REQUEST_BYTES and not tail):
            raise ValueError("native observation is not a bounded regular file")
        offset = max(0, metadata.st_size - MAX_REQUEST_BYTES) if tail else 0
        stream.seek(offset)
        raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("native observation exceeds the transport limit")
    # Only the latest in-flight inference is relevant. Bound IO even for a
    # long-running trace, discarding its cut first row, never its last row.
    return raw.split(b"\n", 1)[-1] if offset else raw


def _canonical_native_json(value: object) -> object:
    # JSON has one number type; native Python projection may spell 1.0
    # where the TS codec spells 1. Preserve booleans and numeric values.
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_canonical_native_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical_native_json(item) for key, item in value.items()}
    return value


def _encoded_native_document(value: object) -> str:
    return json.dumps(_canonical_native_json(value), sort_keys=True, ensure_ascii=False, allow_nan=False)


def _code_mode_source_matches(model_source: object, executed_source: str) -> bool:
    if not isinstance(model_source, str):
        return False
    if model_source == executed_source:
        return True
    # Native code mode removes its optional first-line JSON pragma before
    # recording the executable cell. Preserve the exact remaining source;
    # arbitrary comments, substrings and additional rewrites prove nothing.
    first, newline, body = model_source.partition("\n")
    first = first.lstrip(" \t")
    prefix = "// @exec:"
    if not newline or body != executed_source or not first.startswith(prefix):
        return False
    try:
        options = json.loads(first[len(prefix):])
        json.dumps(options, allow_nan=False)
    except (ValueError, TypeError):
        return False
    # Settings and execution remain native-owned. This only binds the body
    # recorded by the live cell to the model's exact tool call.
    return isinstance(options, dict)


def _native_tool_is_bound(tail: list[dict[str, Any]], completed: list[dict[str, Any]], tools: list[dict[str, Any]], output: dict[str, Any], tool_id: str) -> bool:
    tool = tools[0]["payload"]
    if tool.get("requester") == {"type": "model"}:
        bound = (tool.get("model_visible_call_id") == tool_id
                 and tool.get("code_mode_runtime_tool_id") is None
                 and any(item.get("type") == "function_call" and item.get("call_id") == tool_id
                         and item.get("name") == "exec_command" for item in output.get("output_items", [])))
    else:
        # A code-mode child has no model-visible call id of its own. Bind
        # the live child to its native cell and that cell's exact model
        # output; a child id or a saved source string alone proves nothing.
        requester = tool.get("requester", {})
        cell_id = requester.get("runtime_cell_id")
        cells = [row for row in tail if row["payload"].get("type") == "code_cell_started"
                 and row["payload"].get("runtime_cell_id") == cell_id]
        bound = (requester.get("type") == "code_cell" and isinstance(cell_id, str) and bool(cell_id)
                 and isinstance(tool.get("code_mode_runtime_tool_id"), str)
                 and bool(tool["code_mode_runtime_tool_id"])
                 and tool.get("model_visible_call_id") is None and len(cells) == 1
                 and tail[0]["seq"] < cells[0]["seq"] < tools[0]["seq"]
                 and isinstance(cells[0]["payload"].get("source_js"), str)
                 and isinstance(cells[0]["payload"].get("model_visible_call_id"), str)
                 and not any(row["payload"].get("type") == "code_cell_ended"
                             and row["payload"].get("runtime_cell_id") == cell_id for row in tail)
                 and any(item.get("type") == "custom_tool_call" and item.get("name") == "exec"
                         and item.get("call_id") == cells[0]["payload"].get("model_visible_call_id")
                         and _code_mode_source_matches(item.get("input"), cells[0]["payload"]["source_js"])
                         for item in output.get("output_items", [])))
    return bound


def _model_input_texts(request: dict[str, Any]) -> Iterator[str]:
    for item in request["input"]:
        if item.get("type") == "function_call_output" and isinstance(item.get("output"), str):
            yield item["output"]
        elif item.get("type") == "custom_tool_call_output" and isinstance(item.get("output"), list):
            for part in item["output"]:
                if part.get("type") == "input_text" and isinstance(part.get("text"), str):
                    yield part["text"]


def _read_bundle_payload(bundle: Path, ref: dict[str, Any]) -> dict[str, Any]:
    relative = Path(ref["path"])
    if (relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != "payloads"
            or ".." in relative.parts):
        raise ValueError("native payload reference escapes its bundle")
    path = bundle / relative
    path.resolve(strict=True).relative_to(bundle)
    value = json.loads(read_native_trace_file(path))
    if not isinstance(value, dict):
        raise ValueError("native payload must be an object")
    return value


def _capture_page_range(page: dict[str, Any], *, raw: bytes, digest: str) -> tuple[int, int] | None:
    if page.get("schema_version") != "context_capture_page_v1" or page.get("sha256") != digest:
        return None
    start, content = page.get("offset"), page.get("content")
    if type(start) is not int or start < 0 or not isinstance(content, str) or page.get("ok") is not True:
        return None
    data = content.encode("utf-8")
    end = start + len(data)
    if (page.get("total_bytes") != len(raw) or end > len(raw) or not data
            or page.get("eof") is not (end == len(raw))
            or page.get("next_offset") != (None if end == len(raw) else end)
            or raw[start:end] != data):
        # A host preview can remain valid JSON while shortening content
        # and retaining the original cursor. It proves no bytes.
        return None
    return start, end


def codex_context_in_active_input(observation: dict[str, Any], *, baseline: dict[str, Any], session_id: str) -> bool:
    """Cross-check a host's retention claim against a native in-flight tool call.

    Native trace v1 is an optional, provider-specific IO boundary. Only an
    already enabled trace is read; this neither enables tracing nor modifies a
    session. A saved history, an earlier inference or a completed tool is not
    current-input evidence. Missing/partial/unknown data returns false.

    This observes the input that produced this tool call, not a future model
    dispatch. The caller still owns lifecycle invalidation and same-capture
    recovery. It must not use this function as a standalone continuity lease.
    """
    try:
        if baseline.get("mode") != "full" or baseline.get("target", {}).get("session_id") != session_id:
            return False
        # Python equality conflates booleans and numbers; source observation
        # must retain their JSON types, as the shared projection owner does.
        expected = _encoded_native_document(baseline)
        context = _encoded_native_document(baseline.get("context"))
        bundle = Path(observation["trace_bundle"]).resolve(strict=True)
        manifest = json.loads(read_native_trace_file(bundle / "manifest.json"))
        if (manifest.get("schema_version") != 1 or manifest.get("root_thread_id") != session_id
                or manifest.get("rollout_id") != session_id):
            return False

        raw_events = read_native_trace_file(bundle / "trace.jsonl", tail=True)
        if not raw_events.endswith(b"\n"):
            return False  # a partial writer tail cannot establish the current cursor
        rows = [json.loads(line) for line in raw_events.splitlines()]
        if any(row.get("schema_version") != 1 or type(row.get("seq")) is not int
               or row.get("rollout_id") != session_id for row in rows):
            return False
        if any(a["seq"] >= b["seq"] for a, b in zip(rows, rows[1:])):
            return False
        rows = [row for row in rows if row.get("thread_id") == session_id]
        starts = [i for i, row in enumerate(rows) if row["payload"].get("type") == "inference_started"]
        if not starts:
            return False
        tail = rows[starts[-1]:]
        turn = tail[0]["codex_turn_id"]
        inference = tail[0]["payload"]["inference_call_id"]
        tool_id = observation["tool_call_id"]
        completed = [row for row in tail if row["payload"].get("type") == "inference_completed"
                     and row["payload"].get("inference_call_id") == inference]
        tools = [row for row in tail if row["payload"].get("type") == "tool_call_started"
                 and row["payload"].get("tool_call_id") == tool_id]
        if (len(completed) != 1 or len(tools) != 1
                or not tail[0]["seq"] < min(completed[0]["seq"], tools[0]["seq"])):
            return False
        if completed[0]["seq"] > tools[0]["seq"]:
            # Streaming announces a tool/cell before response completion. The
            # complete model output must still precede this exact live runtime;
            # an announcement or an unfinished inference alone proves nothing.
            runtimes = [row for row in tail if row["payload"].get("type") == "tool_call_runtime_started"
                        and row["payload"].get("tool_call_id") == tool_id]
            if (len(runtimes) != 1
                    or not completed[0]["seq"] < runtimes[0]["seq"]):
                return False
        tool = tools[0]["payload"]
        if tool.get("kind") != {"type": "exec_command"}:
            return False
        if any(row.get("codex_turn_id") != turn or row["payload"].get("type") == "codex_turn_ended"
               or (row["payload"].get("type") in ("tool_call_ended", "tool_call_runtime_ended")
                   and row["payload"].get("tool_call_id") == tool_id)
               for row in tail):
            return False
        output = _read_bundle_payload(bundle, completed[0]["payload"]["response_payload"])
        bound = _native_tool_is_bound(tail, completed, tools, output, tool_id)
        if not bound:
            return False
        request = _read_bundle_payload(bundle, tail[0]["payload"]["request_payload"])
        if request.get("previous_response_id"):
            return False  # implicit server-side history is outside this observer
        raw = read_native_trace_file(Path(observation["capture_file"]))
        digest = hashlib.sha256(raw).hexdigest()
        captured = _encoded_native_document(json.loads(raw))
        if digest != observation["capture_sha256"] or captured not in (expected, context):
            return False
        ranges: list[tuple[int, int]] = []

        for text in _model_input_texts(request):
            # The qualified exec transport wraps complete output after this
            # delimiter. Ignore prose/quoted JSON, previews and other item roles.
            text = text.split("\nOutput:\n", 1)[-1]
            try:
                page = json.loads(text)
                # text(await tools.exec_command(...)) preserves the native
                # result envelope as one text block. Unwrap once, never scan
                # arbitrary quoted prose or use the trace's private result.
                if (isinstance(page, dict) and type(page.get("exit_code")) is int
                        and page["exit_code"] == 0 and isinstance(page.get("output"), str)):
                    page = json.loads(page["output"])
            except ValueError:
                continue
            document = _encoded_native_document(page)
            if document == expected or (captured == context and document == context):
                return True
            if isinstance(page, dict):
                page_range = _capture_page_range(page, raw=raw, digest=digest)
                if page_range is not None:
                    ranges.append(page_range)
        cursor = 0
        for start, end in sorted(ranges):
            if start > cursor:
                return False
            # Reducing page size after truncation changes boundaries. Exact
            # overlapping ranges are safe: each was compared to the immutable
            # capture, not another page or a saved delivery acknowledgement.
            cursor = max(cursor, end)
        return cursor == len(raw)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return False


def record_codex_input_source(
    trace_root: Path, *, session_id: str, goal_id: str, text: str,
    prior_bundles: set[str],
) -> Path | None:
    """Record the actual host injection beside its new native diagnostic bundle.

    Called after native session readback, before decoding the task result. A
    recording failure cannot change the original Host result or its authority.
    Files contain sensitive input and remain inside the opt-in private trace root.
    """
    matches = []
    for bundle in trace_root.glob("trace-*"):
        if bundle.name in prior_bundles or not bundle.is_dir():
            continue
        manifest_path = bundle / "manifest.json"
        manifest_path.resolve(strict=True).relative_to(trace_root.resolve(strict=True))
        manifest = json.loads(manifest_path.read_bytes())
        if not isinstance(manifest, dict):
            raise ValueError("Invalid native trace manifest")
        if (manifest.get("schema_version") == 1
                and manifest.get("root_thread_id") == session_id
                and manifest.get("rollout_id") == session_id):
            matches.append(bundle.name)
    if len(matches) != 1:
        return None
    digest = hashlib.sha256(text.encode()).hexdigest()
    value = {"schema_version": "loopx_codex_input_source_v1", "session_id": session_id,
             "goal_id": goal_id, "trace_bundle": matches[0],
             "source_sha256": digest, "text": text}
    directory = trace_root / "loopx-input-sources"
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.resolve(strict=True).relative_to(trace_root.resolve(strict=True))
    identity = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    path = directory / f"{identity}.json"
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    try:
        with path.open("xb") as output:
            path.chmod(0o600)
            output.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError("Conflicting native input source")
    return path


@contextmanager
def capture_codex_input_source(project: Path, text: str, goal_id: str,
                               session_ids: list[str]) -> Iterator[None]:
    trace_value = os.environ.get("CODEX_ROLLOUT_TRACE_ROOT")
    try:
        root = (project / Path(trace_value).expanduser()).resolve() if trace_value else None
        prior = {path.name for path in root.glob("trace-*")} if root else set()
    except (OSError, ValueError, RuntimeError):
        root, prior = None, set()
    yield
    if root is not None and len(session_ids) == 1:
        try:
            record_codex_input_source(root, session_id=session_ids[0], goal_id=goal_id,
                                      text=text, prior_bundles=prior)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            # Optional diagnostics must not alter native execution or settlement.
            pass
