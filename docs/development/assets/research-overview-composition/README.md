# Research composition evidence

These captures use the packaged Chat frontend with the real disposable SQLite,
history and saved-analysis HTTP owners. The input is the existing public-safe
[`research-display-language.mjs`](../../../../examples/personal-workspace-browser/research-display-language.mjs)
fixture: 20 questions, 10 findings, repeated bilingual content, negative evidence
and unknown judgments. Analysis outputs are canned; no model or solver runs.
Both versions read the same capture. The baseline is `7129efcde`.

The primary question is what happened across the recorded research window and
where its judgments and evidence can be read. Six independent judgments remain
prominent. Phase navigation no longer repeats large research/finding counters
and full ranges above the density plot. Exact phase ranges, research and decision
counts remain in the existing timeline help and accessible button names; failed
and tentative categories remain visible in the phase bars.

| View | Before | After |
| --- | --- | --- |
| Desktop, 1200 × 888 | [Default](before-desktop.png) | [Default](after-desktop.png) |
| Narrow, 390 × 1000 | [Default](before-narrow.png) | [Default](after-narrow.png) |

At the tested desktop width, all six judgments and the density plot fit in the
first viewport. On the narrow Chinese view, the judgments use two rows instead
of three. English retains two columns so labels remain readable; see the
[English narrow view](after-narrow-en.png). Layout responds to the available
content width. No body text was reduced to obtain this result.

The [lower timeline](after-timeline-lower.png) keeps routes, recorded negative
statuses, decision history and all-record access. The selected finding remains
a complete document on [desktop](after-reader-desktop.png) and
[narrow screens](after-reader-narrow.png), with its signed number, uncertainty
and original-evidence access. [Phase help](after-phase-help-narrow.png) works
with hover, keyboard focus or tap; Escape closes it and preserves focus.

The [refused analysis request](after-error-narrow.png) keeps its saved result,
partial-translation marker and retry cost consequence. Source-mismatched
translations also retain originals. These are presentation and interaction
checks, not evidence of translation quality, research effectiveness or benefit.
The narrow view still requires scrolling to the full timeline. Private installed
App readback is kept outside these public artifacts.
