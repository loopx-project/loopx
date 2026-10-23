# Protocol Action Packet Codex CLI Wrapper v0

> Historical synthetic experiment only. The production packet reader is retired;
> see the [current format contract](protocol-action-packet-decision-v0.md).
> These examples do not qualify current execution or quota-size reduction.

This wrapper is the cold-path bridge between `protocol_action_packet_v0` and a
future Codex CLI summarizer. It is intentionally outside `quota should-run` so
the hot path keeps its interface budget.

The wrapper consumes a synthetic `protocol_router_comparison_v0` report, not a
legacy field from fresh quota output. The [PR-05 migration](protocol-action-packet-decision-v0.md)
keeps it outside the live execution contract. It builds a Codex CLI command
envelope for an isolated project:

```text
codex exec --skip-git-repo-check --ephemeral --ignore-user-config
  --ignore-rules -c 'approval_policy="never"' --sandbox workspace-write
  -C <isolated-fixture-project> <public-safe prompt>
```

The public smoke uses a fake executable to verify the command shape and summary
sidecar contract. It does not invoke real Codex CLI, read environment values,
call direct LLM APIs, run Harbor or Terminal-Bench, start Docker/cloud
sandboxes, use paid compute, read private traces, copy raw session history, or
touch leaderboard paths.

An explicit local probe can opt into real Codex CLI execution:

```bash
python3 examples/protocol/protocol-action-packet-codex-cli-wrapper-smoke.py --real-codex-cli
```

That mode still uses an isolated temporary project, `--ephemeral`,
`--ignore-user-config`, `--ignore-rules`, and the same compact prompt; it
records only sidecar fields such as return code, prompt length, summary, and
stdout/stderr character counts.

The wrapper may become a real Codex CLI cold-path experiment only when the
caller explicitly opts into real execution and keeps the output as a compact
sidecar. Direct LLM API wiring remains deferred until the Codex CLI wrapper or
another cold-path comparison proves a measurable action-clarity gain.
