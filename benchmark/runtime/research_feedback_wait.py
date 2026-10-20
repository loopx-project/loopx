"""Adapt an explicit native-feedback dependency to the existing Todo owner.

The native observer owns accepted submission/feedback facts. This transport
only creates its finite observation Todo and defers the caller-selected work.
It never infers a wait from prose, an unchanged score, or another pending job.
The regular quota and Host owners retain all admission and resume decisions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from .layout import _CONTROL


FEEDBACK_OBSERVER_AGENT = "edgebench-feedback-observer"


def _loopx(env, *args):
    from .worker import loopx_command

    result = subprocess.run(loopx_command(env) + list(args), cwd=env["LOOPX_PROJECT"],
        env=env, capture_output=True, text=True, timeout=120, check=False)
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise RuntimeError("native feedback Todo command returned no structured result") from None
    if result.returncode or not isinstance(payload, dict) or payload.get("ok") is False:
        # Native results, credentials and host-local paths do not enter errors.
        code = payload.get("error_code", "todo_command_failed") if isinstance(payload, dict) else "todo_command_failed"
        raise RuntimeError("native feedback Todo command rejected: " + str(code))
    return payload


def _identity(value, label):
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or any(ord(char) < 32 for char in value):
        raise ValueError("native feedback requires a bounded " + label)
    return value


def _records(packet):
    if not isinstance(packet, dict) or packet.get("schema_version") != "edgebench_native_feedback_v1":
        raise ValueError("native feedback packet schema mismatch")
    run, task = (_identity(packet.get(key), key) for key in ("run_id", "task_id"))
    if not isinstance(packet.get("records"), list):
        raise ValueError("native feedback records are unavailable")
    accepted = [record for record in packet["records"]
                if isinstance(record, dict) and record.get("accepted_record") is True]
    identities = [record.get("submission_id") for record in accepted]
    records = {}
    for record in accepted:
        submission = _identity(record.get("submission_id"), "submission_id")
        round_id = _identity(record.get("round_id"), "round_id")
        if identities.count(submission) != 1 or record.get("status") not in {"queued", "running", "completed", "error"}:
            continue  # One unresolved native identity cannot suppress independent feedback.
        key = hashlib.sha256(json.dumps([run, task, submission, round_id], separators=(",", ":")).encode()).hexdigest()
        records[submission] = ("native-feedback:" + key[:32], record)
    return records


def _released(record):
    if record.get("status") not in {"completed", "error"} or record.get("attribution_status") != "verified":
        return False
    if not record.get("result_ref"):
        return False
    _identity(record["result_ref"], "result_ref")
    # Native archive/result identity proves released feedback even when no
    # optional selected-file probe manifest covers that submitted archive.
    for field in ("archive_sha256", "result_sha256"):
        if not isinstance(record.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", record[field]):
            raise ValueError("released native feedback is missing its verified " + field)
    return True


def _scope(env):
    goal = _identity(env.get("LOOPX_GOAL_ID"), "goal_id")
    agent = _identity(env.get("LOOPX_AGENT_ID"), "agent_id")
    observer = _identity(env.get("LOOPX_RESEARCH_FEEDBACK_AGENT", FEEDBACK_OBSERVER_AGENT), "observer_agent_id")
    if agent == observer:
        raise ValueError("native feedback observation requires its separate runner owner")
    return goal, agent, observer


def _is_feedback_todo(todo, observer, agent):
    key = todo.get("target_key")
    return (isinstance(key, str) and re.fullmatch(r"native-feedback:[0-9a-f]{32}", key) is not None
        and todo.get("role") == "agent" and todo.get("claimed_by") == observer
        and todo.get("task_class") == "advancement_task" and todo.get("action_kind") == "observe"
        and agent in (todo.get("excluded_agents") or []))


def _dependency(todos, key, observer, agent):
    matches = [todo for todo in todos if todo.get("target_key") == key]
    if len(matches) > 1:
        raise ValueError("native feedback Todo identity is ambiguous")
    if not matches:
        return None
    todo = matches[0]
    if not _is_feedback_todo(todo, observer, agent):
        raise ValueError("native feedback Todo belongs to another operation")
    return todo


def _create_feedback_todo(env, *, goal, agent, observer, key, record):
    created = _loopx(env, "todo", "add", "--goal-id", goal, "--role", "agent",
        "--claimed-by", observer, "--excluded-agent", agent, "--task-class", "advancement_task",
        "--action-kind", "observe", "--target-key", key, "--operation-id", key + ":observe",
        "--text", "Receive released feedback for native submission " + record["submission_id"]
            + " (" + key + ").",
        "--execute")
    return _loopx(env, "todo", "list", "--goal-id", goal, "--todo-id", created["todo_id"])["todo"]


def wait_for_feedback(env, *, todo_id, submission_id, packet):
    """Declare one exact dependency, preserving unrelated work and its owner.

    Submission/recovery must have returned an accepted native identity before
    this call. A missing or ambiguous response is not authority to guess one.
    Reentry reads canonical state; it neither resubmits nor changes Turn budget.
    """
    goal, agent, observer = _scope(env)
    selected = _records(packet).get(submission_id)
    if selected is None:
        raise ValueError("native feedback wait requires one accepted submission; its identity is absent or ambiguous")
    key, record = selected
    binding = ("--goal-id", goal)
    listed = _loopx(env, "todo", "list", *binding, "--role", "agent")
    source = _dependency(listed["todos"], key, observer, agent)
    waiting = next((todo for todo in listed["todos"] if todo.get("todo_id") == todo_id), None)
    if (waiting is None or waiting.get("role") != "agent" or waiting.get("claimed_by") != agent
            or waiting.get("task_class") != "advancement_task"):
        raise ValueError("native feedback wait requires the caller's advancement Todo")
    if _released(record):
        # A delayed/replayed availability notice never reopens a completed Todo
        # or replaces a newer wait; the current control owner still decides it.
        return {"waiting": False, "todo_id": todo_id, "submission_id": submission_id,
                "result_ref": record["result_ref"]}
    if waiting.get("status") not in {"open", "deferred"}:
        raise ValueError("native feedback wait cannot reopen completed or blocked work")
    resume = "todo_done:" + source["todo_id"] if source else None
    if waiting.get("resume_when") and waiting["resume_when"] != resume:
        raise ValueError("native feedback wait cannot replace another resume condition")
    if waiting.get("status") == "deferred" and (source is None or waiting.get("resume_when") != resume):
        raise ValueError("native feedback wait cannot replace an existing deferral")
    if source is None:
        source = _create_feedback_todo(env, goal=goal, agent=agent, observer=observer, key=key, record=record)
        resume = "todo_done:" + source["todo_id"]
    # Observe again after source creation: feedback or an owner edit may have
    # committed meanwhile. The public CAS binds deferral to this fresh source.
    current = _loopx(env, "todo", "list", *binding, "--role", "agent")
    source = _dependency(current["todos"], key, observer, agent)
    waiting = next(todo for todo in current["todos"] if todo.get("todo_id") == todo_id)
    if source["status"] == "done":
        return {"waiting": False, "todo_id": todo_id, "submission_id": submission_id,
                "dependency_todo_id": source["todo_id"]}
    if source["status"] != "open":
        raise ValueError("native feedback observation was paused or cancelled")
    if waiting.get("status") == "deferred" and waiting.get("resume_when") == resume:
        return {"waiting": True, "todo_id": todo_id, "submission_id": submission_id,
                "dependency_todo_id": source["todo_id"], "resume_when": resume}
    if waiting.get("status") != "open" or waiting.get("resume_when") or waiting.get("claimed_by") != agent:
        raise ValueError("native feedback work changed before wait registration")
    revision = current["authority_read"]["provider_revision"]
    operation = key + ":wait:" + hashlib.sha256((todo_id + "\0" + revision).encode()).hexdigest()[:24]
    _loopx(env, "todo", "update", *binding, "--agent-id", agent, "--todo-id", todo_id,
        "--update-operation-id", operation, "--update-expected-provider-revision", revision,
        "--status", "deferred", "--resume-when", resume,
        "--reason", "This work requires released feedback from its accepted native submission.", "--execute")
    return {"waiting": True, "todo_id": todo_id, "submission_id": submission_id,
            "dependency_todo_id": source["todo_id"], "resume_when": resume}


def synchronize_feedback_waits(env, packet, *, after_release=None):
    """A runner observation releases only its exact existing dependencies.

    A native error or negative scored result can still be usable evidence.
    Unknown attribution stays unknown; receiving a record does not authorize
    adopting a candidate, rolling back files, completing research or resubmitting.
    """
    goal, agent, observer = _scope(env)
    records = _records(packet)
    if not records:
        return {"released": []}
    listed = _loopx(env, "todo", "list", "--goal-id", goal, "--role", "agent")
    completed = []
    for key, record in records.values():
        source = _dependency(listed["todos"], key, observer, agent)
        if source is None:
            source = _create_feedback_todo(env, goal=goal, agent=agent, observer=observer, key=key, record=record)
        if source.get("status") == "done" or not _released(record):
            continue  # An unchanged replay creates no work; unknown is not failure.
        if source.get("status") != "open":
            continue  # A runner does not override an owner pause or cancellation.
        from .research_feedback_sync import write_feedback_finding

        # Publish the existing Explore finding before changing runnable state;
        # otherwise the request guard could admit a replan without its evidence.
        write_feedback_finding(env, packet, record, source["todo_id"])
        result = _loopx(env, "todo", "complete", "--goal-id", goal, "--agent-id", observer,
            "--todo-id", source["todo_id"], "--no-follow-up", "--evidence", record["result_ref"],
            "--note", "Released native feedback is available for this exact submitted snapshot.", "--execute")
        completed.append({"submission_id": record["submission_id"], "dependency_todo_id": source["todo_id"],
                          "result_ref": record["result_ref"], "completed": result.get("ok") is not False})
        if after_release is not None:
            after_release(completed[-1])
    return {"released": completed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    wait = commands.add_parser("wait", help="Defer one Todo until its accepted native submission has released feedback.")
    wait.add_argument("--todo-id", required=True)
    wait.add_argument("--submission-id", required=True)
    commands.add_parser("sync", help="Apply released native feedback through the runner-owned Todo observer.")
    args = parser.parse_args()
    from .research_feedback_sync import feedback_environment, synchronize_native_feedback

    root = Path(os.environ.get("LOOPX_RESEARCH_FEEDBACK_ROOT", _CONTROL + "/native-feedback"))
    env = feedback_environment(root)
    if args.command == "wait":
        packet = json.loads((root / "native.json").read_text(encoding="utf-8"))
        result = wait_for_feedback(env, todo_id=args.todo_id, submission_id=args.submission_id, packet=packet)
    else:
        result = synchronize_native_feedback(env)
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
