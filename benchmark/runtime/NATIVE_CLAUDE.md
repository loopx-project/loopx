# Native Claude Code research runner

`benchmark.edgebench.claude_run` runs an independent file-submission trial using
SForge's `ClaudeCodeAgent`. Claude Code owns tools, sessions and Stop hooks;
SForge owns submission, grading, the total deadline and `--continue` recovery.
There is no LoopX Goal/Todo runtime or implicit relationship to another arm.
The ordinary benchmark runner and existing experiments are unchanged.

The transport installs an operator-supplied native Claude binary with an explicit
SHA-256 and version. It reuses SForge's native Stop hook. Both that script and
`sforge-submit` receive explicit agent-readable executable modes, even when the
operator runs with umask 077. The Docker transport assigns the uploaded task
prompt to the agent with mode 0600, including when host and container UIDs
differ; unrelated uploads retain their original permissions.
Python belongs here as an existing provider
transport, not a second control-plane decision owner.

Select a clean runner revision and freeze the SForge source with the existing
source-admission workflow. Prepare an isolated judge, task directory, log
directory, run identity and resource admission before invoking the runner.
Verify judge connectivity from both the host and container: SForge maps
`host.docker.internal` to loopback for host registration. A judge listening only
on the Docker bridge therefore needs its reachable bridge address as the URL:

```bash
# Supply ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN through private environment
# configuration. The endpoint must implement Anthropic Messages for the model.
python -m benchmark.edgebench.claude_run \
  --tasks-dir "$TASKS" --log-dir "$LOGS" --task "$TASK" --run-id "$RUN" \
  --runner-revision "$RUNNER_SHA" \
  --sforge-revision "$PROVIDER_SHA" --judge-url "$JUDGE_URL" \
  --claude-binary "$CLAUDE_BINARY" --claude-sha256 "$CLAUDE_SHA256" \
  --claude-version "$CLAUDE_VERSION" --model "$MODEL" --effort max \
  --timeout 21600 --submission-cooldown 1800
```

The default worker resources are 2 CPUs / 4 GiB; judge resources are 1 CPU /
2 GiB. These limits do not establish host capacity or authorize oversubscription.
The command does not start a judge, launch a cohort or mutate another run.
The provider's native Stop hook continually requests work until its outer
deadline; do not install it in ordinary personal Claude settings. Disable this
experiment by stopping its own launcher, using the provider's normal cleanup.

An Anthropic-compatible gateway is required for a non-Claude model. A model name
and successful HTTP response alone do not qualify compatibility. Before a long
run, observe the outgoing model and effort, verify streamed tool calls/results,
Stop continuation, exact-session resume and a real native submission/result.
Keep raw trajectories, credentials and synthetic run evidence outside Git.

Claude's native `/goal` and Plan mode can be tested separately with the same
endpoint. `/goal` adds a model-evaluated completion condition; Plan mode restricts
editing. This runner does not silently enable either. The adapter pins the
small-model aliases, including the Goal evaluator, to the selected model, and
passes the requested effort on both initial and resumed invocations. Provider
translation and usage/cache accounting still need live verification.

Validation uses `python -m pytest benchmark/tests/test_native_claude.py`. Set
`LOOPX_EDGEBENCH_DOCKER_SMOKE=1` for the disposable Docker upload/permission
case (requires a local `python:3.12-slim` image). That test uses a synthetic
binary; real CLI/model, Goal/Plan, submission and stability qualification are
separate, explicitly authorized operational checks. Short checks do not prove
long-horizon reliability or benchmark improvement.
