# Reading observations and interpretations

The same saved finding must distinguish its observation/evidence from its
interpretation and competing explanations in ordinary and deep reading.
Previously ordinary expansion had unlabeled paragraphs, and deep reading hid
its only column labels at narrow widths. Each finding now retains its own
labels, with citations beside the observation.

## Reproduce

Use `research-synthesis-readback.json` through the existing `research-history`
browser scenario and its disposable real SQLite/HTTP reader. Reading uses saved
synthetic provider output; it dispatches no model and starts no research work.
The complete packaged scenario passed, including citation return, exact related
episodes, language switching and read-only failure/recovery cases.

Before: `8a9284079aae2613acc1119b3f77e823abe75f91`; the touched component, stylesheet
and synthetic fixture are byte-identical to target main
`ce58c9141b4b805d4189f89959ade1be1d41ef40`.
After: code commit `3add7e2bc`; subsequent evidence-only changes do not alter it.
Both are packaged source builds, not installed App qualification.

| View | Before | After |
| --- | --- | --- |
| Deep report, 1280×900 | [Desktop](finding-account-before-desktop.png) | [Desktop](finding-account-after-desktop.png) |
| Deep report, 390×844 | [Phone](finding-account-before-phone.png) | [Phone](finding-account-after-phone.png) |
| Ordinary expanded finding | Unlabeled paragraphs | [Desktop](finding-account-ordinary-desktop.png), [phone](finding-account-ordinary-phone.png) |

## Visual judgment

The report remains the reading anchor; the desktop comparison stays in two
columns. On the phone, observation, citation, interpretation and alternatives
remain identifiable when stacked. Later findings keep their own labels, and
unknown retention remains visible. Ordinary findings still start collapsed.
The body remains 16px; neither viewport overflows horizontally. No new panel,
control, banner, color token or mandatory step was added. The original
whole-page results → report → history journey, experiment tab and source
reader remain available. This establishes the display distinction and author
visual inspection, not independent scientific validity or measured user
comprehension improvement.
