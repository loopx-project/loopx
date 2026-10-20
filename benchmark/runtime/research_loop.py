"""Retain the legacy research recipe while ordinary runs select their own scope."""
from __future__ import annotations


def runtime_execution_guidance(env: dict[str, str]) -> str:
    """Expand only the selected producer or the unchanged legacy prompt."""
    from .research_probe import execution_observation_guidance, resolve_execution_observation

    legacy = env.get("LOOPX_RESEARCH_LOOP") == "1"
    producer = resolve_execution_observation(
        env.get("LOOPX_EXECUTION_OBSERVATION"), research_loop=legacy,
    )
    if legacy:
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
    matches = [todo for todo in listed["todos"] if todo.get("target_key") == "research-continuation"]
    if len(matches) > 1:
        raise ValueError("research continuation identity is ambiguous")
    if matches:
        current = matches[0]
        if current.get("claimed_by") != runtime.agent_id:
            raise ValueError("research continuation belongs to another Agent")
        return current  # Never reopen completed work or clear a real wait on preparation.
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
    await runtime._loopx(environment, ["todo", "update", "--goal-id", runtime.goal_id,
        "--agent-id", runtime.agent_id, "--todo-id", initial,
        "--successor-todo-id", research["todo_id"], "--execute"], cwd=cwd)
    await runtime._loopx(environment, ["refresh-state", "--goal-id", runtime.goal_id,
        "--agent-id", runtime.agent_id, "--classification", "research_scope_initialized",
        "--delivery-batch-scale", "single_surface", "--delivery-outcome", "surface_only",
        "--vision-state", "active", "--vision-advancement-policy", "as_needed",
        "--vision-summary", "Build a strong initial candidate, then execute evidence-driven improvements while retaining the best validated artifact.",
        "--vision-acceptance", "Candidate validity settles its stage, not the original research objective. Respect user Stop, original budget and genuine blockers; justify research closeout from remaining useful authorized work.",
        "--no-global-sync", "--suppress-external-sinks"], cwd=cwd)
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
