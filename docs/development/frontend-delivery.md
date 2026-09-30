# Frontend build and delivery

Chat source lives in `apps/presentation/dashboard`. `loopx/web/chat/` is generated,
ignored output, not a second editable source tree. Feature PRs commit source and
validation only; resolving a source merge followed by a build produces one
coherent HTML/JavaScript/CSS set. Do not concatenate competing HTML entrypoints or
asset-retention manifests when resolving an older PR.

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

Carry the same task through these steps:

1. **Compare the journey.** Write the current and proposed user steps in the
   existing before/after evidence. Count required navigation, information the
   user must repeat, questions, confirmations and recovery effort. Explain the
   information, authority or unavoidable prerequisite supplied by each required
   step; remove a step that supplies none. This is a comparison, not a universal
   click limit. Keep necessary scoped consent and explicit effect confirmation;
   make optional refinement optional. A complete conversational draft should not
   require a second form just to repeat the same facts.
2. **Choose the owner and composition.** Reuse the existing typed state and
   action owner and inspect companion App/CLI/messaging paths. Follow
   [design.md](design.md#earn-the-users-attention) for the whole viewport, not
   individual cards. Navigation follows user purpose; clearly show the target
   and source of a setting. Do not expose storage scopes as competing pages or
   list controls that cannot apply to the selected target.
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
   Record source/build identity and fixture boundaries. Source preview, packaged
   browser, installed App and real model each prove different things.
5. **Review the task before the diff narrative.** The review capability's
   `problem_context.outcome_impact.user_experience` references the same journey
   and validation, then asks what remains wrong even if every test passes.
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
local paths and raw run logs out. The existing public first-screen preview gate
still applies; do not add user approvals for routine implementation decisions.

**Evaluation cadence:** ordinary work runs offline regressions and affected
browser/backend checks. Paid model interpretation and review-decision probes
belong to release-candidate qualification, with failures/skips retained. A
scripted browser passing cannot certify model intent recognition; an unrun live
probe is not a pass. See [release readiness](../product/release-readiness.md).

中文要点：先定义用户想完成的事，再比较最短合法路径；复用已有授权与状态归属，
减少重填、重复确认和页面跳转。以真实构建页面、代表性数据和状态回读验收，区分
模拟边界、打包验证和安装生效。评审先挑战交互成本与证据，再解释代码；平时不跑
付费模型评测，不用测试数量或 PR 合并代替用户任务完成。

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

## PR qualification and releases

PR CI builds and verifies a clean bundle once, then uploads
`chat-bundle-<checkout SHA>`. Consumers download that one artifact, and
`chat-bundle-browser` exercises its actual pages in a browser in parallel. The
`checks` aggregate requires that browser lane, so `merge-gate` cannot pass on
an artifact that failed browser qualification. Both frontend-only and
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
