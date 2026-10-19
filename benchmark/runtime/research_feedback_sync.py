"""Deliver native feedback through ordinary Explore writeback before Todo resume.

This is the EdgeBench producer adapter. Explore owns its attachment codec and
history, Todo owns waiting, and the current Host owns subsequent admission.
No score comparison, candidate adoption or research completion is inferred here.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path

from .layout import _CONTROL


FEEDBACK_ROOT = f"{_CONTROL}/native-feedback"
_OWNER_ENV_KEYS = frozenset({"LOOPX_CLI", "LOOPX_REGISTRY", "LOOPX_RUNTIME_ROOT", "LOOPX_GOAL_ID",
    "LOOPX_AGENT_ID", "LOOPX_PROJECT", "LOOPX_RESEARCH_FEEDBACK_ROOT",
    "LOOPX_RESEARCH_FEEDBACK_AGENT", "LOOPX_PYTHON", "LOOPX_USAGE_PING", "PATH", "PYTHONPATH"})


def feedback_owner_environment(env):
    """Only control coordinates and staged tooling enter the installed config."""
    return {key: value for key, value in env.items() if key in _OWNER_ENV_KEYS}


def feedback_environment(root: Path, base=None):
    """Use the installed, credential-free owner when a tool filters its env."""
    env = dict(os.environ if base is None else base)
    owner = json.loads((root / "owner.json").read_text(encoding="utf-8"))
    if (not isinstance(owner, dict) or set(owner) - _OWNER_ENV_KEYS
            or any(not isinstance(value, str) for value in owner.values())
            or owner.get("LOOPX_RESEARCH_FEEDBACK_ROOT") != str(root)):
        raise ValueError("native feedback owner configuration mismatch")
    env.update(owner)
    return env


def _attachment(packet, record, *, observation=None, probe_ref=None):
    # Stable native identity survives delayed feedback and a lost writeback
    # response. Identity includes the exact released result digest, never the
    # current workspace. A closed native observation is not silently revised.
    source = [packet["run_id"], packet["task_id"], record["submission_id"],
        record["round_id"], record["archive_sha256"], record["result_sha256"], probe_ref]
    identity = hashlib.sha256(json.dumps(source, separators=(",", ":")).encode()).hexdigest()
    evidence = [record["result_ref"], "native-archive:" + record["archive_sha256"]]
    summary = {"status": record["status"], **record.get("result_summary", {})}
    attachment = {
        "schema_version": "explore_result_attachment_v0",
        "node_id": "native-feedback-" + identity[:32],
        "question": "What did the released native evaluation report for this accepted snapshot?",
        "applicability": "One native accepted snapshot; local probe measurements cover only their declared files and input.",
        "input_revision": "native-submission:" + identity[:32],
        "observation": json.dumps(summary, sort_keys=True, allow_nan=False),
        "interpretation": "The result belongs to the archived submission. It does not describe newer workspace files. "
            "Candidate adoption, rollback and the next experiment require the research owner's separate decision; "
            "negative or error results remain evidence, not automatic research failure.",
        "status": "tentative",
        "evidence_refs": evidence,
    }
    if observation is not None:
        attachment["execution_observation"] = observation
        attachment["input_revision"] = observation["input_revision"]
        attachment["evidence_refs"] = sorted(set([*evidence, *observation["evidence_refs"]]))
    return identity, attachment


def write_feedback_finding(env, packet, record, todo_id):
    """Write an actual observer result; never settle the model's original Turn.

The caller completes the finite observation Todo only after this readback. Each
attachment has a deterministic ordinary writeback identity, so a partial copy,
lost response or repeated native history read can finish without a second result.
"""
    from .research_feedback_wait import FEEDBACK_OBSERVER_AGENT
    from .research_submission import bind_execution_observations
    from benchmark.edgebench.research_feedback import write_bytes_atomic
    from loopx.capabilities.explore.result_writeback import record_observer_result_attachment

    root = Path(env["LOOPX_RESEARCH_FEEDBACK_ROOT"])
    attached = [row for row in bind_execution_observations(root, packet)
                if row["submission_id"] == record["submission_id"]]
    variants = [(None, None), *[(row["execution_observation"], row["probe_ref"]) for row in attached]]
    receipts = []
    for observation, probe_ref in variants:
        identity, attachment = _attachment(packet, record, observation=observation, probe_ref=probe_ref)
        directory = root / "ingestion"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (identity + ".json")
        encoded = json.dumps(attachment, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
        try:
            write_bytes_atomic(path, encoded.encode(), create_only=True)
        except FileExistsError:
            if path.read_text(encoding="utf-8") != encoded:
                raise ValueError("native feedback ingestion identity changed")
        # Native observation is external evidence, not a model Turn. The
        # capability's ordinary log/link owner records it without a synthetic
        # quota receipt, stride progress or acceptance declaration.
        result = record_observer_result_attachment(attachment,
            registry_path=Path(env["LOOPX_REGISTRY"]), runtime_root=Path(env["LOOPX_RUNTIME_ROOT"]),
            goal_id=env["LOOPX_GOAL_ID"],
            agent_id=env.get("LOOPX_RESEARCH_FEEDBACK_AGENT", FEEDBACK_OBSERVER_AGENT),
            todo_id=todo_id, source_id="native-feedback:" + identity,
            recorded_at=record["result_observed_at"])
        if result.get("ok") is not True:
            raise RuntimeError("native feedback Explore writeback is incomplete")
        receipts.append({"observation_id": identity, "written": True})
    return {"submission_id": record["submission_id"], "findings": receipts}


@contextmanager
def _consumer_lock(root):
    directory = root / "ingestion"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "consumer.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # The runner retries incomplete consumption. Never wait behind a
            # second consumer while holding any Goal/provider authority lock.
            raise RuntimeError("native_feedback_consumer_busy") from None
        yield


def _publish_feedback_wake(env, root):
    from .research_feedback_wait import _is_feedback_todo, _loopx, _scope
    from benchmark.edgebench.research_feedback import canonical_json, write_bytes_atomic

    goal, agent, observer = _scope(env)
    listed = _loopx(env, "todo", "list", "--goal-id", goal, "--role", "agent")
    completed = sorted((todo["target_key"], todo["todo_id"]) for todo in listed["todos"]
        if todo.get("status") == "done" and _is_feedback_todo(todo, observer, agent))
    # Current canonical completions, including ones absent from an older
    # packet, own this hint. No publication journal or new admission authority
    # is needed to recover a crash between Todo completion and notification.
    revision = hashlib.sha256(canonical_json(completed)).hexdigest()
    encoded = canonical_json({"revision": revision})
    path = root / "ingestion" / "wake.json"
    try:
        with path.open("rb") as existing:
            if existing.read(len(encoded) + 1) == encoded:
                return revision
    except FileNotFoundError:
        pass
    write_bytes_atomic(path, encoded)
    return revision


def synchronize_native_feedback(env, *, packet=None):
    """Consume runner/manual feedback once, then hint a fresh scheduler read."""
    from .research_feedback_wait import synchronize_feedback_waits, wait_for_feedback
    from .research_submission import declared_wait_packet, read_packet, synchronize_intents

    root = Path(env["LOOPX_RESEARCH_FEEDBACK_ROOT"])
    # Lock order: consumer, the short submission journal transaction, then
    # individual ordinary Todo/Explore calls. No provider lock encloses this.
    with _consumer_lock(root):
        packet = read_packet(root) if packet is None else packet
        intents = synchronize_intents(root, packet)

        def released(_observed):
            # Publish each durable success before another result can fail.
            _publish_feedback_wake(env, root)

        try:
            result = synchronize_feedback_waits(env, packet, after_release=released)
            waiting = []
            for intent in intents:
                if (intent.get("wait_todo_id") and intent.get("acceptance") == "accepted"
                        and intent.get("submission_id") and not intent.get("result_observed")):
                    declaration = declared_wait_packet(root, intent)
                    current = [row for row in declaration["records"]
                               if row.get("submission_id") == intent["submission_id"]]
                    if (len(current) == 1 and current[0].get("status") in {"completed", "error"}
                            and current[0].get("attribution_status") == "verified"
                            and current[0].get("result_ref")):
                        # Released evidence must not re-defer completed or
                        # repurposed work after a lost native result receipt.
                        continue
                    waiting.append(wait_for_feedback(env, todo_id=intent["wait_todo_id"],
                        submission_id=intent["submission_id"], packet=declaration))
        finally:
            # Repair a lost notification even when its Todo was already done
            # before this retry, or a later independent result still fails.
            revision = _publish_feedback_wake(env, root)
        return {**result, "waits": waiting, "revision": revision}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
        default=os.environ.get("LOOPX_RESEARCH_FEEDBACK_ROOT", FEEDBACK_ROOT))
    args = parser.parse_args(argv)
    env = feedback_environment(args.root)
    print(json.dumps(synchronize_native_feedback(env), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
