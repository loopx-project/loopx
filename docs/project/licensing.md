# LoopX Licensing

LoopX uses the Apache License 2.0 as the primary license for its unified open
source core beginning with `v0.4.8`. The complete current license is in
[`LICENSE`](https://github.com/loopx-project/loopx/blob/main/LICENSE), and the
attribution notices distributed with the project are in
[`NOTICE`](https://github.com/loopx-project/loopx/blob/main/NOTICE).

## Version Boundary

- Releases and tags through `v0.4.7` remain permanently available under the
  MIT License that governed those releases.
- Beginning with `v0.4.8`, the unified LoopX open source core is distributed
  under Apache-2.0.
- The previous MIT license text and its copyright notice are preserved
  verbatim in
  [`LICENSE-MIT`](https://github.com/loopx-project/loopx/blob/main/LICENSE-MIT).
  They continue to document and govern the rights already granted for
  historical MIT releases and portions first distributed under those terms.

Changing the license of a current distribution does not revoke or narrow the
permissions already granted for an earlier release. Users may continue to use
an older LoopX release under its original MIT terms.

## Why Apache-2.0

Apache-2.0 keeps the core permissive for commercial and non-commercial use
while adding an explicit patent-license framework for covered contributions.
That clearer framework is useful when model providers, infrastructure teams,
and large enterprises adopt or contribute to LoopX as shared long-horizon
agent infrastructure. The change is intended to make ecosystem collaboration
easier, not to restrict commercial use.

The transition does not manufacture a retroactive Apache patent grant from
people who contributed solely under MIT terms. The patent-governance value of
Apache-2.0 accumulates primarily through code distributed in the new release
line and through future contributions submitted under the Apache-2.0 and DCO
terms. Historical copyright and attribution records remain intact.

## Contributions After The Transition

Unless a contributor explicitly states otherwise before submission, future
contributions accepted into LoopX are submitted under Apache-2.0, as described
by section 5 of that license. Each commit in a pull request must also carry a
Developer Certificate of Origin 1.1 sign-off. See
[`CONTRIBUTING.md`](https://github.com/loopx-project/loopx/blob/main/CONTRIBUTING.md)
and [`DCO`](https://github.com/loopx-project/loopx/blob/main/DCO) for the exact
workflow and certification.

No contributor license agreement or copyright assignment is introduced by
this transition. Copyright remains with contributors for their respective
contributions.

## Reuse, Ports, And Attribution

LoopX was created by [`@huangruiteng`](https://github.com/huangruiteng).
The [authors and contributors record](authors.md) links to the public history;
[`NOTICE`](../../NOTICE) preserves project attribution in distributions.
Creator credit identifies project provenance. It does not assign other
contributors' rights to the creator or determine ownership of a particular
contribution.

Apache-2.0 permits forks, modifications, and derivative distributions,
including commercial ones, subject to its terms. For redistribution under
Apache-2.0, [section 4](https://www.apache.org/licenses/LICENSE-2.0#redistribution)
requires you to:

- provide recipients with a copy of the license;
- place prominent notices in modified files stating that you changed them;
- retain applicable copyright, patent, trademark, and attribution notices in
  the source form of distributed derivative works; and
- include a readable copy of the applicable attribution notices from `NOTICE`
  in one of the locations allowed by section 4(d).

You must not use AI generation, code rewriting, cross-language translation,
or project renaming as grounds to remove or evade copyright, attribution, or
NOTICE retention requirements under the applicable license. If the distributed
material is a derivative work of LoopX, those obligations still apply. This
clarification does not restrict modifications or redistribution permitted by
the applicable license.

Whether a particular implementation is a derivative work depends on its facts;
this guidance does not classify every independent implementation of similar
ideas as a derivative.
Historical MIT material and separately licensed third-party components remain
subject to their applicable terms.

An upstream repository link and a description of the changes help readers
understand a fork's provenance. These are recommended practices, not additional
license conditions. Follow the [name and marks guidance](trademarks.md) when
presenting a fork or integration; software reuse does not establish project
endorsement or an official LoopX release.

This guidance does not add a blanket ban on rewriting, porting, or
re-open-sourcing, or require separate maintainer permission for uses already
permitted by the applicable license. `LICENSE`, historical license grants, and
third-party notices continue to govern; neither README wording nor `NOTICE`
amends those grants.

## Open Core And Commercial Boundaries

This repository's unified open source core is Apache-2.0. A separately
delivered Managed Control Plane, enterprise-only modules, hosted operations,
or implementation and support services may use separate commercial terms when
they are not part of this repository's Apache-2.0 core distribution.

Apache-2.0 itself permits commercial use, modification, and distribution
subject to its terms. The separate project-name and logo guidance in
[`TRADEMARKS.md`](https://github.com/loopx-project/loopx/blob/main/docs/project/trademarks.md)
still applies; the software license does not create endorsement or trademark
rights.

This document explains the project's public licensing policy and is not legal
advice.
