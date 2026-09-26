# LoopX Rename Migration

Status: historical Goal Harness rename plan. The current default paths and
explicit legacy migration are documented in
[Local State Path Migration](local-state-path-migration.md).

LoopX is the canonical product name and `loopx` is the canonical CLI command.
The rename is intentionally fail-fast at the product surface: new installs and
generated prompts expose only `loopx`, so old command names cannot silently hide
missed migration work.

## Canonical Contract

- Product name: `LoopX`.
- CLI command: `loopx`.
- Legacy CLI command: none. `goal-harness` is not installed as an alias.
- Python package/import: `loopx`.
- Local project state: `.loopx/registry.json`.
- Global runtime state at the time of this rename: `~/.codex/loopx`.
- Skill names: `loopx-project`, `loopx-pr-review`, `loopx-doc-registry`, and
  `loopx-self-repair`.

## Migration Todo Batches

1. P0 canonical contract: document the no-legacy-alias surface and rename
   package/import/state/skill namespaces.
2. P0 CLI/install migration: install only `loopx`, update package metadata,
   installers, and install smokes.
3. P0 public docs migration: move the root README, getting-started path, and
   packaged install docs to LoopX language.
4. P0 state migration SOP: ship the explicit one-shot
   [`migrate-state`](loopx-state-migration-sop.md) path for existing local
   users. This is not a legacy CLI compatibility alias; it is an auditable
   dry-run-first import into `.loopx` and `~/.codex/loopx`.
5. P1 external surface release gate: update package publish metadata, hosted
   Pages URLs, external docs, and any release notes that cannot be validated
   until the canonical code rename has landed.
6. P1 GitHub rename gate: rename `huangruiteng/goal-harness` to
   `huangruiteng/loopx` after the canonical rename PR lands, the maintainer
   accepts the public URL migration plan, and the Pages/issue-template cutover
   PR is merged. The no-clone installer already points at the new repo URL, so
   the GitHub rename is a controlled release cutover, not a compatibility
   alias.

## GitHub Rename Gate

The current authenticated GitHub user has admin permission on the existing
`huangruiteng/goal-harness` repository. Codex may perform the final rename to
`huangruiteng/loopx` after the maintainer explicitly approves the repo rename
gate and the cutover checklist below is complete.

Do not rename the repository as part of an ordinary code PR. Before the rename:

- merge the LoopX canonical rename PR;
- confirm the no-clone installer works from the new URL;
- decide the hosted Pages URL strategy;
- update Pages base paths and public GitHub links to `/loopx/`;
- update repository description/topics;
- update local remotes after rename with `git remote set-url origin`;
- keep the old repository name unused so GitHub redirects are not invalidated.

GitHub redirects renamed repository web and git traffic, including clone, fetch,
and push operations, but repository Pages URLs are an exception and GitHub
Actions that use an action from a renamed repository do not follow the rename.
That makes Pages and action-consumer references explicit checklist items.

## Validation Matrix

Required before the rename PR is considered ready:

- `python3 -m py_compile $(find loopx examples -name '*.py' -print)`
- `python3 examples/install-local-smoke.py`
- `python3 examples/codex-cli-packaged-install-smoke.py`
- `python3 examples/fresh-clone-quickstart-smoke.py`
- `python3 examples/state-migration-smoke.py`
- `python3 examples/docs-governance-smoke.py`
- `loopx check --scan-root .`
- `git diff --check`

Self-use validation should install from the working tree into an isolated home,
run `loopx doctor`, bootstrap a temporary project, and confirm quota/status
work through `.loopx` and `~/.codex/loopx`.
