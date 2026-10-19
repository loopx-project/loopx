# Frontend build and delivery

Chat source lives in `apps/presentation/dashboard`. `loopx/web/chat/` is generated,
ignored output, not a second editable source tree. Feature PRs commit source and
validation only; resolving a source merge followed by a build produces one
coherent HTML/JavaScript/CSS set. Do not concatenate competing HTML entrypoints or
asset-retention manifests when resolving an older PR.

For owner-authorized reads of existing runtimes, shared access lives in
`loopx.control_plane.status.goal_source`. Native status and Explore owners retain
their semantics; this Python IO provider adds neither decisions nor lifecycle
authority. Connection (`local` or a configured `ssh` alias) is independent of
environment (`host` or a container pinned by its full physical id), so a remote
container uses the same reader. It never starts a container, tunnel or worker.

The thin presentation adapter
`python -m loopx.presentation.runtime_workspace --sources <manifest>` serves the
packaged Goal workspace. The Agent prepares a private JSON array of sources:

```json
[
  {
    "id": "research", "label": "Research",
    "connection": {"kind": "local"},
    "environment": {"kind": "host"},
    "scope": {"all_registered": true},
    "registry": "/runtime/registry.json", "runtime_root": "/runtime/state",
    "cli": "/runtime/bin/loopx", "python": "/runtime/bin/python",
    "path": "/runtime/bin:/usr/bin:/bin"
  }
]
```

Use `connection: {"kind": "ssh", "host_alias": "configured-alias"}` for SSH;
the alias must remain in the existing SSH catalog. Use
`environment: {"kind": "container", "container_id": "<full 64-character id>"}`
for a container. `scope: {"goal_id": "original-goal"}` restricts the source to
one Goal; `all_registered` allows the native registered Goal list without copying
its registry. Source paths are absolute in the selected environment. The reader
requires this explicit connection/environment/scope model; there is no alternate
manifest format or implicit scope conversion.

Startup JSON returns working workspace URLs, `access_url` and a source namespace.
The existing source selector offers the prepared readers. Keep these manifests
and actual source captures private. Before staging or updating the PR,
compare every source, validation script and visual-evidence path with the
selected Todo's declared write scopes; place presentation evidence inside the
existing authorized surface rather than assuming all docs/examples are covered.
Build Chat first or supply
`--assets-dir`; stop the adapter to remove its temporary readers.

App, CLI and Agents can use the same loopback read API: `GET /api/goal-source`
describes identity, scope and supported operations; `/goals`, `/status` and
`/history` below it require `source_namespace`, with optional `goal_id` and history
`cursor`. Status is wrapped as `status`; Goal lists and history use `items`.
The CLI client shares this transport:

```sh
python -m loopx.control_plane.status.goal_source goals --endpoint <prepared-status-url>
python -m loopx.control_plane.status.goal_source history --endpoint <prepared-status-url> --goal-id <original-goal>
```

Retain the returned pinned status URL/namespace on subsequent reads, including
history cursors. A stale identity returns an error even if a new reader reuses
the port. The App binds an initial relative `/status.json` entry to its prepared
identity once, preserving ordinary local/SSH behavior outside these readers.
Its existing status/history aliases use this same reader, not separate IO.

These are read transports, not connected execution Goals or a second registry.
Native status and complete validated Explore history stay scoped to the physical
source, including identical Goal ids in different containers. Status has a
two-second per-reader cache; history uses bounded server-local snapshots. A failed
read or removed SSH alias invalidates saved status/pages. Unavailable sources
fail visibly without replacement; refreshing retries.
The main view keeps the selected source's failure and retry visible on narrow
screens, with technical detail collapsed. Other sources' cached Goal contents
are withheld on failure; retry targets the failed selection, not the last
successful reader.
Switching prepared readers clears source-local question and analysis selections;
Back restores the previous reader's URL and its own selection.
By default all mutation endpoints are denied. A read-only source does not imply an
analysis pack, model execution, adoption or validated benefit; analysis remains
unavailable until its existing owner binds the appropriate source pack and the
operator explicitly enables [local analysis](../reference/trajectory-assessment.md#existing-isolated-goal-sources).
That separate local provider uses the original source Goal id and a
source-namespace-isolated store; it creates no Goal mirror or source mutations.
The adapter uses one local Python process with one HTTP listener/thread per
prepared source. Cache misses invoke the source's native status CLI or bounded
log read; no lower CPU, process or IO cost is implied. Local host/container
readback and SSH command composition are separate validation boundaries; a
composed command test does not prove a live remote host/container journey.

## Task-first delivery / 从用户任务开始

Use the existing task/PR delivery brief, not a second checklist or approval form.
Production-bound interactions require packaged task and state readback. For a
prototype or documentation example, compare the proposed journey but label its
simulated boundary instead of claiming that the interaction is installed.
Before implementation, name one ordinary request, its current failure, the
observable result, affected entrypoints and existing state/effect owner. Derive
acceptance from that request, a demonstrated failure and the nearest accepted
product contract, before designing components or copying the current click
sequence into tests. Reuse a relevant, accepted
[Golden Query](../product/use-cases/steward/golden-queries.md) when one covers the
task; proposed or unrelated queries can inform exploration but are not a
mandatory acceptance source. If none applies, the task, failure and owning
contract still define acceptance. Inspect current main and related work first.
A passing implementation-shaped test is not an independent experience oracle.

Caller-facing capabilities require an appropriate shipped frontend journey,
including discoverability, authorized operation, feedback and result readback.
CLI-only prerequisites are partial delivery; catalog text or a command-copy
button is not the journey. Reuse the typed capability owner rather than adding
frontend-only decisions or a setting for a capability with no persistent policy.

Carry the same task through these steps:

1. **Compare the journey.** Write the current and proposed user steps in the
   existing before/after evidence, anchored to the original request rather than
   a configuration task invented from the proposed implementation. Compare
   no change and a smaller lawful route, including preparation the Agent can
   already perform. Separate missing intent or owner-only authorization from
   lookup, identifiers, formatting and other preparation. Count total navigation,
   repeated information, questions, confirmations and recovery effort. Justifying
   each control individually cannot establish that this whole human workflow is
   necessary. Default to one required interaction from the original intent to a usable
   result, or zero additional interactions when the intent and authority already
   exist. Account for every required statement, choice or confirmation, including
   preparation outside the UI; one final button does not make a multi-round task
   one-round. Justify each additional round by a missing fact, consequential
   choice or authority boundary, not feature value alone. Record navigation,
   copying, waiting and recovery separately; voluntary refinement does not raise
   the ordinary-path budget. These are design defaults, not a click-count gate.
   Remove work the Agent can prepare under existing authority and
   reuse prior scoped authorization. This is a comparison, not a universal click
   limit or a ban on legitimate technical configuration. Keep necessary scoped
   consent and explicit effect confirmation;
   make optional refinement optional. A complete conversational draft should not
   require a second form just to repeat the same facts.
2. **Choose the owner and composition.** Reuse the existing typed state and
   action owner and inspect companion App/CLI/messaging paths. Follow
   [design.md](design.md#earn-the-users-attention) for the whole viewport, not
   individual cards. Navigation follows user purpose; clearly show the target
   and source of a setting. Do not expose storage scopes as competing pages or
   list controls that cannot apply to the selected target.
   Inventory repeated scope/method/limitation copy across the whole view. Follow
   [explanations on demand](design.md#explanations-on-demand): keep results and
   consequential qualifiers visible, share optional contextual help by region,
   and reuse existing provenance details instead of adding footnotes to each card.
3. **Implement and challenge.** Use the smallest meaningful regression derived
   from the accepted outcome. Select realistic populated data and one negative
   or recovery path likely to falsify the claim: multiple owners, out-of-order
   timestamps, stale/disconnected state, duplicate click, cancellation or reload.
   Select by the touched risk, not all cases for every patch. Reuse shared TS
   projections; fix the owning selector rather than sorting an already-sorted
   mock or adding a frontend-only source of truth.
4. **Walk the actual product.** Start at the affected user entry, use the built
   packaged surface, inspect the rendered viewport, perform the task and read
   back the result. Validate affected narrow/keyboard/language behavior. Pair
   browser fixtures with the real owning backend when they replace the rule
   being claimed. Use disposable synthetic state; do not alter active Goals.
   For contextual help, verify hover-to-panel continuity, keyboard focus/Escape,
   touch dismissal and narrow positioning. Confirm that opening help causes no
   operation and that failures/unknowns remain legible with every help closed.
   Record source/build identity and fixture boundaries. Source preview, packaged
   browser, installed App and real model each prove different things.
5. **Review the task before the diff narrative.** The review capability's
   `problem_context.outcome_impact.user_experience` references the same journey
   and validation. Independently check that the author's task matches the original
   request and challenge the full human workload against the smaller lawful
   route, then ask what remains wrong even if every test passes.
   Reused evidence needs its revision and unchanged assumptions. A material
   unsupported claim remains unproven. A necessary confirmation can be accepted
   with its authority basis; green CI cannot justify redundant confirmation.
6. **Deliver and read back.** Report the observable improvement, evidence limits
   and whether code is proposed, merged, packaged or installed. After an
   authorized upgrade, reopen the exact installed surface and check the same
   task; a pushed PR does not repair an already-running App. Use existing
   install/release owners and retain the previous working version for rollback.

Public PR evidence should fit in the existing summary/validation: one short
before/after journey, a screenshot or clip for the affected visual composition,
and the state readback plus tested revision and limits. Keep private messages,
local paths and raw run logs out. Put the first viewport directly in the PR
description, including its source/build boundary and a preview link when
available; evidence only in chat or a PR comment is insufficient. Follow
[First-Screen Review Evidence](../../AGENTS.md#first-screen-review-evidence).
Routine authorized refinements and new commits do not require another user
confirmation; ask only for a missing consequential decision or an explicitly
reserved presentation approval. Keep merge/install/release authority separate.

For Goal workspace composition, render before and after on the synthetic
showcase workspace (`LOOPX_PERSONAL_WORKSPACE_SCENARIO=goal-showcase node
examples/personal-workspace-browser-smoke.mjs`) and answer the
[craft review](design.md#craft-review) from those screenshots. Placeholder
fixtures prove behavior; they are not a basis for judging composition.

**Evaluation cadence:** ordinary work runs offline regressions and affected
browser/backend checks. Paid model interpretation and review-decision probes
belong to release-candidate qualification, with failures/skips retained. A
scripted browser passing cannot certify model intent recognition; an unrun live
probe is not a pass. See [release readiness](../product/release-readiness.md).

中文要点：先定义用户想完成的事，再比较最短合法路径；复用已有授权与状态归属，
减少重填、重复确认和页面跳转。以真实构建页面、代表性数据和状态回读验收，区分
模拟边界、打包验证和安装生效。评审先挑战交互成本与证据，再解释代码；平时不跑
付费模型评测，不用测试数量或 PR 合并代替用户任务完成。Goal 工作区的构图改动用
`goal-showcase` 合成展示数据截取前后对比图，并按 design.md 的 Craft review 逐项
回答；占位 fixture 只证明行为，不作为判断构图的依据。

## Source development and updates

Prepare Python with `uv sync --extra test`, then run from the repository root:

```sh
uv run --extra test python scripts/chat_bundle.py build --install
uv run --extra test python scripts/chat_bundle.py verify --source
uv run --extra test loopx chat --no-open
```

Builds start with current assets only. Use `build --previous /path/to/prior/chat`
when deliberately carrying a prior local delivery; release and installation
commands select their predecessor automatically.

With frontend dependencies already installed, `npm run build:chat` inside
`apps/presentation/dashboard` rebuilds the packaged UI. That npm entry runs
`scripts/chat_bundle_launcher.mjs`, which selects a Python 3.11+ interpreter the
same way the rest of LoopX does (`LOOPX_PYTHON`, then the installer-recorded
`.loopx-python`, the repository `.venv`, and `python3`/`python`, plus the `py`
launcher on Windows) and then runs the shared `scripts/chat_bundle.py` builder.
Supported Windows installations expose `python.exe` without a usable `python3`
alias, so the entry never assumes the POSIX name; the Windows CI lane rebuilds
the bundle under exactly that condition. `loopx dashboard`'s source development
launcher ensures the packaged backend assets exist before starting Vite. Run the
build again after updating source. A packaged Chat launch rejects missing,
corrupt or stale assets with an actionable rebuild message, including before
reusing an existing service. Explicit development/test `--assets-dir` continues
to support caller-owned assets.

The bundle manifest records the checkout revision, source fingerprints, current
asset set and SHA-256 of every delivered file. Source fingerprints conservatively
include shared TypeScript control-plane contracts. Unrelated changes in that tree
can require a rebuild. Text inputs normalize CRLF so a CI-built bundle remains
valid on Windows; delivered bytes are always hashed exactly. This is integrity
and freshness metadata, not a signature or a replacement for release attestations.

### Load boundaries

The workspace route is what every visit opens, so it loads eagerly. Settings
and its editors, the contributor projection page, the benchmark study page and
the answer report page are dynamic imports: each is its own chunk fetched the
first time it is shown, behind a visible loading status. Keep new secondary
surfaces behind the same kind of boundary rather than adding them to the entry
chunk, and keep a surface eager when a scenario pins the timing of its first
read (team results must list exactly once when a configured team appears). The bundle manifest records every chunk,
and the one-upgrade asset retention already covers an old tab importing a
deferred chunk after a delivery.

### Goal sidebar order

Goal dragging and keyboard move controls save presentation order through
`/api/chat/sidebar-goal-order` into the private runtime's
`presentation/sidebar-goal-order-v1.json`. Local App, browser and CLI-launched
Chat servers that share that runtime read the same preference after a port
change, restart or runtime upgrade. Status sources retain separate orders;
missing or stopped Goal ids remain in the saved list. This changes display
order only, without changing Goal priority, lifecycle or execution.

The existing TypeScript ordering functions remain the semantic owner. The
Python presentation adapter validates and atomically stores the list under a
file lock; it is not a capability configuration namespace. Existing same-origin
browser preferences migrate only when the durable source has no value. An old
origin's inaccessible browser storage cannot be recovered automatically. Read
or save failure keeps the current page usable and displays unsaved feedback;
another move retries. Reverting to an older runtime preserves the private file,
although that runtime continues to use its browser-local preference.

The packaged `navigation-sorting` browser scenario uses synthetic workspace
records with the real HTTP preference owner. It checks pointer/keyboard moves,
refresh, service restart on another port, delayed hydration, rapid moves,
failure/retry and browser-cache migration. It does not certify native WebView
crash recovery or an already-installed App's adoption of a proposed PR.

## PR qualification and releases

PR CI builds and verifies a clean bundle once, then uploads
`chat-bundle-<checkout SHA>`. Consumers download that one artifact, and
`chat-bundle-browser` exercises its actual pages in a browser across three
parallel shards of the scenario catalog (`LOOPX_PERSONAL_WORKSPACE_SHARD`), with
the companion team, craft, upgrade and ZCode smokes spread over the shards. The
`checks` aggregate requires every shard, so `merge-gate` cannot pass on an
artifact that failed browser qualification. Both frontend-only and
mixed/backend PRs pass through this producer. On pull requests the checkout SHA is GitHub's tested merge commit, which
may differ from the branch head. Generated-file Git cleanliness is no longer a
qualification gate. Browser, integrity and workflow gates remain required.

Release Artifacts checks out the exact release tag and runs:

```sh
python scripts/chat_bundle.py release-build --tag vX.Y.Z --repo loopx-project/loopx
python -m build --sdist --wheel
```

`release-build` requires authenticated `gh` read access and Node/npm. It downloads
the preceding stable version's wheel and `SHA256SUMS`, verifies the checksum, and
carries its current assets into the new bundle. Missing predecessor artifacts or
a checksum mismatch stop the release; desktop prereleases and later versions are
excluded. A repository with no predecessor can bootstrap without history.

Both wheel and sdist include the verified bundle. A normal package build fails
if it is absent/stale; editable installation remains available before a frontend
build. Building a wheel from the sdist requires no Node or network access for the
frontend. Release CI installs both wheel forms outside the checkout and requests
every delivered file through the actual Chat HTTP handler, then runs workspace
browser scenarios against the isolated installed interpreter. Desktop packaging adds
the qualified generated bundle to its exact Git source archive and rejects dirty
or mismatched frontend inputs.

## Upgrade window and rollback

A new delivery retains its own assets and the previous delivery's current assets.
It does not carry the previous delivery's entire retained history. The first
upgrade from an older wheel without provenance metadata retains its bounded asset
directory once. Source installers instead use the current installed snapshot as
the predecessor, so local development build history does not define upgrade
compatibility. Already-built archives can add those resources without Node or a
network connection. A failed build leaves the previous complete output intact.

This supports an old tab requesting a previously unloaded module after one
upgrade. It does not promise backend API compatibility indefinitely, or preserve
resources across arbitrary skipped stable releases for wheel installs. After a
second upgrade, reload older tabs. Rollback reinstalls the previous complete wheel
or selects the previous source snapshot; do not mix one version's HTML with
another version's assets. Existing tabs should reload after rollback.

Validation commands:

```sh
uv run --extra test python -m pytest tests/presentation/test_chat_bundle.py
npm --prefix apps/presentation/dashboard run smoke:chat-upgrade
npm --prefix apps/presentation/dashboard run smoke:personal-workspace-packaged
python scripts/verify_installed_chat.py --python /path/to/installed/environment/bin/python
```

The upgrade browser smoke opens version A, delivers B, imports A's deferred module
from the still-open tab, opens B in a new tab, then proves C retains B and retires A.
It uses disposable assets and the production HTTP handler, without active Goal
state. The packaged workspace smoke separately exercises the actual compiled UI.
