# C1 journal source-fidelity checkpoint

中文镜像：[同名检查点](2026-10-29-journal-source-fidelity.zh-CN.md)。

C1 integrates main `a651a824996cce29cc69a5e7e4437b241eef963d` and reuses the
independent normal [PR #7657](https://github.com/loopx-project/loopx/pull/7657),
source `c7042f28e01bae93f7960ad165fe6f4d2d847bed`. This is proposed source
integration, not a merged or installed rollout.

The demonstrated failure is persistence of the complete source through the
existing Turn journal: JavaScript serialization changed `1.0` to `1`, discarded
negative zero and rounded large integers. Python's ordinary equality hid the
first difference. An exact JSON comparison in the production native Turn path,
using a synthetic host, fails before the repair even though the original capture
remains intact. A source-number hash trial also exposed numeric normalization in
context reconstruction; isolated hash-refusal cases did not qualify that trial.
The trial is not adopted and historical hash codecs are unchanged.

Python now transports its original journal JSON as text. The existing TypeScript
journal owner parses and checks that snapshot, including its lifetime, effect,
transition and lock boundaries, then publishes the same text through the existing
atomic writer. Native object callers keep their previous encoding. There is no
second transition owner, new journal schema, historical rewrite or permission
relaxation. C1 reuses that implementation rather than restoring source numbers
in individual recovery callers.

On the independent slice, four numeric cases fail on unchanged main; all six
source-number cases and the existing lifetime cases pass after repair. The
journal/executor/recovery cohort passes 178 Python cases, another 106 production
CLI capture/recovery cases use synthetic hosts, and 53 TypeScript cases pass.
Typecheck, Ruff, public-boundary scan and standard local premerge pass on its
source. C1's final exact-head integration results remain on its PR.

One RPC remains one RPC. With the same quoted/newline synthetic body, request
parameters grow from 9,837 to 11,002 bytes at 8 KiB, and 71,708 to 80,418 bytes at
64 KiB. Compact disk output is smaller, but wire escaping increases transport
and memory cost and reduces headroom under the unchanged 2 MiB request limit.
No latency, model-token or long-horizon net benefit is claimed.

Continue C1 with one coherent numeric source identity through ordinary, warm and
missing-baseline context, then hash and caller/writer cutover. Keep the original
quota single-session semantic/outcome/full-cost comparison and frozen budget
failure open; optional two-session work stays separate. This prerequisite is an
independently useful recovery correction, not completion of those requirements.
