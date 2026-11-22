# loopx-repo-health

Public-safe GitHub repo-health snapshot provider for LoopX. It collects a
bounded set of public repository metrics from the GitHub REST API and emits a
frozen `repo_health_snapshot_v0` document that can feed monthly reports,
community-funnel monitoring, and content material.

## Metrics

- Counts: stars, forks, watchers, open issues, releases, contributors, sampled
  PR total, sampled commented-issue total.
- Traffic (14-day window): repo views, clones, top paths, and derived docs
  views (requires repository access).
- Latency: PR merge p25/p75 and first-human-response p25/p75, computed from a
  bounded recent sample.
- Recent star timeline: weekly buckets from the newest stargazer pages.

Every metric comes from public repository data. The provider never accepts or
emits raw trajectories, benchmark evidence, credentials, or private links.

## Metric model

The snapshot is organized into two groups so reports and digests can stay
plain-language while remaining CHAOSS-aligned.

Adoption and awareness (easy to read, best for recaps):

| plain language | snapshot field | CHAOSS alignment |
| --- | --- | --- |
| people who visited the repo page | `traffic_14d.views.uniques` | activity/attention proxy |
| people who read docs or README | `traffic_14d.docs_views.uniques` | documentation activity proxy |
| people who cloned the repo | `traffic_14d.clones.uniques` | adoption/trial proxy |
| stars / forks / watchers | `counts.stars/forks/watchers` | popularity/social proof |

Community health (CHAOSS working-group aligned):

| plain language | snapshot field | CHAOSS alignment |
| --- | --- | --- |
| active contributor count | `counts.contributors` | bus factor input |
| time to first human response | `latency.first_response_*` | time-to-first-response |
| PR merge turnaround | `latency.pr_merge_*` | code review latency |
| release cadence input | `counts.releases` | release frequency input |

`traffic_14d.docs_views` is derived from the top paths returned by GitHub with
one classification rule: README, `docs/` tree (including blob/tree links), and
`wiki/` tree. Its `uniques` is the sum of per-path uniques and is not
de-duplicated across paths. Consumers must not reinterpret missing metrics as
zero; unavailable traffic surfaces are reported as zero with a warning.

## Auth and boundaries

- Read `GH_TOKEN` or `GITHUB_TOKEN` from the environment; the request packet
  never carries credentials.
- Unauthenticated requests still work for most endpoints, but GitHub now
  requires authentication for the stargazers list, so a token is recommended.
- Rate limits are GitHub's; the collector reports `rate_limit_remaining` in
  evidence and fails fast with an explicit error instead of guessing.
- Latency metrics are samples, not full-history percentiles; the snapshot
  records the sample size in `evidence.warnings`.

## Usage

Managed extension invocation (stdin request → stdout response):

```bash
python3 -m pip install .
loopx extension install --manifest extension.toml --execute --format json
loopx extension run loopx-repo-health --input-json examples/request.json --execute --format json
```

Direct CLI:

```bash
loopx-repo-health --doctor
loopx-repo-health snapshot --owner loopx-project --repo loopx --format json
loopx-repo-health snapshot --owner loopx-project --repo loopx --format md
```

`schemas/request.schema.json` and `schemas/response.schema.json` are the
versioned wire contracts. The provider validates its output against
`repo_health_snapshot_v0` before returning it.

## Validation

```bash
python3 smoke/repo_health_snapshot_smoke.py            # offline contract smoke
python3 smoke/repo_health_snapshot_smoke.py --live owner repo   # optional live check
```
