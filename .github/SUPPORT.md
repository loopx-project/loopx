# LoopX Support

LoopX is an open-source project supported on a best-effort community basis.
The project does not provide a response-time or resolution SLA for support
requests, and it does not currently provide commercial support through this
repository or its community channels. Pull-request review has published
targets instead; see
[Review Service Levels](GOVERNANCE.md#review-service-levels).

## Choose A Channel

| Need | Channel | Use it for |
| --- | --- | --- |
| Reproducible bug or installation failure | [GitHub Issues](https://github.com/loopx-project/loopx/issues/new/choose) | Public, sanitized reproduction steps for behavior that can be investigated or fixed in the repository. |
| Feature request | [GitHub Issues](https://github.com/loopx-project/loopx/issues/new/choose) | A concrete problem, desired outcome, alternatives, and the smallest useful product change. |
| Usage or design question | [GitHub Discussions: Q&A](https://github.com/loopx-project/loopx/discussions/categories/q-a) | Questions, configuration help, and design discussion that do not yet identify a repository bug. |
| Security vulnerability | [Private Vulnerability Reporting](https://github.com/loopx-project/loopx/security/advisories/new) | Suspected unpatched vulnerabilities. Do not post them in an issue, discussion, or chat. See [`SECURITY.md`](SECURITY.md). |
| Informal peer help | [Discord](https://discord.gg/XmGgQyCFZd) | Onboarding, workflow comparison, show and tell, and community conversation. Chat is not an authoritative support or release record. |

Public contributor work belongs on the
[Contributor Task Board](../docs/development/contributor-tasks.md) or in the contributor-task
issue form. The
[Technical Directions map](../docs/project/technical-directions.md) explains
which programs are active and what maturity or promotion gate applies. Pull
requests should follow
[`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Official Publication Sources

- [GitHub Releases](https://github.com/loopx-project/loopx/releases) is the
  authoritative source for published versions and release notes.
- [GitHub Discussions: Announcements](https://github.com/loopx-project/loopx/discussions/categories/announcements)
  is the authoritative source for project announcements that are not tied to
  one release. The pinned
  [Current technical directions and known limitations](https://github.com/loopx-project/loopx/discussions/2851)
  post is the community-facing projection of the versioned repository map.
- [GitHub Security Advisories](https://github.com/loopx-project/loopx/security/advisories)
  is the authoritative source for coordinated vulnerability disclosures.

Repository documentation describes the current product and contributor
contracts. Issues and pull requests are public work records, not general
announcement channels. Discord, the Lark group, and the WeChat contact listed
in the README are informal community channels; their messages do not replace a
release, security advisory, merged repository contract, or published
announcement.

## Account Authenticity

LoopX does not currently designate a standalone social-media account as an
official publication source. Reposts, screenshots, personal accounts, and
third-party communities may be useful, but they are not authoritative project
communications. When sources conflict, prefer the official GitHub source for
the relevant topic and ask for clarification in a public
[Discussion](https://github.com/loopx-project/loopx/discussions).

## Make A Useful Request

For a bug or support question, include the LoopX version or commit, host/runtime
surface, command or workflow, expected behavior, actual behavior, and the
smallest public-safe reproduction you can provide. Search existing issues and
discussions first.

Remove credentials, private repository content, raw agent sessions, local
runtime state, internal links, and sensitive logs before posting. If the
material cannot be made public-safe, do not upload it to a public channel.

Maintainers and community members may redirect requests to a better channel,
close duplicates, or ask for a smaller reproduction. A public request does not
guarantee investigation, a fix, a release date, or suitability for a
production deployment.
