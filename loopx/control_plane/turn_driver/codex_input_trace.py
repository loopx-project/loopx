"""Opt-in native Codex diagnostic IO; no token attribution or authority."""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


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
