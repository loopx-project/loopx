"""Private native-input IO and optional BPE computation; TS owns attribution.

The host records its own injected packet only when native rollout tracing was
already enabled for that invocation. It never enables tracing globally, edits a
Codex rollout, changes execution, or claims provider token attribution.
"""
from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from typing import Any


def _read(root: Path, artifact: str) -> tuple[dict[str, Any], bytes]:
    path = root / artifact
    if Path(artifact).is_absolute() or ".." in Path(artifact).parts or "\\" in artifact:
        raise ValueError("Input observation reference escapes its source")
    path.resolve(strict=True).relative_to(root.resolve(strict=True))
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Input observation must contain an object")
    return value, raw


def load_codex_input_observation(source_root: Path, artifact: str) -> dict[str, Any]:
    """Transport native ids/digests and tokenizer observations, never raw text.

    o200k_base is an explicitly named estimator. Its use does not establish the
    tokenizer of an unknown/new provider model. Encoding assets may be fetched
    by tiktoken during operator registration; ordinary pack reads never tokenize.
    """
    import tiktoken  # Optional specialized computation provider, not a state owner.

    source, source_bytes = _read(source_root, artifact)
    artifacts = [{"artifact": artifact, "artifact_sha256": hashlib.sha256(source_bytes).hexdigest()}]
    if source.get("schema_version") != "loopx_codex_input_source_v1":
        raise ValueError("Unsupported native input source")
    text = source.get("text")
    if not isinstance(text, str):
        raise ValueError("Native input source text is unavailable")
    bundle = source.get("trace_bundle")
    if not isinstance(bundle, str) or Path(bundle).name != bundle:
        raise ValueError("Invalid native input bundle")
    manifest, manifest_bytes = _read(source_root, f"{bundle}/manifest.json")
    artifacts.append({"artifact": f"{bundle}/manifest.json", "artifact_sha256": hashlib.sha256(manifest_bytes).hexdigest()})
    events_path = source_root / bundle / "trace.jsonl"
    events_path.resolve(strict=True).relative_to(source_root.resolve(strict=True))
    raw_events = events_path.read_bytes()
    artifacts.append({"artifact": f"{bundle}/trace.jsonl", "artifact_sha256": hashlib.sha256(raw_events).hexdigest()})
    encoding = tiktoken.get_encoding("o200k_base")
    source_digest = hashlib.sha256(text.encode()).hexdigest()
    source_tokens = len(encoding.encode_ordinary(text))

    def part(value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("Invalid native input content")
        content = value.get("text")
        if value.get("type") != "input_text" or not isinstance(content, str):
            return {"type": value.get("type")}
        digest = hashlib.sha256(content.encode()).hexdigest()
        return {"type": "input_text", "sha256": digest,
                **({"tokens": source_tokens} if digest == source_digest else {})}

    def payload(ref: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(ref, dict):
            raise ValueError("Invalid native input payload reference")
        relative = ref.get("path")
        if (not isinstance(relative, str) or len(Path(relative).parts) != 2
                or Path(relative).parts[0] != "payloads"):
            raise ValueError("Invalid native input payload reference")
        data, raw = _read(source_root, f"{bundle}/{relative}")
        artifacts.append({"artifact": f"{bundle}/{relative}", "artifact_sha256": hashlib.sha256(raw).hexdigest()})
        return {"body": data, "sha256": hashlib.sha256(raw).hexdigest()}

    events = []
    for line in raw_events.decode().splitlines():
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
            raise ValueError("Invalid native input event")
        event = row["payload"]
        observed = {key: event.get(key) for key in (
            "type", "inference_call_id", "thread_id", "codex_turn_id",
            "model", "response_id", "upstream_request_id",
        )}
        if event.get("type") == "inference_started":
            read = payload(event["request_payload"])
            body = read["body"]
            items = body.get("input")
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ValueError("Invalid native request input")
            for item in items:
                if item.get("content") is not None and not isinstance(item["content"], list):
                    raise ValueError("Invalid native request content")
            observed["request"] = {
                "sha256": read["sha256"], "model": body.get("model"),
                "previous_response_id": body.get("previous_response_id"),
                "input": [{"id": item.get("id"), "type": item.get("type"),
                           "role": item.get("role"),
                           "content": [part(p) for p in (item.get("content") or [])]
                          } for item in items],
            }
        elif event.get("type") == "inference_completed":
            read = payload(event["response_payload"])
            body = read["body"]
            observed["response"] = {"sha256": read["sha256"], **{
                key: body.get(key) for key in ("response_id", "upstream_request_id", "token_usage")
            }}
        events.append({key: row.get(key) for key in (
            "schema_version", "seq", "rollout_id", "thread_id", "codex_turn_id",
        )} | {"payload": observed})
    return {
        "source": {key: source.get(key) for key in (
            "schema_version", "session_id", "goal_id", "source_sha256",
        )} | {"text_sha256": source_digest},
        "source_artifact_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "trace_sha256": hashlib.sha256(raw_events).hexdigest(),
        "manifest": {key: manifest.get(key) for key in (
            "schema_version", "root_thread_id", "rollout_id",
        )},
        "tokenizer": {"name": "tiktoken", "version": version("tiktoken"), "encoding": "o200k_base"},
        "events": events,
        "artifacts": artifacts,
    }
