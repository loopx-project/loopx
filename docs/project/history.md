# LoopX Project History

This page records the public milestones that changed LoopX's identity,
governance, or stable product direction. It is not a commit-by-commit changelog.
Ordinary release changes belong in [GitHub Releases](https://github.com/loopx-project/loopx/releases)
and the [update notes](../update-notes/README.md).

## Public Timeline

### 2026-05-31: Public Repository Begins

[The initial public commit](https://github.com/huangruiteng/loopx/commit/7dcdc9dc79226d157ba57d3e8ff4bae664f020c1)
introduced the goal-harness scaffold. The core idea was already present: keep
long-running agent work anchored in explicit goal state instead of relying on
one transient session.

### 2026-06-17: A Contributor Work Surface Appears

[The contributor task board](https://github.com/huangruiteng/loopx/commit/7bb315246e3082965e2763530cf0da6d37c4e320)
made public, claimable work a repository surface. This established a path for
contributions beyond the creator's local development loop.

### 2026-06-21: The Product Becomes LoopX

[The LoopX product-surface rename](https://github.com/huangruiteng/loopx/commit/320fbedaa4d90bd02e5149a8fd9a46c9a498c650)
gave the control plane its current public name. Visual identity assets followed
on [2026-06-22](https://github.com/huangruiteng/loopx/commit/ac13d32ab5668b92ef64ddb00a9a41110ae3da76).

### 2026-06-23 to 2026-06-24: External Contributions Join The Main Line

Early public contributions included hardware-agent showcase documentation in
[#597](https://github.com/huangruiteng/loopx/pull/597) and Claude Code CLI LoopX
mode in [#604](https://github.com/huangruiteng/loopx/pull/604), both contributed
by [`liangsalt`](https://github.com/liangsalt). This period demonstrated that
the repository could accept product and documentation work through the same
public review path.

### 2026-07-02: The Public Release Archive Starts

[LoopX v0.1.3](https://github.com/huangruiteng/loopx/releases/tag/v0.1.3) is the
first retained entry in the current GitHub release archive. Later v0.1 releases
iterated on installation, control-plane contracts, validation, and public
documentation.

### 2026-07-10: Agent Coordination Moves To A Peer Runtime

[#1787](https://github.com/huangruiteng/loopx/pull/1787) removed agent hierarchy
as a runtime authority model. Claims became soft routing and writeback signals;
actual execution remained governed by quota, gates, capabilities, write scope,
and explicit handoff state.

### 2026-07-11: The v0.2 Control Plane Ships

[LoopX v0.2.0](https://github.com/huangruiteng/loopx/releases/tag/v0.2.0)
promoted the peer-agent runtime and expanded long-lived issue-fix, PR lifecycle,
Explore, and control-plane validation surfaces. The v0.2 line continues in the
public [release archive](https://github.com/loopx-project/loopx/releases).

## How This History Is Maintained

- Add a milestone only when it changes project identity, governance, the
  release line, or a durable product contract.
- Link every factual claim to a public commit, pull request, tag, or release.
- Use Git history and GitHub's contributor graph for contributor attribution;
  do not infer human identity or maintainer authority from commit counts.
- Keep private operating context, raw trajectories, internal links, and local
  paths out of the public timeline.

See [AUTHORS.md](https://github.com/loopx-project/loopx/blob/main/docs/project/authors.md) for
creator and contributor attribution and
[Governance](https://github.com/loopx-project/loopx/blob/main/.github/GOVERNANCE.md)
for the current maintainer model.
