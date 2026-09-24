# Welcome Wagon: From Reader to Participant in 30 Minutes

<!-- welcome-wagon:small-first-outcome -->

> **Complete one real small thing first.** You do not need to finish the Dev
> Book, understand the entire CLI, or modify the Kernel. Choose one route
> below, leave a verifiable result, and then decide whether to go deeper.

The Chinese and English pages are semantic mirrors. A material difference in sections, actions, commands,
link targets, or boundaries is a documentation defect.

<!-- welcome-wagon:four-routes -->

<div class="grid cards" markdown>

-   :material-play-circle-outline: **Run it once**

    Install LoopX, connect your own project, and inspect the first state.

    **About 15 minutes · Outcome: a recoverable Goal**

-   :material-message-text-outline: **Share feedback**

    Submit a sanitized first-run report, question, or longer usage story.

    **About 10 minutes · Outcome: actionable public feedback**

-   :material-source-pull: **Make a contribution**

    Claim one bounded task and complete the smallest change and validation.

    **About 45–60 minutes · Outcome: a reviewable PR**

-   :material-comment-check-outline: **Review something**

    Comment on an RFC, Issue, or PR with evidence before writing code.

    **About 30 minutes · Outcome: a review that advances a decision**

</div>

## Choose Your Finish Line {#choose-finish-line}

| What you want to do | Completion evidence | Start here |
| --- | --- | --- |
| Decide whether LoopX fits your project | `doctor` passes, project state is readable, and local state stays out of Git | [Route A](#run-once) |
| Tell maintainers what worked or blocked you | A minimal, public-safe, reproducible report | [Route B](#share-feedback) |
| Submit your first code or documentation change | Claimed task, bounded diff, validation, and DCO sign-off | [Route C](#first-contribution) |
| Participate in architecture or direction | Separate shipped facts from proposals and identify evidence, risk, or a smallest slice | [Route D](#review-path) |

The routes are independent. A user does not have to become a code contributor, and contributions are not
limited to Kernel changes. Reproductions, documentation, deterministic fixtures, community answers, and
design reviews all count.

## Shared Starting Point: Inspect Before Writing {#inspect-first}

<!-- welcome-wagon:inspect-before-write -->

Run these commands from the target project root:

```bash
loopx --version
node --version
loopx doctor
git status --short --branch
```

This checkout requires Python 3.11+ and Node.js 22.22.3+. If `doctor` fails, repair the installation
through [Installing LoopX](/loopx/docs/guides/installing-loopx/) before writing project state in a broken
environment.

<!-- welcome-wagon:public-private-boundary -->

Never put credentials, private project names, internal links, absolute machine paths, raw transcripts,
`.loopx/`, `.codex/goals/`, or unsanitized logs in public feedback or contributions.

## A. Run LoopX Once {#run-once}

<!-- welcome-wagon:run-first-goal -->

### 1. Install and inspect

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

Restart the Agent Host after installation so it reloads workflow skills. Run `loopx doctor --deep` when
you need to exercise the TypeScript Effect runtime. LoopX manages this runtime automatically; you do not
start a daemon yourself.

### 2. Connect a project you understand

```bash
cd /path/to/your-project
loopx connect --dry-run
loopx connect
loopx status
```

If the project has no Goal to continue, ask your current Agent to run:

```text
$loopx <a cross-session task with a verifiable finish condition>
```

When the Host has no native `/loopx` entry, follow the guided route in the
[Newcomer Command Path](/loopx/docs/guides/newcomer-command-path/).

<!-- welcome-wagon:verify-first-goal -->

### 3. Verify the outcome, not only the exit code

A successful first run should prove at least:

- `loopx doctor` checks the release, skills, and Effect runtime; for
  Host-specific checks, first use `loopx agent-onboard --list-agent-types` to
  select the exact type, then run `loopx doctor --agent-type <agent-type>`;
- `loopx status` shows the exact Goal, current Gate, and next Todo;
- `.loopx/`, `.codex/goals/`, and `.local/` remain outside Git;
- the current Host loop driver is active, or the output gives an explicit manual start step;
- Goal selection, identity takeover, credentials, and external writes stop at a Gate.

See [Connect an existing Git project](chapters/05-connect-existing-project.md) for the complete Agent
onboarding contract.

## B. Share One Real Experience {#share-feedback}

<!-- welcome-wagon:share-feedback -->

You do not need to fix code first. High-quality feedback is a contribution.

```bash
loopx first-run-report
```

The command prints a local environment summary and a prefilled Issue link. It sends **no telemetry**.
Review the draft and remove anything that should not be public before submitting it.

<!-- welcome-wagon:route-community-channel -->

| Your situation | Use this route | Include |
| --- | --- | --- |
| First installation or connection | [First-run feedback](https://github.com/huangruiteng/loopx/issues/new?template=first_run.yml) | Version, OS, Host, and completed steps |
| A run lasting hours or days | [Usage story](https://github.com/huangruiteng/loopx/issues/new?template=usage_story.yml) | Duration, capabilities, recovery, and outcome |
| Reproducible incorrect behavior | [Bug report](https://github.com/huangruiteng/loopx/issues/new?template=bug_report.yml) | Minimal reproduction, expected/actual behavior, sanitized diagnostics |
| Usage or design question | [GitHub Q&A](https://github.com/huangruiteng/loopx/discussions/categories/q-a) | Goal, current version, and attempted route |
| A public workflow or outcome | [Show and tell](https://github.com/huangruiteng/loopx/discussions/categories/show-and-tell) | What ran, evidence, and limitations |

For informal conversation, join [Discord](https://discord.gg/XmGgQyCFZd). Chat is useful for exploration;
final bugs, decisions, and reproducible conclusions should return to an Issue, Discussion, PR, or
versioned document.

## C. Make Your First Contribution {#first-contribution}

<!-- welcome-wagon:find-current-work -->

### 1. Start from current work

Read these in order:

1. [Current Technical Directions](/loopx/docs/project/technical-directions/) to identify whether a
   direction is shipped, incubating, research, draft, or held;
2. the [Contributor Task Board](/loopx/docs/development/contributor-tasks/) to choose a
   `Starter / Good First` task or another bounded task with design agreement;
3. [CONTRIBUTING](https://github.com/huangruiteng/loopx/blob/main/CONTRIBUTING.md) for setup, DCO,
   public/private boundaries, and validation.

The task board is a dynamic source of truth. This book does not copy its current rows. If a task has no
Issue, open one with the
[Contributor task form](https://github.com/huangruiteng/loopx/issues/new?template=contributor-task.yml)
to establish a public coordination boundary.

<!-- welcome-wagon:claim-bounded-slice -->

### 2. Leave a useful claim comment

A useful claim says:

```text
I plan to handle:
- Smallest outcome:
- Non-goals:
- Expected files or owner:
- Validation:
- Target base branch:
```

Do not duplicate a `Maintainer-owned` task. Ask whether it can expose an independent fixture,
documentation, accessibility, or public-safe replay slice.

<!-- welcome-wagon:deliver-clean-loop -->

### 3. Complete one clean loop

```text
problem
  -> canonical owner
  -> invariant
  -> smallest coherent change
  -> focused validation
  -> signed commit
  -> pull request
```

Good first contributions often include:

- documentation navigation, terminology, or locale parity;
- one missing negative case in an existing smoke;
- a public-safe synthetic fixture;
- CLI error or output consistency;
- a narrow parity check for an existing Capability or Host.

Do not confuse “easy to edit” with “safe to change.” Even a few lines should state the user outcome they
change and the evidence that proves it.

## D. Review An RFC Or Change {#review-path}

<!-- welcome-wagon:review-by-maturity -->

Start with the [RFC Index](/loopx/docs/architecture/rfcs/) and identify the material's status:

| Status | Useful participation | Do not |
| --- | --- | --- |
| Accepted | Check implementation, add negative cases, improve compatibility and docs | Recreate a second semantic owner |
| Active research | Improve experiment design, fixtures, attribution, and boundaries | Turn experimental results directly into default product claims |
| Draft | Review the problem, authority, non-goals, smallest slice, and validation | Start a broad implementation before a bounded task exists |
| Integration proposal | Add parity, characterization, and promotion evidence | Treat an integration branch as `main` truth |

A first review can answer only six questions:

1. Which user or maintainer problem does it solve?
2. Where is the current canonical authority?
3. Which statements describe shipped behavior, and which are proposals?
4. Does it change defaults, permissions, or the public/private boundary?
5. What is the smallest verifiable slice and the strongest counterexample?
6. After failure or rollback, which state remains trustworthy?

Cross-direction questions may enter an
[Open Strategy Review](/loopx/docs/community/open-strategy-reviews/). It produces a disposition, owner,
next artifact, and review trigger; it does not replace the RFC, Issue, or PR path with a meeting vote.

## Ask For Help Without Losing The Signal {#ask-for-help}

<!-- welcome-wagon:ask-with-signal -->

Include:

- `loopx --version`;
- the Host or runtime surface;
- the outcome you want;
- the smallest public reproduction;
- expected and actual behavior;
- the sanitized result of any read-only diagnostics you ran.

Do not paste a large log without context or say only that something “does not work.” A good question lets
another person reproduce the problem, locate the owner, and propose a next action. See
[LoopX Support](https://github.com/huangruiteng/loopx/blob/main/.github/SUPPORT.md) for channel and
response boundaries.

## Where To Go Next {#next-stop}

<!-- welcome-wagon:choose-next-depth -->

- To understand why a control plane helps, read
  [From one session to long-running work](chapters/01-from-session-to-loop.md).
- To connect your own project, read
  [Connect an existing Git project](chapters/05-connect-existing-project.md).
- To change LoopX, read the [Developer contribution map](chapters/source-protocol-map.md).
- To enter the Kernel, follow the [Control-Plane Developer Course](chapters/12-control-plane-course.md).

Complete one route before choosing the next. The Welcome Wagon is not meant to teach everything at once;
it makes the first real action safe, verifiable, and easy for the community to receive.
