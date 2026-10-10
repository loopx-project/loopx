# Project Skill Delivery

LoopX can ship a canonical skill without adding it to every user's global agent
configuration. A release-owned skill opts into project delivery by adding:

```text
skills/<skill-id>/.loopx-skill-scope
```

with the exact content:

```text
project
```

The global installer keeps that source in the versioned LoopX release and skips
global skill installation. A connected project can then install one or more
host-native managed copies:

```bash
loopx project-skill install \
  --project . \
  --skill <skill-id> \
  --surface codex \
  --surface claude-code \
  --surface opencode \
  --execute
```

## Surface Map

| Surface | Project target |
| --- | --- |
| `codex` | `.agents/skills/<skill-id>/` |
| `claude-code` | `.claude/skills/<skill-id>/` |
| `opencode` | `.opencode/skills/<skill-id>/` |
| `pi` | `.pi/skills/<skill-id>/` |

These locations follow the host discovery contracts documented by
[Codex](https://developers.openai.com/codex/skills),
[Claude Code](https://code.claude.com/docs/en/slash-commands#where-skills-live),
and [OpenCode](https://opencode.ai/docs/skills/#place-files).

## Lifecycle

All mutations are preview-first:

```bash
loopx project-skill status \
  --project . \
  --skill <skill-id> \
  --surface codex

loopx project-skill install \
  --project . \
  --skill <skill-id> \
  --surface codex

loopx project-skill uninstall \
  --project . \
  --skill <skill-id> \
  --surface codex
```

Add `--execute` only after reviewing the plan. The managed marker records the
release version, source digest, skill id, and host surface. Install and
uninstall fail closed for unmanaged targets, local modifications, symlink
escapes, missing project connection, or failed digest readback.

Multi-surface installation stages every target before replacement and restores
earlier targets if a later replacement fails. The implementation copies the
whole skill directory instead of linking `SKILL.md`, so supporting scripts,
references, and host metadata remain complete.

Python distributions bundle the canonical `loopx-material` and
`loopx-change-quality` sources, scope markers and host display metadata
alongside the global workflows. Bundling a project skill does not add it to the
global host catalog or activate its capability. Project install, readback and
uninstall use the same release-owned sources in a wheel or source checkout.

## Authority Boundary

Project skill delivery controls discoverability, not domain authority. A
project-local skill cannot create a LoopX goal, todo, write scope, operator
gate, material-store authority, or external permission. Each consumer
capability still declares its own activation and mutation contract.

`loopx-material` is the first consumer: the skill can be visible in a project
while Material Lifecycle remains default-off for every goal that has not
explicitly activated it.

## Default Discovery And Explicit Project Copies

The marker declares default discovery. `project` keeps the source in the
versioned release and skips global installation. `global` keeps a reusable
workflow globally discoverable and also permits an explicitly requested project
copy. Missing or unknown markers are rejected by project delivery; an unmarked
source is not implicitly eligible.

The six packaged workflows (`loopx-project`, `loopx-self-repair`,
`loopx-pr-review`, `loopx-pr-program`, `loopx-doc-registry`, and
`loopx-benchmark`) declare `global`. Connection, repair, review, and shared
workflow instructions must be available before a project installs a local
copy. Their existing task, project, and permission checks still apply.
`loopx-material` and `loopx-change-quality` remain `project` workflows.

`skills/loopx-pr-merge` is deliberately unmarked. The merge-decision workflow is
repo-kept: a host adopts it by reading the repository copy, and it has no global
or project delivery path, so the default install cannot add it to a machine that
never merges LoopX pull requests.

Prefer the global copy for ordinary use. Install a project copy for an isolated
host profile or an intentional project override; this operation never removes
or rewrites a global skill. Hosts may discover both copies, so do not install a
second copy merely to make an already visible workflow available.
