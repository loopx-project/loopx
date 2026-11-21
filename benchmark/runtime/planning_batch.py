"""Apply one initial planning proposal through the existing LoopX owners."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from .planning import task_plan_packet


def planning_batch_guidance() -> str:
    command = shlex.join([sys.executable, "-m", "benchmark.runtime.planning_batch"])
    return (
        "Initial plan batch (optional for an empty work and requirements frontier). "
        "After following the planner, save a JSON proposal alongside "
        "LOOPX_PLANNING_RESULT with input_digest from "
        "the checkpoint and expected_provider_revision from "
        "goal_acceptance.provider_revision. Set outcome to the "
        "declaration's objective, requirements and source. Set todos to the "
        "ordered list of objects with text, action_kind and optional target_key. "
        f"Run {command} <proposal.json> and return the successful planning result. Keep the unchanged "
        "proposal on interruption and rerun the same command. For existing work, "
        "requirement revisions or blockers, follow the ordinary planning steps.\n\n"
    )


def _record(value, required: set[str], optional: set[str] | frozenset[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or not required <= value.keys() <= required | optional:
        raise ValueError("Planning proposal has missing or unsupported fields")
    return value


def _text(value) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("Planning proposal requires non-empty text")
    return value.strip()


def _request(proposal: dict) -> dict:
    _record(proposal, {"input_digest", "expected_provider_revision", "outcome", "todos"})
    outcome = _record(proposal["outcome"], {"objective", "requirements", "source"})
    source = _record(outcome["source"], {"ref", "summary"})
    requirements, todos = outcome["requirements"], proposal["todos"]
    if not all(isinstance(items, list) and items for items in (requirements, todos)):
        raise ValueError("Planning proposal needs at least one requirement and one Todo")
    requirements = [
        {key: _text(value) for key, value in _record(item, {"id", "description"}).items()}
        for item in requirements
    ]
    todos = [
        {key: _text(value) for key, value in _record(todo, {"text", "action_kind"}, {"target_key"}).items()}
        for todo in todos
    ]
    if len({item["id"] for item in requirements}) != len(requirements):
        raise ValueError("Planning requirement IDs must be unique")
    if len({todo["text"] for todo in todos}) != len(todos):
        raise ValueError("Planning Todos must be distinct")
    return {
        "input_digest": _text(proposal["input_digest"]),
        "expected_provider_revision": _text(proposal["expected_provider_revision"]),
        "outcome": {
            "objective": _text(outcome["objective"]),
            "requirements": requirements,
            "source": {key: _text(value) for key, value in source.items()},
        },
        "todos": todos,
    }


def _outcomes(acceptance: dict) -> list[dict]:
    return acceptance["goal_acceptance_contract"].get("outcome_requirements", {}).get("outcomes", [])


def _declared_outcome(acceptance: dict, declaration: dict) -> dict:
    state = acceptance["goal_acceptance_contract"].get("outcome_requirements", {})
    if state.get("current_lifetime") is not True:
        raise ValueError("Planning declaration belongs to a different Goal lifetime")
    for row in _outcomes(acceptance):
        if row["id"] == declaration["outcome_id"]:
            requirements = {item["id"]: item["description"] for item in row["requirements"]}
            expected = {item["id"]: item["description"] for item in declaration["requirements"]}
            if (row["owner_agent_id"] == declaration["owner_agent_id"]
                    and row["objective"] == declaration["objective"]
                    and requirements == expected):
                return row
            break
    raise ValueError("Planning declaration is missing or changed; inspect the current Goal")


def apply_initial_plan(env: dict[str, str], proposal: dict) -> dict:
    if env.get("LOOPX_TASK_STAGE") != "plan":
        raise ValueError("Planning batch requires the runner's planning stage")
    proposal = _request(proposal)
    from .worker import loopx_command

    cli = loopx_command(env)
    packet = task_plan_packet(env, cli)
    if proposal["input_digest"] != packet["input_digest"]:
        raise ValueError("Planning input changed")
    revision = proposal["expected_provider_revision"]
    acceptance = packet["goal_acceptance"]
    if packet["goal_waiting_on"] or packet["blocking_todo_ids"]:
        raise ValueError("Use the ordinary planning path for a Goal wait or blocker")

    def digest(value) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    # Bind all Todo intent to the declaration receipt. Two different proposals
    # for one input cannot replay the same declaration and mix their Todo writes.
    operation = f"benchmark-plan:{packet['input_digest']}"
    declaration = {
        **proposal["outcome"],
        "outcome_id": f"benchmark-plan-{digest(proposal)[:32]}",
        "owner_agent_id": packet["agent_id"],
    }
    if acceptance["provider_revision"] == revision:
        if packet["existing_todos"] or _outcomes(acceptance):
            raise ValueError("Use the ordinary planning path for existing work or requirements")
    else:
        _declared_outcome(acceptance, declaration)

    goal = ["--goal-id", packet["goal_id"]]
    actor = ["--agent-id", packet["agent_id"]]

    def call(*args: str) -> dict:
        process = subprocess.run(
            [*cli, *args], cwd=env["LOOPX_PROJECT"], env=env, text=True,
            capture_output=True, timeout=120,
        )
        try:
            result = json.loads(process.stdout)
        except ValueError:
            raise ValueError(f"LoopX {args[0]} {args[1]} returned no JSON receipt") from None
        if not isinstance(result, dict):
            raise ValueError(f"LoopX {args[0]} {args[1]} returned an invalid receipt")
        if process.returncode or result.get("ok") is not True:
            reason = result.get("error_code") or result.get("error") or "unknown"
            raise ValueError(f"LoopX {args[0]} {args[1]} failed: {reason}")
        return result

    with tempfile.TemporaryDirectory(prefix="loopx-planning-") as directory:
        document = Path(directory) / "refinement.json"

        def refine(value: dict, expected: str, effect: str) -> None:
            document.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            call("goal-acceptance", "refine", *goal, *actor, "--document", str(document),
                 "--expected-provider-revision", expected, "--operation-id", effect, "--execute")

        refine(declaration, revision, f"{operation}:declare")
        ids = []
        for index, item in enumerate(proposal["todos"]):
            written = call(
                "todo", "add", *goal, "--project", env["LOOPX_PROJECT"],
                "--role", "agent", "--claimed-by", packet["agent_id"],
                "--task-class", "advancement_task",
                *(f"--{key.replace('_', '-')}={value}" for key, value in item.items()),
                "--operation-id", f"{operation}:todo:{index}", "--execute",
            )
            todo_id = written.get("todo_id")
            if not isinstance(todo_id, str) or not todo_id or todo_id in ids:
                raise ValueError("Planning writer did not return distinct real Todo IDs")
            ids.append(todo_id)

        # Resolve the binding revision and check actual runnable ownership. A
        # historical add receipt can be valid even if its Todo changed later.
        current_packet = task_plan_packet(env, cli)
        if current_packet["input_digest"] != packet["input_digest"]:
            raise ValueError("Planning input changed before binding")
        if not set(ids).issubset(current_packet["runnable_todo_ids"]):
            raise ValueError("Planning Todos are no longer runnable by this agent")
        if current_packet["goal_waiting_on"] or current_packet["blocking_todo_ids"]:
            raise ValueError("Planning encountered a new Goal wait or blocker")
        current = current_packet["goal_acceptance"]
        linked = _declared_outcome(current, declaration)["todo_ids"]
        if linked and linked != ids:
            raise ValueError("Planning work links changed; inspect the current Goal")
        if not linked:
            basis = current["provider_revision"]
            refine({"outcome_id": declaration["outcome_id"], "todo_ids": ids},
                   basis, f"{operation}:bind:{digest(basis)[:32]}")
    return {"input_digest": packet["input_digest"], "status": "ready", "todo_ids": ids}


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m benchmark.runtime.planning_batch PROPOSAL.json", file=sys.stderr)
        return 2
    try:
        proposal = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        result = apply_initial_plan(dict(os.environ), proposal)
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
