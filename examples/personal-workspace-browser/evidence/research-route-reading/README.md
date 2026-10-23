# Research route reading evidence

Public synthetic data only: three routes, six questions, six tentative/refuted
findings, twelve events. The production packaged Chat UI reads the disposable
HTTP/SQLite history owner. Analysis readbacks are synthetic; no model or solver
runs. The fixture is defined in `../../research-route-reading.mjs`.
Fixture SHA256: `9b47d0d7f464d2c11352492db797607a12356f24828fad3f45af23dfb073f70d`.

Before: bundled source `3810d143ab18eb6b3d9f1452b548204cd1ed923b`; all affected
UI source bytes also match baseline `74651e3bec46c773f187f7eb2688962761860a12`.
After: the changed UI source set's SHA256 is `4d1e2af0a4c6a0fb07ec2dc59743c599bea24419be8498a39b5d1d5e222a22a8`
(sorted compact JSON of the three UI paths to their byte SHA256 values).
The reviewed commit containing these images identifies the complete source.
This is packaged browser evidence; installed App readback is recorded separately.

The primary question is whether a route's beginning and end are readable and
whether its controls stay aligned as analysis state and metadata change.
Default route entries wrap into two lines, with separate start/end identities;
one activation opens the complete original in the existing reader. Closing
returns keyboard focus to the same entry. The timeline marks, complete record
collection, negative states and source identity remain intact. View, analysis,
help and refresh share a control row; model/usage and selected scope use separate
rows. Optional methods and provenance stay in their existing disclosures.

| View | Before | After |
| --- | --- | --- |
| Desktop opening viewport | [before](before-first-1440.png) | [after](after-first-1440.png) |
| 390px opening viewport | [before](before-first-390.png) | [after](after-first-390.png) |
| Desktop routes | [before](before-routes-1440.png) | [after](after-routes-1440.png) |
| 390px routes | [before](before-routes-390.png) | [after](after-routes-390.png) |
| Complete selected original | — | [desktop](after-detail-1440.png), [390px](after-detail-390.png) |

The before captions permanently clip their end: measured natural widths
1155–1220px in 846px desktop / 284px narrow containers. Desktop controls differ
by 10px vertically; narrow controls also differ in height. The after regression
checks equal control top/bottom edges, separate metadata rows, 16px caption
wrapping, complete selected text, exact focus return, reload/Back and zero model
or source-write effects at 1440, 768 and 390px in Chinese and English. It covers
loading, fresh/view/reuse/continue actions, recorded-running and unavailable
states, including wrapping model metadata. All ten images were inspected as
whole viewports with long mixed content, rather than qualifying from geometry
alone. Whole-page generated translation and complete scientific cost/value
qualification remain separate work; these images establish neither.
