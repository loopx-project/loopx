# Release Readiness

Status: stable maintainer contract.

LoopX can move quickly without making every merged PR feel like a product
release. This note defines the small mental model maintainers should use before
promoting a release snapshot, recommending an install path, or telling users
which control-plane surfaces are safe to build on.

## Supported Install And Update Paths

For a first-time user, prefer the canonical PyPI release:

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

Restart the agent host after this first install so the newly delivered
workflow skills become active.

PyPI owns normal release acquisition and dependency resolution. `loopx update
apply` uses that same owning environment and then refreshes LoopX host material
and readbacks; it does not switch channels.

Use the same explicit intent flow for PyPI and archive installations:

```bash
loopx update check
loopx update plan
loopx update apply
loopx doctor
loopx extension doctor --all-enabled --execute
```

After `loopx update apply`, revalidate enabled extensions with
`loopx extension doctor --all-enabled --execute`. Stale extension readiness
is already revalidated during a successful apply; the explicit command above
is an independent readback and recovery entry point. A provider that still
fails remains closed until its per-extension doctor result is repaired and the
command passes. See [Extension lifecycle](../reference/extensions.md#runtime-lifecycle).

Do not collapse package acquisition, host-material delivery, core runtime
activation, and enabled-extension readiness into one "installed" claim. The
[installation guide's active-layer checklist](../guides/installing-loopx.md#verify-the-active-layers)
names the readback and recovery command for each layer.

For a pip or pipx distribution, apply delegates to that owner. For an archive
snapshot, apply uses the public `stable` ref by default and preserves atomic
snapshot rollback. Use `loopx update plan --ref main` and `loopx update apply
--ref main` only for maintainer/dev archive qualification. Re-running the curl
installer remains a repair path when an archive wrapper is too broken to run
its own updater.

For contributors, keep the clone-plus-canary path:

```bash
git clone https://github.com/huangruiteng/loopx ~/loopx
~/loopx/scripts/install-local.sh
loopx doctor
loopx-canary doctor
```

The PyPI path is the user default. The clone-plus-canary path is the maintainer
validation path, and the no-clone archive is the recovery fallback.

Before promoting a stable install/update recommendation, maintainers must move
the public `stable` ref to the release commit that passed this gate. Do not
claim stable-channel readiness while `stable` is missing or stale.

## Atomic Local Promotion Failure Matrix

Local clone installs promote through `scripts/install-local.sh`. Default
executable swaps are atomic symlink replaces that run only after a candidate
release directory passes deep `loopx doctor` validation and any required
workflow-skill preflight. Contributors should treat the matrix below as the
safe failure contract; do not invent a second promotion path or claim that a
failed candidate became the live default.

Shipped coverage lives in
`examples/release/release-promotion-concurrency-smoke.py` (lock, wait, and
pre-swap rejection) and
`examples/release/local-install-promotion-boundary-smoke.py` (canary-only
boundary, explicit override, and skill preflight stop).

| Case | When it happens | Before default symlink swap? | Waiter / recovery behavior | Contributor stop |
| --- | --- | --- | --- | --- |
| Promotion guard held | Another install holds `releases/.install-guard` | Yes: waiter has not reserved or swapped yet | Waiter polls the flock; it must not create a competing `.install-lock` while blocked. After the owner unlocks, the waiter acquires the guard and completes | Retry is safe. Do not delete a live guard or force-promote around it |
| Live legacy lock owner | `releases/.install-lock` names a live PID | Yes: timed-out waiter never reaches candidate build or swap | Waiter times out with `timed out waiting for another local install`; the live owner lock and PID file stay untouched | Stop and wait for the live installer, or ask a maintainer. Do not reap a live owner's lock |
| Empty or dead legacy lock | Lock directory has no valid live PID | Yes: reaped before a new owner publishes its PID | Waiter reaps the interrupted lock, then acquires ownership and continues | Safe automatic recovery. No maintainer action required |
| Concurrent same-second install | Two promoted installs race with a shared release-id seed | Partial: each waiter serializes under the guard/lock before its own swap | Each run gets a distinct release directory (`id`, `id-2`, …); one audited default symlink remains after both finish | Treat distinct release ids as expected. Do not hand-edit release directory names mid-install |
| Incomplete candidate (doctor fail) | Candidate package fails deep doctor or required package-root checks | Yes: candidate directory is removed; previous default target is preserved | No waiter swap occurs. Re-run only after the checkout is complete and doctor-clean | Stop promoting. Fix the checkout; do not set `LOOPX_PROMOTE_DEFAULT=1` to bypass doctor |
| Skill preflight blocked | Exact-host entry skill cannot be materialized (for example a user-owned colliding skill) | Yes: release candidate is removed; existing default binary stays unchanged | Failure is local to the install attempt; no partial default promotion | Stop. Resolve the skill ownership collision or install without that skill surface before retrying |
| Untrusted checkout (auto mode) | Checkout is dirty, not on the approved default ref, or otherwise untrusted | Yes for default: installer never builds a promoted release snapshot | Installer exits canary-only (`promotion mode: canary_only_untrusted_checkout`), leaves the existing default executable alone, and may refresh `loopx-canary` | Use `loopx-canary` for validation. Do not set `LOOPX_PROMOTE_DEFAULT=1` unless you are explicitly approving this checkout as a maintainer-owned default |
| Explicit override | `LOOPX_PROMOTE_DEFAULT=1` on an otherwise untrusted checkout | No: this is the intentional swap path after candidate validation | Promotion is auditable as `explicit_override` in `release.json` and doctor provenance | Contributor boundary ends here. Explicit default promotion, moving public `stable`, tagging, and PyPI publish remain maintainer-only |

Contributor-safe defaults:

- Prefer canary-only installs from ordinary feature checkouts.
- Treat any failure listed as "before default symlink swap" as proof that the
  previous default must still be live.
- When a waiter times out on a live owner, stop; recovery belongs to that
  owner finishing or a maintainer reclaiming a truly dead lock.
- Do not document, script, or smoke a contributor path that moves `stable`,
  publishes packages, or claims default promotion without
  `LOOPX_PROMOTE_DEFAULT=1` plus an explicit maintainer approval.

## Merged Is Not Runtime-Active

A post-merge check proves behavior on the tested source commit. It does not
prove that an installed LoopX runtime contains that commit. This distinction
matters when a fix reaches `main` after the latest named release: package
versions may still match while the installed source commit is behind.

Use `loopx update check --ref main` for archive maintainer qualification. Its
`runtime_activation_qualification` result compares the release-manifest source
commit with the selected ref's current GitHub commit. `source_commit_check`
records the bounded read; an unavailable lookup does not turn equal package
versions or a locally cached ref into an up-to-date claim:

- `runtime_active` means the installed commit is the target commit or contains it;
- `release_or_install_successor_required` means the installed commit is behind
  or diverged, so a release/install successor must remain explicit;
- `activation_qualification_required` means the selected commit differs without
  proven ancestry, the ref lookup is unavailable, or archive identity is not
  established; the runtime-active claim must fail closed until qualified.

A pinned full commit SHA is its own trusted target, so
`loopx update check --ref <40-hex-commit>` qualifies against the installed
manifest source commit without waiting for a branch lineage lookup. Installed
and pinned commits match: the receipt is `runtime_active`. They differ: the
receipt stays `activation_qualification_required` and names the installed
commit difference instead of the generic lineage message.

Closing a PR monitor after latest-`main` validation is valid, but the closeout
must not say the fix is active in the installed runtime unless this receipt is
`runtime_active`. Publishing a release remains a separate maintainer action.
When the qualification command itself runs from newer source code, pass a local
snapshot from the older installed CLI with `--installed-doctor-json`; this
option is read-only and accepted only by `update check`.

## Named Version Contract

LoopX releases are tagged and built from GitHub. The release workflow
publishes artifacts to GitHub Releases and, when its Trusted Publisher gate
passes, PyPI; each stable promotion still needs one package version name. The
version source is `loopx.__version__`, mirrored by `pyproject.toml`; the
expected public tag is `vX.Y.Z` for that version.

Before moving `stable`, maintainers should:

- bump `loopx.__version__` and `pyproject.toml` together when user-visible
  release behavior changes;
- create or verify the matching Git tag, for example `v0.1.3`;
- for host Goal/prompt changes, explicitly run the
  [release-only native Goal regression](../development/testing-and-quality.md#release-only-native-goal-regression--仅发布前的原生-goal-回归)
  in a supported Codex environment; record an unavailable environment as
  `skipped`, not a live pass. Never enable paid model execution in default PR CI;
- fast-forward `stable` to that tagged commit after the release canary passes;
- confirm `release.json`, `loopx doctor`, and `loopx update check` report the
  same package version and tag;
- tell existing users to run `loopx update check`, then
  `loopx update apply` when the check recommends or when they want to
  refresh to the named stable release.

The release workflow builds a wheel and source distribution from the tagged
commit. Its release assets include a canonical `SHA256SUMS` file, and GitHub
records build-provenance attestations for both packages and the checksum
manifest. Verify a downloaded bundle before installation:

```bash
sha256sum --check SHA256SUMS
gh attestation verify loopx-X.Y.Z-py3-none-any.whl --repo huangruiteng/loopx
gh attestation verify loopx-X.Y.Z.tar.gz --repo huangruiteng/loopx
```

The checksum proves that the downloaded bytes match the release manifest. The
attestation separately binds those bytes to the repository, workflow, commit,
and build event; neither mechanism claims that the package is vulnerability
free.

PyPI publication is an explicit, fail-closed extension of the same build. The
release workflow publishes only when maintainers have configured all of these:

- a PyPI project named `loopx` with a Trusted Publisher for
  `huangruiteng/loopx` and `.github/workflows/release-artifacts.yml`;
- a protected GitHub environment named `pypi` that matches the Trusted
  Publisher configuration;
- the repository variable `PYPI_PUBLISH_ENABLED=true`.

Do not add a long-lived PyPI token. Without every condition above, GitHub
Release packages and their verification material are still produced, while
the PyPI job remains skipped.

## Public Release Timeline

The public GitHub release timeline starts at `v0.1.3`. Earlier work should be
treated as pre-public bootstrap for the local control plane, installer, update
path, and canary route rather than as a user-facing release baseline.

- `v0.1.3` on 2026-07-02 14:45 +08:00: initial public stable-channel release
  at commit `10509b06`. This release made LoopX explainable as a no-clone,
  local-first control plane for long-running AI agents: install, update,
  doctor, named version reporting, and the first public status/quota/todo/gate
  surfaces were ready to recommend together.
- `v0.1.4` on 2026-07-03 00:24 +08:00: fast-follow release at commit
  `07d0a753`. This release tightened product-capability monitor projection,
  release-readiness checks, and canary evidence so the first public baseline
  was easier to diagnose and refresh.
- `v0.1.5` on 2026-07-03 13:28 +08:00: long-horizon execution hardening at
  commit `c036d60e`. This release improved quota/status/runtime routing,
  monitor and scheduler projection, release packaging coverage, and
  outcome-floor recovery for stuck or low-progress loops.
- `v0.1.6` on 2026-07-03 17:07 +08:00: visible multi-agent startup hardening
  at commit `1e3df9df`. This release made auto-research startup easier to see
  and trigger, clarified decentralized pane routing, tightened monitor and
  scheduler projection, and expanded the Codex CLI first-run release checks.
- `v0.1.7` on 2026-07-04 12:52 +08:00: command-entry integration release at
  the matching `v0.1.7` tag. This release made the supported entry layer
  explicit: Codex installs LoopX command-facade skills such as `$loopx`, Claude
  Code gets matching skill entries, legacy prompt shims are retired, and the
  rich workflow skills remain available for implicit LoopX behavior.
- `v0.1.8` on 2026-07-04 16:53 +08:00: deterministic host-loop activation
  release at the matching `v0.1.8` tag. This release gives new agent hosts an explicit
  `agent-onboard` contract for choosing `codex-app`, `codex-cli`,
  `claude-code`, `opencode`, `manual`, or `other-agent`, rejects ambiguous inputs such as
  `codex`, and makes `/loopx <task>` activate or gate the correct host loop
  after todo writeback.
- `v0.1.9` on 2026-07-05 21:45 +08:00: real auto-research and agent-scoped
  evidence release at the matching `v0.1.9` tag. This release removes fake
  auto-research demo metrics, makes the KNN preset use a real benchmark
  workspace with public-safe evidence writeback, exposes role-named visible
  research panes, wires agent-scoped evidence read hints into replan, and
  hardens successor/frontier recovery when completed advancement has no next
  executable todo.
- `v0.1.10` on 2026-07-06 11:50 +08:00: scoped user-gate and agent-management
  release at the matching `v0.1.10` tag. This release makes blocking owner
  todos explicitly typed as `user_gate` or non-blocking `user_action`, scopes
  per-agent gates with `blocks_agent`, adds read-only live agent-management
  status projections, and continues moving quota, todo, scheduler, review
  packet, and handoff rules into bounded control-plane contexts with focused
  canary coverage.
- `v0.1.11` on 2026-07-06 19:38 +08:00: vision-replan and recovery-routing
  release at the matching `v0.1.11` tag. This release makes goal-vision gaps
  participate in the quota/replan decision plane, preserves continuation audits
  in quota and interaction contracts, supersedes stale vision checkpoint gaps
  when newer evidence closes them, and adds judge guidance for when a vision
  gap is real work versus stale state. It also promotes the latest control-plane
  bounded-context cleanup, auto-research successor/evidence fixes, connector
  source-map packets, structured run-index classification, and Codex CLI/TUI
  recovery fixes.
- `v0.1.12` on 2026-07-08 02:05 +08:00: presentation/read-model and frontier
  recovery release at the matching `v0.1.12` tag. This release moves large
  status, goal-channel, dashboard, and Lark rendering paths into bounded
  presentation/read-model modules, fixes monitor-only plus open-vision frontier
  replan gaps, makes installer reruns overwrite stale wrappers/files safely,
  exposes premerge canary progress earlier, and promotes auto-research visible
  worker/successor routing plus selected public benchmark route/profile and
  SkillsBench helper hardening.
- `v0.1.13` on 2026-07-08 18:15 +08:00: guided onboarding and multi-agent
  control-plane release at the matching `v0.1.13` tag. This release makes new
  project setup more repairable with guided start-goal previews (#1631, #1633),
  non-destructive write-scope migration (#1636), delivery-scale aliases, and
  clearer refresh-state diagnostics (#1641); routes primary controllers toward
  subagent orchestration (#1622) behind an explicit default-off feature switch
  (#1643); improves scheduler ACK/backoff recovery and heartbeat migration
  (#1626, #1639); splits quota/status fixture hot paths, Lark projection row
  helpers, and content-ops markdown renderers into narrower modules (#1640,
  #1642, #1644, #1646); adds public-safe external ML task ledgers (#1627);
  hardens SkillsBench source/countability/launcher evidence (#1612, #1620,
  #1621, #1625); and relaxes local `next_action` / `recommended_action` text to
  allow local project routing references while still rejecting inline
  credentials (#1645).
- `v0.1.14` on 2026-07-09 11:49 +08:00: developer-contributed exploration
  topology and monitor/quota recovery release at the matching `v0.1.14` tag.
  This release promotes the software exploration result layer (#1546): public
  explore node/edge/finding records, Lark presentation mapping, graph exports,
  router/load-profile planning primitives, and deny-by-default
  `explore_harness` worker/todo branch planners gated by each goal's
  `spawn_policy`. It also ships the monitor scheduler cadence repairs that
  keep quiet monitor polls from collapsing back to short intervals (#1699 and
  related scheduler fixes), plus quota/status/todo read-model hardening for
  user-gate counts, completed-todo successors, evidence-log counts, delivery
  lineage, and compact agent-lane status summaries (#1707-#1716).
- `v0.1.15` on 2026-07-10: actionable routing and long-run reliability release
  at the matching `v0.1.15` tag. This release makes the agent-facing current
  action and quota-selected todo more explicit, centralizes primary-action
  resolution, and preserves replan acknowledgements, filtered resumes, vision
  lifecycle state, and due monitors across bounded progress (#1720, #1731,
  #1751, #1757, #1764-#1766, #1769-#1770). It hardens external monitor and
  multi-agent continuation through quiet-timeout handling, identity/capability
  gates, no-handoff lane fidelity, and typed continuation policies (#1722-#1724,
  #1745, #1747, #1754, #1773). Experimental issue-fix and SkillsBench routes
  gain feasibility, lifecycle, evidence, failure-attribution, cache/proxy,
  prewarm, and ledger-closeout improvements (#1726, #1734, #1738-#1744,
  #1748-#1750, #1753, #1756, #1759-#1763, #1767-#1768, #1771-#1772). The
  release also adds and repairs the parallel full-public smoke sweep, fixes
  direct-install doctor behavior, clarifies Explore's measurable-metric fit,
  and closes the todo CLI ownership-budget regression (#1721, #1725, #1727-#1730,
  #1735, #1743, #1752, #1774).
- `v0.1.16` on 2026-07-10: archive-install provenance hotfix at the matching
  `v0.1.16` tag. This release isolates release-manifest generation from the
  caller's working directory and inherited Python path, so running an update
  from an older LoopX checkout cannot stamp that checkout's package version
  into the new stable snapshot. The no-clone release gate now covers this
  stale-checkout invocation directly (#1776). No product capability or state
  migration changes in this hotfix.
- `v0.2.0` on 2026-07-11: peer-agent runtime and issue-fix control-plane
  release at the matching `v0.2.0` tag. This release completes the v0.2
  runtime cutover from hierarchical agent ownership toward equal peer agents:
  task claims are soft routing signals, independent handoff uses
  `continuation_policy=independent_handoff` plus `excluded_agents`, and stale
  legacy review continuation paths are rejected or migrated. It also promotes
  the issue-fix capability from feasibility planning into a fuller public
  maintainer loop with caller-repo branch preparation, acceptance artifacts,
  reviewer request fallback, PR lifecycle observation, and domain-state
  writeback. Explore Harness and long-run benchmark projections gain stronger
  public result contracts, while install/update, release provenance, quota,
  todo, scheduler, and protocol-action smokes were swept under the full-public
  suite for the 0.2 release cut.
- `v0.2.1` on 2026-07-12: agent-facing quality and long-run reliability
  fast-follow at the matching `v0.2.1` tag. This release makes bounded turn
  context explicit through TurnEnvelope contracts, adds trajectory-hygiene
  and packet-duplication measurements, and preserves action contracts while
  trimming repeated hot-path material. Issue-Fix gains repository snapshots,
  decision-useful memory, Explore projection, reviewer/CI receipts, impact
  metrics, and guarded promotion of newly discovered public defects. Optional
  Explore planning now preserves independent experiment lanes and supports
  resource-aware portfolio decisions. Peer routing is hardened across task
  lease validity, advisory agent profiles, deferred successor exclusions, and
  non-blocking user actions. The repository also establishes parallel pytest,
  Ruff, strict typing, import-boundary, coverage-floor, and release-promotion
  concurrency checks so these broader capabilities remain maintainable.
- `v0.2.2` on 2026-07-12: visible execution and projection reliability
  fast-follow at the matching `v0.2.2` tag. Explore gains recoverable execution
  episodes and ReplayPoint-based counterfactual branches, plus an optional
  owner-facing visual sink and real graph examples in the public entry
  surfaces (#1892, #1962, #1965-#1966, #1971). Visible multi-agent runs now
  wake only lanes whose runnable state changed and freeze the newest compatible
  host Codex CLI before launch (#1967, #1973). Diagnose capability projection,
  terminal PR-gate reconciliation, and vision replanning under monitor load are
  repaired (#1963-#1964, #1969). Benchmark comparison, report, learning-ledger,
  and result read models move into their control-plane runtime owner while
  preserving compatibility imports and restoring the full-public smoke shard
  (#1961, #1968, #1970, #1972). No persisted-state migration is required;
  Explore execution and visual sinks remain explicit opt-ins.
- `v0.2.3` on 2026-07-13: control-plane truthfulness and maintainer-surface
  release at the matching `v0.2.3` tag. LoopX adds a provider-neutral
  model-behavior qualification contract with public-safe corpus and decision
  receipts plus an optional direct provider actor (#1994, #1998-#1999, #2001,
  #2003). Optional capability discovery and the Lark event inbox/collector
  become clearer product surfaces without adding mandatory first-run
  configuration (#1978, #1986, #1997, #2000). Monitor, todo, quota, and vision
  routing now preserve capabilities and attribution, prefer advancement over
  stale monitor pressure, keep future waits quiet, and correlate material
  transition receipts (#1989-#1993, #2008, #2011, #2013-#2015). Explore graph
  activation now respects run-scoped sink authority (#1995, #2016), while
  deterministic update notes and project governance make the public repository
  easier to maintain (#1983, #1996, #2012). No persisted-state migration is
  required; optional provider, Lark, semantic-preference, and Explore surfaces
  remain opt-in.
- `v0.2.4` on 2026-07-14: Explore presentation and delivery-reliability
  release at the matching `v0.2.4` tag. Explore board layout is now a
  first-class `board_style` product parameter with two supported values:
  `auto_flow` uses Mermaid's automatic graph layout for topology-oriented
  views, while `semantic_lane_columns` emits deterministic stage SVGs for
  operator boards with meaningful parallel lanes (#2062). The Lark visual
  sink can publish one managed board per evidence stage, project the selected
  style into every stage, keep labels inside lane nodes, retry eventual visual
  readback, and reconcile generated document sections so stale or duplicate
  stages do not accumulate (#2051, #2063, #2065-#2066, #2068). The same
  canonical Explore result graph remains authoritative for both styles, and
  existing Mermaid-only configs continue to resolve as `auto_flow`. This
  release also includes same-source canonical/executive views, explicit
  issue-fix semantic-preference call sites, provider diagnostics, and further
  monitor, scheduler, installer, onboarding, and public-smoke hardening
  (#2002, #2005-#2006, #2018-#2021, #2027-#2028, #2032, #2036, #2052-#2061).
  No persisted-state migration is required; Explore and its Lark visual sinks
  remain opt-in.
- `v0.2.5` on 2026-07-15: reward-memory and cross-runtime reliability release
  at the matching `v0.2.5` tag. LoopX now ships a provider-neutral Reward
  Memory path from reviewed corpus and health contracts through candidate
  review, opt-in recall/application, evaluation, dogfood controls, and explicit
  actor-peer routing at the Issue-Fix planning boundary (#2076-#2085, #2096,
  #2100, #2103, #2128). Runtime projection routes become a first-class source
  of truth for material events, refreshes, and Explore commands across shared
  runtimes, with source-mirror ambiguity and compact diagnostics repaired
  (#2091, #2094, #2097, #2099, #2102, #2129). Issue-Fix gains stronger commit
  evidence, evidence-backed close counts, candidate dedupe, reviewer fallback,
  and delivery-window queuing (#2071, #2087, #2098, #2105, #2107, #2111).
  Monitor, scheduler, peer-replan, Lark inbox, Explore readback, and long-running
  SkillsBench paths are hardened against repeated host failures, scoped gates,
  transport loss, setup drift, and countability ambiguity (#2101, #2104,
  #2108-#2127, #2130-#2131). No persisted-state migration is required; Reward
  Memory and advanced fixer execution remain explicitly activated and bounded.
- `v0.2.6` on 2026-07-16: typed interaction authority and isolated Turn runtime
  release at the matching `v0.2.6` tag. Scheduler decisions now follow the
  typed interaction contract, exact blocked successors can trigger bounded
  autonomous replanning, and user gates no longer deadlock unrelated agent
  lanes ([#2136](https://github.com/huangruiteng/loopx/pull/2136),
  [#2177](https://github.com/huangruiteng/loopx/pull/2177),
  [#2187](https://github.com/huangruiteng/loopx/pull/2187),
  [#2188](https://github.com/huangruiteng/loopx/pull/2188),
  [#2198](https://github.com/huangruiteng/loopx/pull/2198),
  [#2203](https://github.com/huangruiteng/loopx/pull/2203),
  [#2204](https://github.com/huangruiteng/loopx/pull/2204)). LoopX Turn becomes
  a shipped isolated-headless route with executable envelopes, session
  recovery, independent validation, real CLI qualification, and a SkillsBench
  integration ([#2158](https://github.com/huangruiteng/loopx/pull/2158),
  [#2166](https://github.com/huangruiteng/loopx/pull/2166),
  [#2169](https://github.com/huangruiteng/loopx/pull/2169),
  [#2171](https://github.com/huangruiteng/loopx/pull/2171),
  [#2173](https://github.com/huangruiteng/loopx/pull/2173),
  [#2193](https://github.com/huangruiteng/loopx/pull/2193),
  [#2199](https://github.com/huangruiteng/loopx/pull/2199),
  [#2202](https://github.com/huangruiteng/loopx/pull/2202)). New-user
  onboarding is protected by deterministic lifecycle canaries and repeated
  one-arm Doubao qualification of the actual default packet, while CLI output
  budgets and release outcome contracts make semantic regressions visible
  before promotion ([#2144](https://github.com/huangruiteng/loopx/pull/2144),
  [#2148](https://github.com/huangruiteng/loopx/pull/2148),
  [#2153](https://github.com/huangruiteng/loopx/pull/2153),
  [#2157](https://github.com/huangruiteng/loopx/pull/2157),
  [#2159](https://github.com/huangruiteng/loopx/pull/2159),
  [#2167](https://github.com/huangruiteng/loopx/pull/2167),
  [#2168](https://github.com/huangruiteng/loopx/pull/2168),
  [#2201](https://github.com/huangruiteng/loopx/pull/2201)). Explore source
  reconciliation, optional Reward Memory experiments and reviewer gates, and
  Lark delivery are also hardened without making them first-run requirements
  ([#2200](https://github.com/huangruiteng/loopx/pull/2200)). No persisted-state
  migration is required; advanced capabilities remain explicitly activated.
- `v0.2.7` on 2026-07-18: control-plane convergence and exact-release-evidence
  release at the matching `v0.2.7` tag. Scheduler, quota, and todo decisions
  share one agent/runtime/capability/ACK scope; monitors converge independently
  without resetting one another; blocking user gates use one typed response
  plan; and Reward Memory v1 ships project corpus configuration with bounded
  Issue-Fix recall.
- `v0.2.8` on 2026-07-19: typed Codex App automation contract and periodic
  report control plane at the matching `v0.2.8` tag. Agent-scoped scheduler,
  quota, todo, monitor, user-gate, and frontier decisions become typed runtime
  contracts, and a provider-neutral periodic-report control plane ships for
  scheduled or material-progress reports without granting external-write
  authority.
- `v0.2.9` on 2026-07-20: lane-isolated scheduling and OpenCode host support at
  the matching `v0.2.9` tag. One agent lane can no longer consume or suppress
  another lane's frontier, OpenCode becomes a first-class Turn-backed host, and
  periodic reports gain a dense self-contained HTML presentation.
- `v0.2.10` on 2026-07-20: in-session weekly report quick start at the matching
  `v0.2.10` tag. A normal project session can request a local report without
  profiles, RRULE, host Automation, providers, or external sinks; owner pause
  stays authoritative for monitor-only quota work.
- `v0.2.11` on 2026-07-20: packaged weekly report preset at the matching
  `v0.2.11` tag. `loopx periodic-report inspect-profile --preset weekly`
  exposes the built-in provider-neutral preset; it creates no schedule, invokes
  no external sink, and grants no external-write authority.
- `v0.2.12` on 2026-07-24: heartbeat receipt and review-quality release at the
  matching `v0.2.12` tag. One quota receipt is persisted per heartbeat turn,
  monitor/replan routing stays fresh, `loopx pr-review` gains a code-volume and
  simplification lens, and adaptive multi-turn live-worker lifecycle phases
  stay visible through compact run and ledger views.
- `v0.2.13` on 2026-07-24: monitor follow-through release at the matching
  `v0.2.13` tag. Material monitor writeback resolves the exact todo before
  target-key fallback, exposes newly runnable successors immediately, and
  returns a structured stale-projection warning instead of reporting a failed
  write when only projection reload failed. Continuous-monitor todos may no
  longer carry `resume_when`.
- `v0.3.0` on 2026-07-30: capability and control-contract release at the
  matching `v0.3.0` tag. LoopX promotes simplify-first change qualification,
  provider-neutral decision context, governed material lifecycle workflows,
  managed-project delivery, and Ark Managed Agent host support while ordering
  quota rules and making recoverable Turn stages explicit.
- `v0.4.0` on 2026-08-02: onboarding and turn-authority release at the matching
  `v0.4.0` tag. Goal startup projects capability-owned admission routes,
  replan acknowledgements require canonical agent-visible evidence, the default
  `quota should-run` JSON stays inside a bounded model-facing budget, and the
  README foregrounds two inspectable 200+ hour loop trajectories.
- `v0.4.1` on 2026-08-04: durable work selection and Goal-host continuation
  release at the matching `v0.4.1` tag. Capability-admitted Todo routes persist
  across turns, Goal hosts wake on the earliest material frontier transition,
  grouped Issue Fix PR monitors materialize explicitly, and default-off Agent
  Turn Recall ships with agent/goal/project/Todo/authority scoping.
- `v0.4.2` on 2026-08-07: host and workflow surface release at the matching
  `v0.4.2` tag. Pi and TraeX become first-class host paths, adaptive child
  admission enforces domain/capability/repository/write-scope readiness,
  provider-neutral PR queue observation and PR program workflows ship, and
  Issue Fix pins work to an approved base snapshot.
- `v0.4.3` on 2026-08-09: effect-interpreter evolution release at the matching
  `v0.4.3` tag. A second real `EffectTurn` interpreter consumes turn results,
  data-encoded execution and an ordered effect program shape land, the runtime
  plan is replacement-first, and a unified bilingual Dev Book adds an
  independent Control-Plane Course chapter.
- `v0.4.4` on 2026-08-09: M6 effect-program quality-gate completion at the
  matching `v0.4.4` tag. Hot control-plane modules are bounded,
  `EffectTurn`/`EffectProgram` are consumed by real runtime paths, and the M6
  RFC is marked Complete with audit evidence.
- `v0.4.5` on 2026-08-13: security-hardening and control-plane release at the
  matching `v0.4.5` tag. LoopX fixes five privately reported security
  advisories, adds caller-approved completion validation, ships a
  durable-smoke review gate, and continues replan/evidence/settlement
  hardening with community contributions across 16 contributors.
- `v0.4.6` on 2026-08-13: replan and notification hardening release at the
  matching `v0.4.6` tag. Replan closeout becomes semantic, quota/heartbeat
  notification correctness is repaired, refresh-state writeback guards land,
  and two architecture RFCs document the effect-program direction.
- `v0.4.7` on 2026-08-15: governed-host continuation release at the matching
  `v0.4.7` tag. OpenCode 1/2 goal loops stop interrupting on user messages or
  task closeout, DeepSeek Harness connects through a managed Turn connector,
  per-goal handoff mode gates claim/lease authority in state files, and Explore
  can publish multiple Feishu visual boards in one automated step.
- `v0.4.8` on 2026-08-16: open-core packaging and qualification release at the
  matching `v0.4.8` tag. LoopX adopts Apache-2.0 for the open core, ships as a
  first-class PyPI distribution, adds per-Todo validation budgets, and tightens
  benchmark integrity qualification and Content Ops presentation density.
- `v0.4.9` on 2026-08-19: cross-platform host and long-loop reliability release
  at the matching `v0.4.9` tag. LoopX makes PyPI the default complete install
  path, adds native Windows PowerShell and KunlunCode Goal Pro support, ships an
  opt-in repository change-window provider with a durable pending-change
  ledger, and hardens heartbeat settlement, Todo validation, PR review
  scheduling, native Goal benchmark isolation, and public repository signal
  providers.
- `v0.5.0` on 2026-08-20: personal control-plane workspace release at the
  matching `v0.5.0` tag. LoopX promotes the dashboard as the supported
  browser/PWA workspace, adds a source-built native desktop shell over the same
  local authority, makes Goal stop/resume and repository change windows
  operator-visible, and admits goal-bound external capability providers.
- `v0.5.1` on 2026-08-21: operator workflow and collaboration hardening at the
  matching `v0.5.1` tag. LoopX adds downloadable macOS and Windows desktop
  previews, first-class DeepSeek Harness packaging, botmux Goal Channel turns,
  three Agent-scoped Lark ingress modes, and fixes Goal lifecycle, Todo defer,
  quota settlement, replan closeout, and event-sourced freshness paths.
- `v0.5.2` on 2026-08-23: transaction and multi-source workspace reliability
  release at the matching `v0.5.2` tag. LoopX moves the core Turn settlement
  path behind typed effect transactions, makes local/SSH source changes
  generation-fenced, adds a native DeepSeek Harness Goal workspace and
  Agent-scoped external Connector providers, and hardens long Todo chains,
  prepared-effect recovery, benchmark admission, and public-smoke release gates.
- `v0.5.3` on 2026-08-27: host reach and autonomous-continuation reliability
  release at the matching `v0.5.3` tag. LoopX adds ZCode and Antigravity CLI
  Goal surfaces, a bounded citation-led deep-research workflow, and an explicit
  Pi task-lease facade; it also strengthens Lark inbox routing and catch-up,
  typed Todo/quota/scheduler settlement, repository delivery admission, and
  runtime startup recovery.
- `v0.5.4` on 2026-09-03: typed control-plane and governed-workflow release at
  the matching `v0.5.4` tag. LoopX moves more Todo, task-lease, quota,
  scheduler, Vision, and replan transactions behind TypeScript owners;
  advances staged file, PostgreSQL, and NoKV shared-authority providers;
  completes the periodic-report lifecycle; makes the DSH plugin one-step ready; and adds
  public-safe benchmark study projection without granting upload authority.
- `v1.0.0` on 2026-09-06 20:44 +08:00: the Workspace milestone release at the
  matching `v1.0.0` tag (merge `d6e8387e`). The desktop companion gains signed
  in-app updates with a paired runtime and a recovery path for interrupted
  installs (#3994); Todo claim and update authority finishes its
  claim-neutral correction through the TypeScript transaction (#4005, with
  promoted claim retry identity from #3987); multi-agent Goal Channels ship
  with per-agent connection resolution (#3969); and reward-memory recall
  guides outbound messages behind a digest-bound review loop (#3968).
- `v1.0.1` on 2026-09-08 06:51 +08:00: post-1.0 reliability release at the
  matching `v1.0.1` tag (`7f2a020b`). Goal Channels gain resumable multi-Agent
  onboarding and Agent-authorized typed report requests; Desktop recovery gains
  bounded diagnostics and verified signed updates; Todo ownership, projection
  recovery, and Stage 2C management use stronger typed transaction boundaries;
  and frozen bundles install the same version-bound workflow skills as package
  distributions. The exact-tag Python, PyPI, macOS, Windows, signed-update,
  public-smoke, and live-model gates passed before `stable` fast-forwarded.
- `v1.0.2` on 2026-09-09 11:22 +08:00: single-owner Todo authority and
  recovery release at the matching `v1.0.2` tag (`a5374d5b`). Promoted Todo
  terminal transitions commit through one TypeScript-owned provider
  transaction; missing generated Todo projections recover without making
  Markdown authoritative; and long-history, Desktop, DSH, and managed-skill
  paths gain bounded reads and clearer recovery diagnostics. The published
  wheel, source distribution, macOS, Windows, checksum, update, and PyPI
  artifacts were verified against the exact release source before promotion.
- `v1.0.3` on 2026-09-11 11:53 +08:00: native monitor observation and consumer
  closure release at the matching `v1.0.3` tag (`0496975e`). Native monitor
  observations and their successors commit atomically through one typed
  transaction, and completed history stays out of target-key selection (#4187);
  quota unifies typed scope selection without erasing user gates, and the
  scheduler hint accepts canonical Base64 transport; archived history preserves
  decision and resume semantics (#4184); the manager keeps concrete Core
  findings in progress reports behind scoped evidence reads (#4213, #4218);
  Goal Channels extract runtime command ownership (#4154) and deliver manager
  terminal failure receipts (#4217); and iteration-fresh host dispatch plus
  typed upstream terminal errors land through #4126 and #4215. The published
  wheel, source distribution, macOS, Windows, checksum, update, and PyPI
  artifacts were verified against the exact release source before promotion.
- `v1.0.4` on 2026-09-15 14:19 +08:00: bounded local authority storage,
  human-confirmed operations, and a wider typed kernel release at the matching
  `v1.0.4` tag (`b6d877b0c`). The opt-in SQLite authority provider keeps its
  retained state log bounded with compact operation deltas plus periodic
  checkpoints, separates storage, head, and continuity integrity, and migrates
  V1 -> V2 as one idempotent, fail-closed transaction with a plan/execute
  operator command (#4408, #4328, #4121); PostgreSQL authority admission
  becomes a service-owned boundary whose authority ladder runs against a real
  server in CI (#4334, #4399). Goal Channel operations ship as human-confirmed
  typed actions whose review decision travels with the operation instead of
  being re-derived per surface (#4275, #4364, #4132), and Todo, quota, lease,
  monitor, and decision-scope rules converge on shared typed owners (#4273,
  #4289, #4292, #4348, #4351). The published wheel, source distribution,
  macOS, Windows, checksum, signed-update, and PyPI artifacts were verified
  against the exact release source before `stable` fast-forwarded.
- `v1.0.5` on 2026-09-16 03:14 +08:00: first-connect registration and explicit
  host selection hot-fix release at the matching `v1.0.5` tag (`0c3971fea`).
  `connect`/`bootstrap` register the goal and write the active state only, so a
  freshly connected goal is immediately eligible instead of parking on a
  generated owner gate, and the eight first-connect bootstrap options plus the
  `onboarding_*` payload fields and the `adapter.connection_validation`
  annotation are removed (#4465). Managed execution is selected rather than
  inferred: the managed Turn host (#4443, #4451) and the steward channel
  endpoint (#4446, #4419) come from explicit configuration, PR-review CI
  waiting is configurable per machine or goal (#4452) under a documented review
  frame (#4434), and update activation is qualified from an immutable source
  commit (#4460) while the serving runtime publishes its identity with a
  restart path (#4423). Quota window-slot spend clamps against the voids that
  target it (#4384), the held coordination fence survives an authority-source
  change (#4429), and a goal frontier missing its final-outcome claims is
  diagnosed (#4341). Community contributions cover text-mode subprocess
  decoding (#4396), stale executor revisions rejected before persisting
  (#4398), CLI dash options (#4428), manager delegation reconciliation (#4372),
  window-slot clamping (#4384), the semantic vocabulary registry and its
  governance (#4433, #4453), and the Codex App autonomous goal guide (#4442).
  The published wheel, source distribution, checksum, and PyPI artifacts were
  verified against the exact release source before `stable` fast-forwarded.
- `v1.1.0` on 2026-09-20 01:04 +08:00: organization migration and governed
  collaboration release at the matching `v1.1.0` tag (`607c11d75`). LoopX
  moved its repository, Pages, installer, and update surfaces to
  `loopx-project`; team plans gained an explicit confirmation-to-canonical-work
  path, local delegation gained durable operation recovery and checked returns,
  and Goal acceptance plus replan obligations retained their existing authority
  owners. The published package, desktop artifacts, signed update feed, and
  website were verified before `stable` fast-forwarded.

- `v1.2.0` on 2026-09-25 10:19 +08:00: recoverable authority, typed Todo
  transitions, and inspectable host activation at commit `eb11130e1`.
  The release separates package acquisition, delivered host material, runtime
  activation, and enabled-extension readiness.

When a new public release is promoted, add it here only after the matching tag,
release note, stable ref, update path, and focused release canary agree.

## Compatibility Gate

Before a release snapshot is promoted or a public guide tells users to depend
on a new surface, run the smallest gate that covers the touched surface:

```bash
python3 -m py_compile loopx/*.py
python3 examples/release/codex-cli-no-clone-release-verification-smoke.py
python3 examples/fresh-clone-quickstart-smoke.py
python3 examples/loopx-update-smoke.py
python3 examples/release/release-version-contract-smoke.py
python3 examples/release/release-readiness-doc-smoke.py
python3 examples/repository-hygiene-smoke.py
git diff --check
loopx check --scan-path README.md --scan-path docs/ --scan-path examples/
```

This is not a universal full suite. Add focused smokes for the changed command,
projection, or workflow. Do not require benchmark raw logs, raw task text,
trajectories, verifier output, credentials, or local private artifact paths as
release evidence.

After the individual lanes pass, bind their compact receipts to the exact clean
release checkout before tagging or moving `stable`:

```bash
loopx canary release-qualification \
  --manifest-json release-qualification.json \
  --repo-root .
```

The `exact_release_commit_qualification_manifest_v0` contract requires the
same Git commit, Git tree id, package version, and version tag across pytest,
Ruff, mypy, risk-based canary, full-public, install/upgrade/host,
public-boundary, and actual-default one-arm Doubao receipts. The command also
checks the current checkout and rejects dirty or rebased source. It only
reduces existing bounded receipts: it does not execute tests, call a provider,
move refs, create tags, or publish a release.

## Canary Model

A release canary is a catalog-informed readiness slice. It is near-E2E in the
sense that it follows a real promotion or operator path across several seams,
but it is intentionally smaller than a full end-to-end test suite. Its job is
to answer "can the touched public surfaces be promoted under this declared
boundary?" rather than "is every LoopX path correct?"

Choose the canary group from existing interaction pattern families; do not add
new IPs solely to describe a validation bundle:

- status/quota/scheduler changes should include Work Routing checks such as
  `quota should-run`, scheduler hints, and hot-path interface budget;
- state projection or public/private changes should include State And Boundary
  checks such as `loopx check`, task graph or todo detail cold-path contracts;
- dashboard/frontstage changes should include catalog or fixture route checks,
  with browser smokes only when the visual surface itself is being promoted;
- release/install changes should include installer, update, wrapper, doctor,
  and public-boundary checks;
- benchmark or external-evidence changes should use compact lifecycle
  evidence only, never raw task text, raw logs, trajectories, or verifier tails.

The default promotion canary is:

```bash
python3 examples/canary/canary-promotion-readiness-smoke.py --no-write-evidence
```

The default dashboard policy is `--dashboard-mode=auto`: source checkouts run
dashboard demo-readiness when `apps/presentation/dashboard` is present, while installed
release snapshots that omit the dashboard app skip that optional surface and
keep the omission visible in the canary output. Use `--dashboard-mode=require`
when the dashboard/frontstage itself is being promoted, and
`--dashboard-mode=skip` only when the release boundary intentionally excludes
the dashboard app.

Use the writeback form only when you intentionally want to append fresh
promotion-readiness evidence:

```bash
python3 examples/canary/canary-promotion-readiness-smoke.py
```

For broader source-checkout regressions, keep `loopx canary smoke-suite` as the
source of truth. Local and LoopX automation should continue to use the runner
payload directly:

```bash
python3 examples/run-smokes.py --suite default-public --module canary
loopx canary smoke-suite --suite default-public --module canary
```

For larger source-checkout sweeps, use the runner's bounded parallelism instead
of moving smoke semantics into a second test framework. `--jobs` keeps the
LoopX runner payload as the source of truth while preserving serial execution
for smokes that declare a scheduling-sensitive surface:

```bash
python3 -m loopx.cli canary smoke-suite --profile public-smoke-watch --jobs 4 --timeout-seconds 60
python3 examples/run-smokes.py --suite full-public --jobs 4 --timeout-seconds 60
```

For repeatable canary/refactor batches, prefer named smoke-suite profiles over
hand-curated script lists. Profiles expand to the same runner payload as
`--module`, `--script`, catalog selectors, and the pytest facade:

```bash
loopx canary smoke-profiles
loopx canary smoke-suite --profile core-control-plane --no-execute
loopx canary smoke-suite --profile core-control-plane --offset 20 --limit 20 --timeout-seconds 60
loopx canary smoke-suite --profile canary-runner --timeout-seconds 60
python3 examples/run-smokes.py --profile public-entry-install-release --no-execute
```

Use `--offset` with `--limit` to sweep large profiles in stable windows without
rerunning the same prefix batch on every heartbeat.

Default `pytest` is the fast unit and contract lane. The smoke-suite facade is
explicitly opt-in so a normal PR test run does not silently expand into the
canary matrix. CI may wrap an explicit runner selection in pytest when JUnit
reporting is useful. The facade still executes each selected
`examples/**/*-smoke.py` through a subprocess; it is not a migration of legacy
smokes into pytest unit tests:

```bash
python3 -m pytest tests/test_smoke_suite.py \
  --loopx-smoke-suite default-public \
  --loopx-smoke-profile canary-runner \
  --loopx-smoke-offset 0 \
  --junitxml smoke-suite.xml
```

The required [Python test workflow](../../.github/workflows/python-tests.yml)
owns the Ruff namespace selection and package coverage floor. The floor is a
regression guard, not a claim of sufficient coverage; raise it as durable
behavior moves from subprocess smokes into focused tests.

[Architecture tests](../../tests/architecture/test_control_plane_import_boundaries.py)
reject outward control-plane dependencies and forbidden status dependencies
without migration exceptions, and protect presentation ownership of quota
Markdown. See the [dependency policy](../architecture.md#current-dependency-budget)
for the boundary rationale. Existing source-wide lint debt is characterized
separately. Strict mypy scope is the `[tool.mypy].files` list in
[`pyproject.toml`](../../pyproject.toml); use `python -m mypy` to check that exact
scope. Expand each protected namespace only after a bounded cleanup rather
than duplicating changing file counts or mass-fixing unrelated code to make a
broad gate green.

If the source checkout has optional frontend dependencies installed, dashboard
readiness can be included in the same canary. If a release snapshot omits the
dashboard app, the canary should degrade gracefully and record that boundary
rather than failing unrelated CLI/install promotion or silently treating the
dashboard path as covered.

## What Is Safe To Depend On

Treat these v0.x surfaces as stable enough for user guides, examples, and
host integrations when their focused smokes pass:

- `loopx doctor`, `loopx update`, `loopx check`, and the no-clone installer;
- `loopx extension doctor` for enabled-extension readiness after install or
  update apply;
- project lifecycle commands: `bootstrap`, `connect`, `status`,
  `refresh-state`, `registry`, and `sync-global`;
- todo lifecycle commands: `todo add`, `todo claim`, `todo update`,
  `todo complete`, `todo list`, `todo supersede`, and `todo archive`;
- control-plane read paths: `quota should-run`, `quota spend-slot`,
  `review-packet`, `heartbeat-prompt --thin`, task graph projection, and cold
  todo detail references;
- public slash command names: `/loopx`, `/loopx <goal>`,
  `/loopx-global-summary`, `/loopx-global-gates`, `/loopx-global-todos`, and
  `/loopx-global-risks`;
- ignored local state boundaries under `~/.codex/loopx`, project-local registry
  files, and project-local active-state workbench files recognized by
  `loopx doctor`, `loopx status`, and `loopx check`.

Treat these as experimental until their contract docs say otherwise:

- benchmark runner behavior, scoring, upload, and raw task execution routes;
- host-plugin command registry implementations beyond the published protocol
  contract;
- frontstage/dashboard presentation details that are not part of the public
  status data contract;
- monitor scheduler cadence fields while they are still rolling out across
  todo creation, quota projection, writeback, and migration.

## Release Note Checklist

Start the final GitHub release body from the canonical
[release note template](release-note-template.md). Its first substantive
section is a compact `## Release Decision` block that answers the five
questions a reader needs before inspecting the detailed changelog:

| Field | Required decision |
| --- | --- |
| `**Who should upgrade:**` | Name the affected users or operators, the reason to upgrade now, and who can remain on the current version. |
| `**What this release solves:**` | State the concrete failure, missing workflow, or reliability gap in user-outcome language. |
| `**Breaking changes:**` | Start with `No.` or `Yes.`; when yes, give the migration path, and when no, still disclose changed defaults, deprecations, or experimental boundaries. |
| `**How to verify:**` | State the expected post-upgrade result and include a minimal runnable `bash` block that proves package identity and the affected behavior. |
| `**Contributors:**` | Name the release maintainer and community contributors from the tag range, or explicitly say that the release has no community contribution. |

Mirror the same decisions under `### 升级决策` in the Chinese summary with
`**谁需要升级：**`, `**解决了什么：**`, `**是否有破坏性变更：**`,
`**如何验证：**`, and `**贡献者：**`. The summary is a decision aid, not a
replacement for the detailed product groups, per-claim PR evidence, optional
capability lifecycle, or exact-commit validation evidence below it.

Keep user-visible product changes first. When merged pull requests between the
previous and current tags include community contributors other than project
founder `@huangruiteng`, add a prominent `## Community Contributors` section
after the English product groups and before compatibility, validation, or
update material. Link each eligible GitHub handle and relevant pull requests,
summarize the concrete contribution, and explicitly call out external or
first-time contributors when applicable.

Do not list or thank `@huangruiteng` in this section; founder stewardship is
implicit in each LoopX release. Omit the section when the tag range contains no
eligible community contribution. Contributor recognition must complement the
release narrative, not replace or precede its product highlights.

Build the list from the tag-to-tag Git range and merged pull-request metadata,
not commit display names or an unreviewed generated changelog. Attribution is
part of the release contract even when the same pull request is linked again
under a product group.

Organize the remaining release note into the following stable groups. Omit an
empty product group instead of inventing filler:

1. **State Kernel & Control Plane** for state, todo, quota, scheduler, gate,
   peer-routing, and runtime authority changes.
2. **Capabilities & Workflows** for shipped user workflows such as Issue-Fix,
   Explore, Reward Memory, onboarding, and LoopX Turn.
3. **Quality & Testing** for deterministic tests, canaries, output budgets,
   model-behavior qualification, and release gates.
4. **Benchmarks & Integrations** for benchmark adapters, Lark, host runtimes,
   and other external boundaries.
5. **Documentation & Compatibility** for public contracts, install/update
   guidance, migrations, defaults, and intentional exclusions.

Bilingual releases with eligible community contributions must add
`### 社区贡献者` after the Chinese product groups and before Chinese
compatibility or validation material, with the same people, pull-request
links, and concrete contribution scope as the English section. Omit both
language sections together when no eligible contributor exists. Bilingual
releases must preserve these same group boundaries in both languages. Use the
matching headings **状态内核与控制面**, **能力与工作流**, **质量与测试**,
**基准与集成**, and **文档与兼容性**. The Chinese copy may be shorter, but it
must not collapse several groups into a generic highlights list, omit a
non-empty English group, or weaken contributor attribution.

Within each non-empty group, every material claim must carry one or more direct
GitHub pull-request links such as
`[#2051](https://github.com/huangruiteng/loopx/pull/2051)`. A compare link is
still useful at the end, but it does not replace per-claim PR attribution.
Avoid bare PR ranges as the only evidence because ranges can hide omitted or
unrelated changes.

After the decision summary and product groups, every public release note should
also record:

- What package version and public tag name this stable release uses?
- Which install/update path should a new user follow?
- Which surfaces are still experimental or intentionally excluded?
- For every new or materially changed experimental, default-off, or opt-in
  capability, include an **Optional capability activation** entry in both
  languages: name its scope, read-only preview, exact enable and disable
  commands, prerequisites or safety gates, and canonical docs. If no persistent
  switch exists, say that opt-in is per command or preset instead.
- Did the public/private scan run on the changed docs, examples, and workflow
  files?
- Did full `pytest`, focused release/install contracts, risk-based canary, and
  promotion-readiness/public-boundary checks pass on the exact release commit?
- Did `loopx canary release-qualification` confirm that every required compact
  receipt matches the same clean commit, Git tree, package version, and tag?
- Did the low-frequency live model gate run against the actual default
  agent-facing packet with at least two repeats? Record the model id, behavior
  decisions checked, call count, failures, and skips, but never retain raw
  prompts, packets, responses, credentials, or local paths. This remains a
  local/manual release gate rather than ordinary CI.
- If the release claims benchmark or long-horizon outcome improvement, did a
  matched stable-versus-candidate outcome baseline pass? If no outcome claim is
  made, state that this expensive gate was not required rather than implying it
  ran.
- For Chinese-speaking operators, include a compact `## 中文摘要` section that
  mirrors the English group structure and material claims in neutral product
  language. Keep each group shorter than its English counterpart while
  preserving direct PR attribution and compatibility boundaries.

### Final Release Body Usage Gate

The release-note PR is not sufficient evidence. Before publishing, save the
complete final GitHub release body to an ignored or temporary Markdown file and
validate that exact file:

```bash
python3 examples/release/release-readiness-doc-smoke.py \
  --release-notes <final-release-body.md> \
  --surface "<new-or-materially-changed-surface>" \
  --surface "<another-surface>"
```

Derive the repeated `--surface` values from the tag diff, merged PR inventory,
and release claims. Each named surface must have a dedicated English
`### <surface>` entry under `## Optional Capability Activation & Use` and a
matching Chinese `#### <surface>` entry under `### 可选能力启用与使用`. Both
entries must include these explicit fields:

| English | Chinese | Required content |
| --- | --- | --- |
| `**Activation:**` | `**启用：**` | Exact install/enable command, or the exact per-command/profile opt-in when no persistent switch exists. |
| `**Validation:**` | `**验证：**` | Minimum runnable status, readback, or verification command. |
| `**Disable / rollback:**` | `**停用 / 回退：**` | Exact disable, uninstall, envelope removal, or rollback path. |
| `**Authority boundary:**` | `**权限边界：**` | Write, merge, provider, privacy, and host limits that activation does not grant. |
| `**Docs:**` | `**文档：**` | Canonical versioned documentation link. |

Every entry needs at least one runnable `bash` block. A release with no new or
materially changed optional capability, workflow, or host surface must instead
run the gate with `--expect-no-optional-capability-changes` and include the
exact bilingual declarations required by the validator. Omitting both a
surface list and the explicit no-change decision fails closed.

After publishing, read the complete remote body back with `jq -rj`, compare its
hash with the reviewed local file, and run the same validator against the
readback. Do not validate a draft and then publish a different body.

### Capability Narrative Gate

For every new or materially changed capability, write the release claim in
three explicit layers:

1. **User outcome**: say what a user can now accomplish in product language,
   before naming protocols, providers, renderers, or other implementation
   mechanisms.
2. **Shipped layer**: identify whether the release provides a control-plane or
   protocol kernel, a built-in adapter or presentation layer, or a complete
   end-to-end workflow. Name the commands, docs, or smokes that prove that
   layer.
3. **Last-mile boundary**: name any default profile, collector, scheduler,
   destination, credential setup, or publication step that is still absent or
   explicitly opt-in. Do not overclaim a complete workflow, but do not hide a
   shipped core behind mechanism-only wording either.

A built-in capability is not an optional extension merely because some of its
collectors, renderers, sinks, or providers are optional. Describe the built-in
outcome and its lifecycle separately from provider activation. When a release
ships successive layers of one outcome, such as a report kernel followed by an
HTML presentation layer, attribute and explain each layer instead of folding
both into a generic integrations bullet.

The Chinese summary must preserve the same user outcome, shipped layer, and
last-mile boundary. Translation may be shorter, but it must not replace the
user-facing outcome with architecture-only terminology.

The release PR and final GitHub release body must use the same grouping and
contributor attribution, plus the same validation receipt. Re-run the gates
after rebasing or merging any additional runtime change; results from an
earlier commit do not qualify a later tag. Public git history, merged PR
metadata, and shipped CLI behavior remain the source of truth.

## Related Docs

- [Codex CLI packaged install path](runtimes/codex-cli/codex-cli-packaged-install.md)
- [Codex CLI no-clone release verification](runtimes/codex-cli/codex-cli-no-clone-release-verification.md)
- [Getting started](../guides/getting-started.md)
- [Update notes](../update-notes/README.md)
- [Public/private boundary](../public-private-boundary.md)
- [Interaction pattern catalog](../concepts/interaction-pattern-catalog.md)
