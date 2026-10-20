"""An explicit benchmark research preset using existing Goal and Todo owners."""
from __future__ import annotations

from uuid import uuid4


def runtime_execution_guidance(env: dict[str, str]) -> str:
    """Select the research preset or independent observation guidance."""
    from .research_probe import execution_observation_guidance, resolve_execution_observation

    research_preset = env.get("LOOPX_RESEARCH_LOOP") == "1"
    producer = resolve_execution_observation(
        env.get("LOOPX_EXECUTION_OBSERVATION"), research_loop=research_preset,
    )
    if research_preset:
        return research_execution_guidance()
    return execution_observation_guidance() if producer is not None else ""


def research_stride_strategy(enabled, strategy):
    """One treatment definition shared by native CLI and programmatic entries."""
    if not enabled:
        return strategy
    if strategy not in (None, "evidence_conditioned_v1"):
        raise ValueError("research_loop requires evidence_conditioned_v1, not a disabled or different review strategy")
    return "evidence_conditioned_v1"


async def prepare_research_continuation(runtime, environment, *, cwd):
    """A candidate stage cannot close a separately retained research outcome.

    The deferred successor contains actual optimization work, not a recurring
    instruction to replan. Its existing resume owner releases it when the
    initial stage is done; budget, Stop and evidence-backed closeout stay native.
    """
    initial = runtime._seeded_todo_id
    if not initial:
        raise ValueError("research continuation requires its initial candidate stage")
    listed = await runtime._loopx(environment, ["todo", "list", "--goal-id", runtime.goal_id,
        "--role", "agent"], cwd=cwd)
    initial_todo = next((todo for todo in listed["todos"] if todo["todo_id"] == initial), None)
    if initial_todo is None:
        raise ValueError("research continuation requires its retained initial candidate stage")
    matches = [todo for todo in listed["todos"] if todo.get("target_key") == "research-continuation"]
    if len(matches) > 1:
        raise ValueError("research continuation identity is ambiguous")
    if matches:
        research = matches[0]
        if research.get("claimed_by") != runtime.agent_id:
            raise ValueError("research continuation belongs to another Agent")
        if (research["todo_id"] in initial_todo.get("successor_todo_ids", [])
                or research.get("status") != "deferred"
                or research.get("resume_when") != "todo_done:" + initial):
            return research  # Never reopen work, clear waits or replace later Vision.
    else:
        research = await runtime._loopx(environment, [
            "todo", "add", "--goal-id", runtime.goal_id, "--role", "agent",
            "--claimed-by", runtime.agent_id, "--task-class", "advancement_task",
            "--action-kind", "benchmark_task", "--target-key", "research-continuation",
            "--status", "deferred", "--resume-when", "todo_done:" + initial,
            "--text", f"[P1] Improve the original research objective in {runtime._task_document} through "
            "complete, evidence-driven experiments. Preserve the best validated candidate. Use released "
            "results and actual resource diagnostics to choose between a local repair and a different "
            "mechanism; retain negative results. Candidate correctness and a successful submission settle "
            "only that candidate stage. Close this research work only with evidence against the original "
            "objective and remaining useful authorized work. Explicit user Stop, the original budget limit "
            "and genuine blockers remain valid stops; do not manufacture work or run merely to fill time.",
            "--execute"], cwd=cwd)
    binding = ["--goal-id", runtime.goal_id, "--todo-id", initial]
    mode = await runtime._loopx(environment, ["handoff-mode", "show", "--goal-id",
        runtime.goal_id], cwd=cwd)
    lease = None
    proof = []
    if mode["handoff_mode"] == "hard_lease":
        observed = await runtime._loopx(environment, ["task-lease", "inspect", *binding], cwd=cwd)
        version = observed["lease"]["version"] if observed.get("lease") else 0
        # A failed update releases this attempt. A retry must acquire a fresh
        # grant, never borrow another execution's proof or reuse a retired key.
        key = "research-preparation-" + uuid4().hex
        acquired = await runtime._loopx(environment, ["task-lease", "acquire", *binding,
            "--owner", runtime.agent_id, "--idempotency-key", key,
            "--expected-version", str(version)], cwd=cwd)
        lease = acquired["lease"]
        proof = ["--task-lease-idempotency-key", key,
            "--task-lease-expected-version", str(lease["version"])]
    try:
        # Publish scope before the edge: an interrupted preparation with a
        # missing edge reuses this Todo, while a complete edge preserves later Vision.
        await runtime._loopx(environment, ["refresh-state", "--goal-id", runtime.goal_id,
            "--agent-id", runtime.agent_id, "--classification", "research_scope_initialized",
            "--delivery-batch-scale", "single_surface", "--delivery-outcome", "surface_only",
            "--vision-state", "active", "--vision-advancement-policy", "as_needed",
            "--vision-summary", "Build a strong initial candidate, then execute evidence-driven improvements while retaining the best validated artifact.",
            "--vision-acceptance", "Candidate validity settles its stage, not the original research objective. Respect user Stop, original budget and genuine blockers; justify research closeout from remaining useful authorized work.",
            "--no-global-sync", "--suppress-external-sinks"], cwd=cwd)
        await runtime._loopx(environment, ["todo", "update", *binding,
            "--agent-id", runtime.agent_id, "--successor-todo-id", research["todo_id"],
            *proof, "--execute"], cwd=cwd)
    finally:
        if lease is not None:
            await runtime._loopx(environment, ["task-lease", "release", *binding,
                "--owner", runtime.agent_id, "--idempotency-key", lease["idempotency_key"],
                "--expected-version", str(lease["version"])], cwd=cwd)
    return research


def research_execution_guidance() -> str:
    return (
        '\nResearch-loop execution evidence:\nThe initial candidate and ongoing research are separate canonical Todos. '
        'Do not close the research successor merely because the candidate passes tests. Preserve the original '
        'objective and existing Stop/budget/blocked conditions. Before the first native submission, run a relevant '
        'local candidate check and retain actual resource observations, including unknown measurements. Use '
        '`loopx-research-probe --help` for the local probe wrapper: it pins explicit candidate files, measures '
        'runtime and enforces the selected local limit. A solver can write search_iterations and stop_reason as JSON '
        'to LOOPX_RESEARCH_DIAGNOSTICS; absent values remain unknown, and solver reports are not independently '
        'verified. This is not another evaluation or a requirement to use every available second. Attach the emitted '
        'execution_observation in the normal Explore result writeback. Preserve its input revision and evidence '
        'refs. Record changed method ids and the reason when actual implementation differs from the proposal. '
        'Resolve this evidence at the next replan.\n'
    )
