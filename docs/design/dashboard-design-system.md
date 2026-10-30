# Dashboard design system

The workspace UI is governed by a small set of tokens and rules. Change the
tokens, not the call sites. This page is the reference a design review uses; if a
screen disagrees with it, the screen is wrong.

For the product-level layout and interaction decisions, see
[`apps/presentation/dashboard/design.md`](../../apps/presentation/dashboard/design.md).

## Where the tokens live

| Token group | File | Scope |
| --- | --- | --- |
| Colour, shape, spacing | `apps/presentation/dashboard/src/features/personal-workspace/personal-workspace.css` (the token block at the top) | every workspace surface |
| Type scale | inherited `--text-*` custom properties | set by the shell |
| Board sizes | the task-kanban block in `personal-workspace.css` | the Task board only |

## Shape

Three radii. Nothing else.

```
--pw-r-control: 6px   inputs, small buttons, segmented controls
--pw-r-item:    8px   list items, small cards, menu rows
--pw-r-card:   12px   panels, drawers, large cards
```

`--pw-r-pill: 999px` is only for status chips and counters.

The workspace used to carry 9 / 10 / 11 / 13 / 14 / 99 px at the same time.
Those were normalised; do not reintroduce one.

## Spacing

```
--pw-s-1:  4px   inside a chip, between an icon and its label
--pw-s-2:  8px   between rows in a list
--pw-s-3: 12px   inside a card, between a label and its control
--pw-s-4: 16px   between sections inside a panel
--pw-s-5: 24px   between major blocks on a page
--pw-s-6: 32px   page-level separation
```

## Type

| Size | Role | Rule |
| --- | --- | --- |
| 28 | page title | one per page |
| 20 | section title, Goal name | |
| 16 | card title | |
| 14 | body, row text | the default |
| 12 | secondary text, labels, counters | |
| 11 | timestamps and counts | tabular numbers |

A row is 40px tall. Text is one line and truncates; the full value belongs in a
tooltip or the detail panel, never in a second line that makes the row taller.

## Status colour

Colour encodes **status only**. Category is carried by text or an icon, never by
a second colour.

| Token | Meaning |
| --- | --- |
| `--pw-amber` | waiting on the owner |
| `--pw-red` | failed, blocked |
| `--pw-blue` | running |
| `--pw-muted`, `--pw-faint` | quiet, no action |
| `--pw-green` | completed |

Every coloured dot is accompanied by a word. Colour alone is not a state.

## Components

**Row.** One line, 40px: status dot or mark, primary text, secondary metadata,
trailing time or count. Truncate with `text-overflow: ellipsis`; the full text
goes in `title`.

**Card.** White surface, 1px `--pw-line` border, `--pw-r-card`, no resting
shadow. Title, then at most two lines of content, then a meta row.

**Toolbar.** One row: title, search, a single filter control, view switch on the
right. Filters are not a row of pills; a select carries the current value.

**Empty state.** One line inside a dashed border, or a plain muted line. It never
takes a third of the screen, and its explanation never hides behind a `?` icon.

**Hints.** If a sentence can carry the meaning, write the sentence. A help icon
is reserved for values that are genuinely ambiguous, and it must sit next to
visible text.

## Flow

The Goal page reads top to bottom as numbered stages:

```
01 Current state → 02 Needs you → 03 Now → 04 Delivery and evidence
```

The stage bar above the document shows Plan → Run → Accept with the current stage
filled. A section that belongs to the sequence is never moved into a side rail.

The home page shows the run band: one segment per recent run, colour is outcome.
Counts and tiles never replace the band, because they cannot show order.

## Adding a screen

1. Reuse the tokens. If a value is missing, add one to the scale — do not
   introduce a one-off.
2. Keep one line per row and one control per job.
3. Empty is a line, not a panel.
4. Number the stages if the page is a flow.
5. Run `node scripts/check-css-custom-properties.mjs dashboard` before pushing;
   undefined custom properties fail CI.
