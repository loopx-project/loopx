"""Controlled local Host: finite program search through real LoopX CLI owners.

This is a deterministic experiment actor, not a model or an implementation of
LoopX's choice policy. It only reads training data; the owner verifier is a
separate program and receives withheld cases after a candidate is deployed.
"""
from __future__ import annotations

from hashlib import sha256
from itertools import islice, product
import json
from pathlib import Path
import random
import subprocess
import sys
import traceback


ROLES = ("retained", "parameter", "structural")


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def simplify(op, left, right):
    if isinstance(left, int) and isinstance(right, int):
        return {"+": lambda: left + right, "-": lambda: left - right, "*": lambda: left * right}[op]()
    if op == "+" and left == 0:
        return right
    if op in ("+", "-") and right == 0:
        return left
    if op == "*" and (left == 0 or right == 0):
        return 0
    if op == "*" and left == 1:
        return right
    if op == "*" and right == 1:
        return left
    if op in ("+", "*") and encoded(left) > encoded(right):
        left, right = right, left
    return [op, left, right]


def programs(role, budget, seed):
    incumbent = simplify("+", "x", "y")
    if role == "retained":
        yield from (incumbent for _ in range(budget))
        return
    if role == "parameter":
        # A fixed representation: a*x + b*y + c. No input interaction is
        # available in this family, regardless of the parameter order.
        coefficients = list(product(range(-2, 3), repeat=3))
        random.Random(seed).shuffle(coefficients)
        for a, b, c in coefficients:
            yield simplify("+", simplify("+", simplify("*", a, "x"), simplify("*", b, "y")), c)
        return
    assert role == "structural"
    atoms = ["x", "y", -1, 0, 1]
    random.Random(seed).shuffle(atoms)
    by_size, seen = {1: atoms}, set()
    for size in (1, 3, 5):
        if size > 1:
            by_size[size] = [simplify(op, left, right)
                for left_size in range(1, size - 1, 2)
                for left in by_size[left_size] for right in by_size[size - 1 - left_size]
                for op in ("+", "-", "*")]
        for program in by_size[size]:
            key = encoded(program)
            if key not in seen:
                seen.add(key)
                yield program


def evaluate(program, x, y):
    if isinstance(program, int):
        return program
    if isinstance(program, str):
        return {"x": x, "y": y}[program]
    op, left, right = program
    a, b = evaluate(left, x, y), evaluate(right, x, y)
    return {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b}[op]()


def size(program):
    return 1 if not isinstance(program, list) else 1 + size(program[1]) + size(program[2])


def render(program):
    return str(program) if not isinstance(program, list) else f"({render(program[1])}{program[0]}{render(program[2])})"


def cli(request, *arguments, expected=0):
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", request["registry"],
        "--runtime-root", request["runtime"], "--format", "json", *arguments],
        cwd=request["project"], capture_output=True, text=True, check=False)
    assert result.returncode == expected, (arguments, result.stdout, result.stderr)
    return json.loads(result.stdout)


def actor(request):
    return ("--goal-id", request["goal_id"], "--agent-id", request["agent_id"])


def guard(request, turn, *, todo=True):
    return cli(request, "quota", "should-run", *actor(request), "--codex-app", "--scan-path", request["project"],
        "--turn-instance-id", turn, *(("--todo-id", request["todo_id"]) if todo else ()))


def vision(request, evidence, *, outcome="continue"):
    return {"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": request["objective"]},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": outcome,
            "prior_assumption": "The retained program may not express the observed relation.",
            "observed_reality": "The bounded experiment produced an attributable result; withheld acceptance remains independent.",
            "retained": ["Original owner acceptance and the best measured program."], "evidence_refs": [evidence]}}


def refresh(request, turn, packet, *, obligation=None):
    path = Path(request["project"]) / (turn + "-vision.json")
    path.write_text(json.dumps(packet))
    # The guard reconciles this exact existing Todo into the same Turn. Its
    # receipt retains the due review; a planning recommendation alone is not a
    # new replan-settlement identity or a reason to manufacture another Todo.
    binding = ("--todo-id", request["todo_id"])
    result = cli(request, "refresh-state", *actor(request), *binding, "--turn-instance-id", turn,
        "--classification", "comparison_measured_work", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--delivery-boundary", "semantic_closeout" if obligation else "in_flight_continuation",
        "--agent-vision-json", str(path), "--no-global-sync", "--suppress-external-sinks")
    cli(request, "quota", "spend-slot", *actor(request), *binding, "--turn-instance-id", turn,
        "--slots", "1", "--source", "heartbeat", "--execute", "--scan-path", request["project"])
    return result


def probe(request, role):
    turn = "comparison-probe-" + role
    admitted = guard(request, turn)
    assert admitted["should_run"] and admitted["selected_todo"]["todo_id"] == request["todo_id"], admitted
    assert not admitted.get("autonomous_replan_obligation"), admitted
    training = json.loads(Path(request["training"]).read_text())
    trace = []
    for program in islice(programs(role, request["budget"], request["seed"]), request["budget"]):
        predicted = [evaluate(program, row["x"], row["y"]) for row in training]
        trace.append({"program": program, "predicted": predicted,
            "squared_error": sum((prediction - row["observed"]) ** 2 for prediction, row in zip(predicted, training))})
    assert len(trace) == request["budget"]
    best = min(trace, key=lambda row: (row["squared_error"], size(row["program"]), encoded(row["program"])))
    result = {"role": role, "training_sha256": sha256(Path(request["training"]).read_bytes()).hexdigest(),
        "score_calls": len(trace), "sample_evaluations": len(trace) * len(training), "trace": trace, "best": best}
    artifact = Path(request["project"]) / ("experiment-" + role + ".json")
    artifact.write_text(encoded(result))
    evidence = "experiment:" + sha256(artifact.read_bytes()).hexdigest()
    packet = vision(request, evidence)
    packet["path_delta"]["observed_reality"] = (
        f"The {role} family measured {render(best['program'])} with squared error "
        f"{best['squared_error']} in {len(trace)} score calls; withheld performance is still unknown.")
    packet["explore_result"] = {"schema_version": "explore_result_attachment_v0", "node_id": "comparison-" + role,
        "question": "Can this bounded search family express the observed relation?",
        "applicability": "Only the recorded training cases and the common scoring budget; heldout outcome is unknown.",
        "input_revision": result["training_sha256"], "status": "confirmed" if best["squared_error"] == 0 else "refuted",
        "observation": f"Best program {render(best['program'])} has squared error {best['squared_error']} in {len(trace)} score calls.",
        "interpretation": "Compare the measured programs before adopting one; an unsuccessful representation may need a structural alternative.",
        "evidence_refs": [evidence]}
    written = refresh(request, turn, packet)
    assert written["explore_result_delivery"]["ok"], written
    return {"result": result, "writeback": written}


def propose(request):
    pending = guard(request, "comparison-review", todo=False)
    assert pending["heartbeat_receipt"]["settlement_binding_owed"] is True, pending
    admitted = guard(request, "comparison-review")
    assert admitted["heartbeat_receipt"]["settlement_identity"]["todo_id"] == request["todo_id"], admitted
    obligation = admitted["autonomous_replan_obligation"]
    contract = admitted["replan_action_packet"]["stride_control"]
    current = cli(request, "explore", "turn-context", *actor(request), "--result-limit", "20")
    findings = {row["finding_id"]: row for row in current["graph"]["writeback_results"]}
    artifacts = {}
    for role in ROLES:
        path = Path(request["project"]) / ("experiment-" + role + ".json")
        artifacts["experiment:" + sha256(path.read_bytes()).hexdigest()] = json.loads(path.read_text())
    eligible = []
    for card in contract["explore_basis"]["comparison_candidates"]:
        for outcome in card["outcomes"]:
            measured = next((artifacts[ref] for ref in findings[outcome["finding_id"]]["evidence_refs"] if ref in artifacts), None)
            if measured:
                eligible.append(({"todo_id": card["todo_id"], **outcome}, measured))
    assert len(eligible) == 3, eligible
    choice, result = min(eligible, key=lambda pair: (pair[1]["best"]["squared_error"], size(pair[1]["best"]["program"]), ROLES.index(pair[1]["role"])))
    comparison = "; ".join(f"{item['role']} {render(item['best']['program'])}: SSE {item['best']['squared_error']}"
        for _, item in sorted(eligible, key=lambda pair: ROLES.index(pair[1]["role"])))
    decision = json.loads(json.dumps(contract["minimal_example"]))
    decision.update(basis="useful_new_route" if result["role"] != "retained" else "supported_progress",
        action="switch" if result["role"] != "retained" else "verify")
    decision["axes"] = {
        "work_slice": {"plan": "Deploy and validate the measured program.", "reason": "Training alone leaves acceptance open."},
        "route_distance": {"plan": "Use " + render(result["best"]["program"]) + " from the selected comparison.", "reason": "Measured loss determines retention or change."},
        "validation_scope": {"plan": "Execute the pinned verifier on the deployed module.", "reason": "Withheld inputs check transfer."}}
    decision["next_probe"] = {"scope": "One deployment and independent validation.", "prediction": "The program transfers to withheld inputs.",
        "adoption_criterion": "Original owner verifier succeeds on deployed code.",
        "cost_bound": "One implementation Turn.", "minimum_evidence": "Actual source, experiment provenance, acceptance receipt."}
    decision["explore_review"] = {"revision": contract["explore_basis"]["revision"], "disposition": "used",
        "considered_refs": [pair[0]["finding_id"] for pair in eligible], "selected_candidate": choice,
        "reason": "Compare measured loss and representation size; retain the incumbent on equal evidence."}
    packet = vision(request, choice["finding_id"], outcome="replan")
    packet["stride_decision"], packet["todo_delta"] = decision, ["retain:" + choice["todo_id"]]
    packet["path_delta"]["observed_reality"] = comparison + ". Each family used 125 score calls and 750 sample evaluations; heldout outcome is unknown."
    packet["path_delta"]["changed"] = [f"Deploy {render(result['best']['program'])} from {result['role']}, then execute the unchanged owner verifier."]
    return {"obligation_id": obligation["obligation_id"], "vision": packet, "selected_result": result}


def adopt(request):
    proposal = propose(request)
    written = refresh(request, "comparison-review", proposal["vision"], obligation=proposal["obligation_id"])
    assert written["autonomous_replan_ack"]["semantic_delta"]["accepted"], written
    # Reread the committed source, then execute its selected artifact. A submitted
    # proposal or a context card alone is not counted as implementation.
    recorded = json.loads(Path(written["json_path"]).read_text())["agent_vision"]
    selected = recorded["stride_decision"]["explore_review"]["selected_candidate"]
    assert selected == proposal["vision"]["stride_decision"]["explore_review"]["selected_candidate"]
    from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
    control = GoalModeMCPControlPlane(GoalModeMCPConfig(server_name="comparison-fixture", runtime_profile="generic_cli", legacy_host_surface="claude_code"),
        lambda: {"goal_id": request["goal_id"], "agent_id": request["agent_id"], "registry": request["registry"]})
    control.command_prefix = lambda: [sys.executable, "-m", "loopx.cli", "--runtime-root", request["runtime"]]
    admitted = json.loads(control.should_run())
    assert admitted["should_run"] and admitted["selected_todo"]["todo_id"] == selected["todo_id"], admitted
    artifact = Path(request["project"]) / ("experiment-" + proposal["selected_result"]["role"] + ".json")
    result = json.loads(artifact.read_text())
    module = Path(request["project"]) / "predictor.py"
    module.write_text("import json,sys\n\ndef predict(x,y):\n    return " + render(result["best"]["program"]) +
        "\n\nif __name__ == '__main__':\n    print(json.dumps([predict(*pair) for pair in json.load(sys.stdin)]))\n")
    training = json.loads(Path(request["training"]).read_text())
    execution = subprocess.run([sys.executable, str(module)], input=json.dumps([[row["x"], row["y"]] for row in training]),
        capture_output=True, text=True, check=True)
    deployed = {"selected_candidate": selected, "experiment_sha256": sha256(artifact.read_bytes()).hexdigest(),
        "program": result["best"]["program"], "role": result["role"], "module_sha256": sha256(module.read_bytes()).hexdigest(),
        "training_predictions": json.loads(execution.stdout)}
    target = Path(request["project"]) / "deployed.json"
    target.write_text(encoded(deployed))
    evidence = "deployed:" + sha256(target.read_bytes()).hexdigest()
    terminal = vision(request, evidence, outcome="no_change")
    terminal["state"] = "no_followup"
    terminal["path_delta"]["observed_reality"] = f"Deployed {render(result['best']['program'])} from {result['role']}; actual training execution agrees. Completion will execute the unchanged independent verifier."
    completed = json.loads(control.complete_task(selected["todo_id"], request["agent_id"], evidence, no_follow_up=True, agent_vision=terminal))
    assert completed["ok"], completed
    verified = cli(request, "goal-acceptance", "verify", "--goal-id", request["goal_id"], "--execute")
    assert verified["goal_acceptance_contract"]["status"] == "accepted", verified
    # Publish the measured adoption through the existing evidence owner after
    # actual verification. The shipped Explore view already links this node to
    # the original Todo and shows the finding beside its completion state.
    adoption = cli(request, "explore", "finding", *actor(request), "--node", selected["node_id"],
        "--title", f"Adopted {render(result['best']['program'])} after independent acceptance.", "--status", "confirmed",
        "--summary", proposal["vision"]["path_delta"]["observed_reality"].replace("heldout outcome is unknown", "the deployed source now passed the original heldout verifier")
            + " Selection used measured loss and program size, retaining the incumbent on ties. This finite result does not establish live model creativity or broader utility.",
        "--evidence-ref", evidence, "--evidence-ref", selected["finding_id"], "--tag", "comparison-adoption")
    return {"proposal": proposal, "review": written, "deployment": deployed, "completion": completed,
        "verification": verified, "adoption_finding": adoption}


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    try:
        if request["stage"] == "probe":
            result = probe(request, request["role"])
        elif request["stage"] == "propose":
            result = propose(request)
        else:
            assert request["stage"] == "adopt"
            result = adopt(request)
    except Exception:
        Path(request["response"]).write_text(json.dumps({"error": traceback.format_exc()}))
        raise
    Path(request["response"]).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
