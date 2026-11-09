# issue_fix_acceptance_loop_v0

`issue_fix_acceptance_loop_v0` is the first executable LoopX protocol for repo
issue fix work. Its goal is acceptance, not display: given a public issue/PR
metadata signal, the loop must prove that an agent can move from signal to a
validated fix artifact.

The initial implementation is a deterministic fixture command:

```bash
loopx issue-fix acceptance-fixture --format json
```

The command creates a temporary fixture workspace, runs a focused repro that
fails, applies a minimal code patch, reruns the same focused validation, and
returns an `issue_fix_validated_fix_artifact_v0`. The artifact is ready only
when the repro failed before the patch and validation passed after the patch.

The next fixture exercises the same repair path through a real temporary git
repository and issue branch:

```bash
loopx issue-fix repo-branch-fixture --format json
```

It initializes a local fixture repo, commits the failing baseline, creates
`codex/issue-123-public-metadata-fixture`, runs the repro, patches the branch,
reruns validation, confirms the branch-local patch diff without exposing raw
git output, and returns the same validated fix artifact shape with an extra
`issue_fix_repo_branch_artifact_v0` section.

The promoted caller-approved branch mode moves the same contract onto a real
local repository selected by the caller:

```bash
loopx issue-fix caller-repo-branch \
  --repo-path /path/to/approved/repo \
  --url https://github.com/owner/repo/issues/123 \
  --base-branch main \
  --validation-command "python test_calculator.py" \
  --validation-label "python test_calculator.py" \
  --execute \
  --format json
```

This mode creates or claims a `codex/` issue branch, runs only the
caller-declared validation command, and returns
`issue_fix_caller_repo_branch_packet_v0`. The public packet records the repo
label, issue branch, validation pass/fail, repo-relative changed files, and PR
review readiness. Before creating a new issue branch, it records a typed base
snapshot, requires the approved local base branch to match its local tracking
ref, creates from that immutable revision, and reads the new branch back against
the snapshot. A stale, ahead, or diverged base fails closed before branch
creation. An already-created issue branch remains claimable when the old base
branch is no longer present. The command never refreshes remote refs
implicitly; callers that need new remote evidence must fetch and reconcile it
explicitly. It does not expose the local repo path, validation stdout or stderr,
raw issue body/comment content, external remotes, or raw git output.
Git pathnames that are valid UTF-8 are preserved literally. A pathname
with invalid UTF-8 is shown only as a `git-bytes:<repr(bytes)>` diagnostic;
the artifact records `changed_files_unrepresentable_count`, and
`review_packet.ready` stays false because that diagnostic is not an
openable repo-relative UTF-8 pathname. Reviewers must inspect the local
checkout before proceeding.
Without `--execute`, the command is a dry-run plan and does not inspect or
modify the local repository.

## Product Contract

The user-facing value is the validated repair path:

1. public metadata intake establishes the repo/issue signal without copying
   issue body text or comment body text;
2. a repro command proves the bug is currently present;
3. a code route names the files and reason for the minimal patch;
4. the patch is applied in the fixture workspace;
5. focused validation passes;
6. a PR-review packet is ready, but no external comment, PR creation, merge, or
   publish action is performed by this fixture.

For caller-approved local repositories, PR-review readiness is true only when
the issue branch exists or is claimed, caller-declared validation passes, and
there is repo-relative change evidence. External issue comments, PR creation,
merge, and publish actions remain separate explicit caller decisions.

This keeps the protocol useful for automation while preserving safe defaults.
The packet is evidence of a completed repair loop, not a substitute for the
repair loop.

## Public-Safe Fields

The fixture packets must report:

- `external_reads_performed: false`
- `external_writes_performed: false`
- `issue_body_captured: false`
- `comment_bodies_captured: false`
- `local_paths_captured: false`
- `private_repo_state_read: false`
- `destructive_git_used: false`

Caller-approved repo mode differs in one narrow way: when `--execute` is used,
`private_repo_state_read` is `true` because LoopX inspects the caller-approved
local git repository. That packet must still keep `local_paths_captured: false`,
must summarize validation stdout/stderr, and must not perform external issue
comments, PR creation, merge, publish, or destructive git.

Validation command output is summarized with pass/fail and exit code only. The
fixture does not expose stdout, stderr, local temporary paths, or raw provider
payloads in the artifact.

## Next Promotion

The next implementation step is agent-applied patch orchestration on top of the
caller-approved repo branch mode: after the branch is prepared, the project
agent should choose the minimal code route, apply a patch, rerun the declared
validation, and then use the PR-ready packet as review evidence. External
comments, PR creation, merge, or publish actions still require explicit caller
action.

## Smoke

The durable smoke is:

```bash
python3 examples/issue-fix-acceptance-loop-smoke.py
```

It exercises the CLI, checks the failure-before/fix-after validation sequence,
and rejects local path exposure in the public artifact.
