# New Project Codex Prompt

Use this prompt when you already have:

- a local project folder;
- a project goal document;
- a Codex session with access to that folder.

Replace the placeholders before sending it to Codex.

## CLI Generator

Generate the current handoff prompt locally:

```bash
loopx new-project-prompt \
  --project <PROJECT_ROOT> \
  --goal-doc <GOAL_DOC_PATH>
```

If the project needs a controller that can split scoped sub-agent probes:

```bash
loopx new-project-prompt \
  --project <PROJECT_ROOT> \
  --goal-doc <GOAL_DOC_PATH> \
  --spawn-allowed \
  --allowed-domain docs-map \
  --allowed-domain validation-map \
  --write-scope "docs/**"
```

## Copy-Paste Prompt

Use the CLI output as the setup instructions; it is the maintained prompt owner.
The following request asks Codex to generate and follow that current output:

```text
请将当前项目接入 LoopX。先读取目标文档，再运行：
loopx new-project-prompt --project <PROJECT_ROOT> --goal-doc <GOAL_DOC_PATH>
按本机当前版本生成的 prompt 完成接入、验证和读回。
保留现有 Goal/Agent 身份和授权；接入成功不等于交付完成。
首次接入只登记目标和状态，不生成首连 onboarding todo；先核对候选 Todo，
得到所需确认再开始 delivery。已有授权覆盖的后续动作不重复请示。
```

The generated prompt checks the current guard before autonomous work and follows
`interaction_contract.cli_channel.settlement_plan.ordered_steps` or current
`next_cli_actions` after validation. It preserves exact Turn/effect identities,
actual outcomes, failed/unknown readback, authorization and explicit stop gates.
For recurring execution, generate the host-specific `--bootstrap` loader; do not
save a static execution body or reconstruct refresh/spend from this guide.

Continue a selected outcome through implementation, integration, negative cases
and readback while authorized work remains feasible. Keep recovery evidence and
the next action in existing state; a call, receipt or setup step is not completion.
Notification suppression is independent of admission to work. A missing authority
gates the dependent path; it cannot grant permission to that path or silently
cancel independent admitted work.

Use `configure-goal` for incremental scope changes. Rebuilding with
`bootstrap --force` requires explicit user direction; use `--preserve-todos`
when existing state/Todos must survive. Sub-agent settings only describe the
approved scope; they do not grant spawn permission. Runtime helpers, private
state and generated logs stay out of public artifacts.

## Minimal Command

If the goal is simple and does not need a project-specific adapter yet:

```bash
cd <PROJECT_ROOT>
loopx connect \
  --goal-id <STABLE_GOAL_ID> \
  --objective "<OBJECTIVE_FROM_GOAL_DOC>" \
  --domain <DOMAIN> \
  --goal-doc <GOAL_DOC_PATH>
```

Then inspect:

```bash
loopx registry
loopx status
loopx check --scan-root .
```

## What Good Looks Like

The first connection is successful when:

- the project has a stable `ACTIVE_GOAL_STATE.md`;
- the registry points to that state file;
- the goal appears in `loopx status`;
- the attention queue says exactly who should act next;
- private evidence is kept in the project or local runtime, not in public docs;
- the next Codex tick can continue from saved state instead of re-reading the
  whole conversation.

For larger projects, the first useful adapter is usually read-only. It should
map documents, TODOs, validation surfaces, risks, and handoff packets before it
edits files.
