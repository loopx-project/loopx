# Existing Agent Session calendar report journey

The `session-*` captures use the packaged frontend and real local HTTP, Core,
native SQLite Task/Turn and periodic-report owners. Model and provider
transports, Goal and conversation data are synthetic. No production account,
message, workspace or report is shown.

- Baseline frontend: `c1a589aac771b39df9137dc808280c2a33fb4cbd`.
- Changed source and packaged runtime: `1fe1582cf8ad136b0df0f0c76d1e86cfc3232849`.
- Settings desktop: 1200×888; narrow settings/report: 390×844 viewport emulation.
  This is browser evidence, not physical-phone acceptance.

The primary question is where this Goal's scheduled report will arrive.
The selector replaces a raw route with an existing bound Agent conversation.
The default view shows saved scheduling, honest execution/delivery state and
the verified full-report link. Profile, timezone and the existing calendar
remain together; optional general capability help stays on demand. The
shared settings panel still needs vertical scrolling to reach save controls
at these viewport heights; that broader layout is outside this change.

The journey verifies:

1. Save an enabled future first date; the sole existing wake owner makes no
   model request before the deadline.
2. Preview/apply an eligible calendar in the same original Session. The
   existing worker calls the synthetic model once and settles one native
   report generation. An exact full-body Core readback precedes cursor commit.
3. Reload shows verified delivery. The existing full-answer viewer opens that
   original message, including all five sections of the 27,750-character
   synthetic report and the existing Copy Markdown control. The deliberately
   repeated long content checks page composition, not authored-report quality.
4. Blocking only the read-only report-health request produces the visible
   unknown state and Read again action. Restoring that request reads the same
   delivered body; it does not generate another report.
5. Pause through the same preview/apply controls. The original body, task,
   actor and history remain; the model-call count stays one.

Separate packaged failure evidence and File/SQLite tests preserve invalid
authored output, forbid stranding a frozen undelivered window and resume only
eligible native settlement effects without model replay. This slice does not
provide invalid-window cancellation, arbitrary profiles, natural-language
authoring or the complete managed/attached Agent workspace.

To run the disposable fixture:

```sh
uv run --extra test python examples/personal-workspace-browser/steward_report_fixture.py \
  --managed-report --output-dir /tmp/loopx-managed-session-report-qa
```

Use its returned URL. The fixture's `start-existing-owners` control starts its
single existing wake service. `--invalid-editorial` selects a known failed
synthetic author response for failure inspection. Closing the fixture removes
its disposable data; it does not alter an original service or external chat.
