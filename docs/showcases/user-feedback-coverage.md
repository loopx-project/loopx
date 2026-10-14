# User Feedback Coverage Map

This map records how the owner-approved user-manual feedback set was translated
into public Showcase material. It prevents repeated imports from producing
duplicate cases and preserves useful negative decisions without publishing the
private source URL or raw internal discussion.

| Stable source key | Public disposition | Showcase mapping | Reason |
| --- | --- | --- | --- |
| `cpp-algorithm-accuracy` | Promoted | [13+ hour C++ algorithm accuracy run](cases/independent-cpp-accuracy-long-run.md) | Concrete problem, >13h runtime, intervention statement, reported result, and a public method reference. |
| `four-day-unattended-agent` | Promoted | [Four-day unattended agent run](cases/independent-four-day-unattended-agent.md) | Concrete duration, zero reported intervention, usefulness judgment, and an inspection surface. |
| `public-engine-refactor` | Promoted | [Public Engine refactor](cases/independent-public-engine-refactor.md) | User report plus one public issue and seven merged PRs. |
| `small-request-relief` | Feedback signal only | Not a standalone case | Positive satisfaction signal, but no duration, intervention, or inspectable outcome. |
| `ecommerce-24x7-interest` | Not promoted | Not a realized case | Expresses interest in seller analytics and competitor monitoring; it does not report a completed LoopX deployment. |
| `peer-harness-assessment` | Not promoted | Not a LoopX usage case | Positive assessment of the vision and a comparable harness, but not evidence that the reported workloads used LoopX. |

## Later Public Sources

These sources were added separately from the six-cluster manual import above.

| Stable source key | Public disposition | Showcase mapping | Reason |
| --- | --- | --- | --- |
| `vllm-pr-54951-public-workflow` | Promoted | [vLLM long-context optimization](cases/independent-vllm-long-context-optimization.md) | October 13, 2026 author posts explicitly attribute the workflow engine to LoopX; vLLM PR #54951 independently establishes the upstream merge. Measurements retain their reported configuration and limits. |

## Import Boundary

- Source revision: owner-approved manual revision `813`.
- Published chat screenshots are owner-approved message excerpts from the
  LoopX public Lark developer group; that source attribution does not make the
  underlying projects, run state, or unselected chat context public.
- Public assets are local, compressed, and contain no authenticated image URLs.
- The four-day excerpt was cropped to remove chat identity and internal
  reporting context.
- Internal group screenshots, raw chat exports, contact details, credentials,
  and private links are not published.
- A future sync should update an existing source key or explicitly supersede it;
  it should not create a second card for the same evidence cluster.
