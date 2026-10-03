# Validation, compatibility, and safety

A Dev Book is complete only when readers can reproduce its paths, recognize success, recover from failure,
and avoid crossing authority boundaries. This chapter closes project onboarding and developer
contributions with one validation model. Extension packages use separate lifecycle checks, but remain one
developer-contribution path.

## What you should learn

After this chapter, you should be able to:

- choose the right validation layer for onboarding and different contribution surfaces;
- distinguish compatibility, readiness, and domain correctness;
- scan private state before publication;
- route content to the book, official documentation, or project sources of truth.

## Four validation layers

### 1. Artifact validation

Check that files and schemas are self-consistent:

- Markdown builds;
- internal links resolve;
- JSON and TOML parse;
- requests and responses satisfy JSON Schema;
- fixtures can be reset from scratch.

Book:

```bash
python3 -m pip install -r docs/requirements-docs.txt
python3 examples/dev-book-publication-smoke.py
mkdocs build --strict
```

The LoopX monorepo publishes the site through MkDocs Material. Dependency ranges live in
`docs/requirements-docs.txt`; `examples/dev-book-publication-smoke.py` guards the Book navigation,
bilingual routes, official homepage entrypoints, and the no-Labs boundary. After a dependency change:

```bash
python3 -m pip check
mkdocs build --strict
```

Do not stop at parsing Markdown in isolation. Verify discoverability through the unified `mkdocs.yaml`
navigation, the GitHub Pages base path, and the homepage Learn route.

Standalone Extension example from this book:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e './standalone-extension[test]'
python3 -m pytest standalone-extension
```

### 2. Product-surface validation

Check that the tutorial uses real release surfaces:

```bash
loopx --version
loopx doctor
loopx doctor --deep
loopx start-goal --help
loopx capability list --format json
loopx extension --help
```

Command existence is not complete workflow validation. Host automation, visible Goal, and Extension
activation each need their own readback. `doctor --deep` also starts and probes the TypeScript Effect
runtime packaged with the release. A successful return through a migration-time Python facade does not by
itself prove the TypeScript semantic owner, runtime decoder, and durable effect path.

### 3. Lifecycle validation

Project onboarding should prove:

- reconnect reuses state;
- status finds the active Goal;
- local state is ignored by Git;
- Host activation is observable;
- quota and selected Todo agree.

An Extension or package-lifecycle contribution should prove:

- package entrypoint resolution;
- doctor success without effects;
- revision-bound install state;
- disabled Extensions cannot run;
- enable reruns doctor;
- invalid requests fail closed;
- a failed upgrade preserves the current revision.

Control Plane, Capability, Provider, Host or Runner, and projection contributions should prove:

- expected decisions come from an independently reviewed invariant rather than current output;
- unit or contract tests cover positive, negative, and illegal states;
- a focused smoke or public-safe replay exercises the real protocol chain;
- affected consumers such as Agent-facing output, scheduler, and writeback receive the right checks;
- a Capability has a real caller, outcome contract, and Domain State owner;
- a Provider returns bounded observation, effect, and readback without gaining Goal authority;
- a Host or Runner preserves typed request and result boundaries, independent validation, and real runtime
  readback;
- a projection or dashboard consumes a typed public-safe read model without creating browser write
  authority;
- documentation and fixtures bind to a public contract and maintenance trigger instead of copying private
  runtime state;
- `loopx canary premerge --from-git-diff` or an equivalent risk set covers cross-surface changes;
- the PR contains only the product, documentation, and durable validation needed for one protocol result.

### 4. Outcome validation

Finally, validate the reader's result:

- Does an onboarded agent recover from the same canonical state?
- Does a Control Plane or Capability change preserve authority, precedence, replay, and recovery invariants?
- Do Provider and Host paths prove results through real readback and an independent validator?
- Do projections, documentation, and fixtures still point to the same authoritative source?
- Does the Extension return the correct stable domain result?
- Are permissioned actions rejected or routed correctly?
- Can the reader recover when the normal path fails?

## Compatibility is not one version number

An Extension has at least four compatibility layers:

| Layer | Example |
| --- | --- |
| Package | Python version and dependency ranges |
| LoopX API | `requires_loopx_api = ">=1,<2"` |
| Wire protocol | `loopx_text_stats_extension_v0` |
| Domain schema | Request and response schema versions |

A package version upgrade must not silently change the meaning of an existing schema. A breaking wire
contract needs a new protocol or schema version and a caller migration path.

## Scan the public/private boundary

Before a public commit:

```bash
git status --short
git diff --name-only
git ls-files --others --exclude-standard

loopx check \
  --scan-path README.md \
  --scan-path chapters/ \
  --scan-path en/
```

If you generated runnable directories from a book example, scan those paths too:

```bash
loopx check \
  --scan-path README.md \
  --scan-path standalone-extension/
```

Review manually for:

- credentials, tokens, and cookies;
- absolute machine paths;
- `.loopx/`, `.loopx/goals/`, or runtime state;
- raw agent transcripts, trajectories, and verifier output;
- private Issues, internal links, and unredacted organizational narrative;
- temporary probes and generated logs.

`.gitignore` does not replace scanning, and it does not remove an already tracked file.

## Assign documentation authority deliberately

| Content | Authoritative home |
| --- | --- |
| Learning order, concept explanation, recovery model, and scaffold guidance | `loopx-book` |
| Complete CLI arguments, protocols, and release behavior | Official LoopX repository |
| Product code, durable fixtures, and smoke tests | The corresponding LoopX or Extension source repository |
| Current Goal, Todo, Gate, and evidence for a project | Project-local LoopX state |
| Commits, PRs, CI, and external resources | The corresponding external system |

The book keeps only the minimum high-drift command path required to finish a task and points readers to
current `--help` and official documentation.

## Maintenance triggers

After each LoopX minor release, review:

- installer and `doctor`;
- `connect` and `start-goal`;
- Host surface names;
- Codex App heartbeat, Codex CLI Goal activation, and the Runtime Connector Catalog;
- the TypeScript migration RFC's shipped baseline, active phase, and facade exit conditions;
- core protocols, state machines, bounded-context ownership, and the quality catalog;
- Extension manifest, doctor, run, and lifecycle;
- whether book steps still reproduce on the current official scaffold and command surface.

Update theory chapters only when the public contract changes, not when internal files are reorganized.

## Pre-publication checklist

### Book

- [ ] The first viewport identifies the reader, value, and two practice paths.
- [ ] Chinese remains the editorial source of truth and English facts stay aligned.
- [ ] Six foundation chapters cover sessions, Goals, state, work graphs, Turns, recovery, and boundaries.
- [ ] Onboarding covers Codex App and Codex CLI.
- [ ] Developer contributions cover the Control Plane, Capabilities, Providers, Hosts and Runners,
  projections, documentation, and fixtures.
- [ ] Contribution guidance is organized around placement, protocols, invariants, and evidence rather than
  function lists.
- [ ] Extension is presented as a contribution subpath and its example remains reproducible on the current
  official scaffold.
- [ ] `python3 examples/dev-book-publication-smoke.py` passes.
- [ ] `mkdocs build --strict` passes.
- [ ] Internal links and the public-boundary scan pass.
- [ ] The bilingual first screen has owner approval.

Only after these checks should GitHub Pages publish from `main`. Pages is the display surface, not the source
of content truth or LoopX state.
