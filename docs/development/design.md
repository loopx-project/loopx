# LoopX Design System

This file is the canonical visual design contract for LoopX user interfaces.
Read it before changing the public website, dashboard, desktop application,
documentation chrome, prototypes, screenshots, or any UI reproduction task.

The visual direction adapts the black-and-white precision, Geist typography,
hairline surfaces, and restrained accent system documented in the
[Vercel docs/development/design.md reference](https://getdesign.md/vercel/design-md). LoopX is
not affiliated with Vercel. The reference is an inspiration and token source;
this document owns the LoopX-specific decisions.

## Product Character

LoopX should feel:

- precise, calm, and engineered;
- readable before decorative;
- monochrome by default, with color reserved for state and one controlled hero
  accent;
- dense enough for operators without becoming visually noisy;
- consistent across marketing, dashboard, and future desktop surfaces.

The interface should read like excellent technical documentation that also
communicates a confident product.

LoopX's value to an owner is delegating long-running work to Agents without
losing sight or control of it. Every surface should answer, at a glance, what
is moving, what is waiting on the owner, and what can be trusted, and let the
owner act on the answer in place. The same honesty is what makes LoopX credible
to people outside the project: a screenshot of LoopX should never overstate
progress.

### Earn The User's Attention

Every visible element must provide at least one of these:

1. **High-value information** that changes what the user understands or decides.
2. **An essential interaction** needed at this point in the user's journey.
3. **Expressive visual presentation** worth seeing: a clear composition, useful
   visual comparison, or purposeful motion that improves understanding or delight.

This is an OR rule, not a demand for three justifications per element or for
maximum minimalism. Decorative density, raw protocol fields, duplicated status,
and a button for every available capability do not earn attention by existing.
Judge the whole viewport, including information repeated across components.

Before implementation, inventory the visible elements and their value. Keep
conversation, results, and decisions prominent; consolidate routine activity;
offer one-step access to relevant controls and details without a chain of pages.
Start from the owner's original task and minimize the whole job they must do.
Agent preparation should absorb discovery, identifiers, formatting and execution
detail within existing authority. The owner supplies missing intent and real
decisions. Compare a smaller lawful route before justifying individual controls:
a sequence of locally useful fields can still assign the wrong work to the user.
Reuse prior scoped authorization; explain any remaining command effect at its
decision point. Advanced configuration must remain optional for an ordinary task
when an Agent can prepare the same reviewable plan.
Count mandatory interaction rounds from the original intent to a usable result,
including preparation outside the visible page. One round is one user statement,
choice or confirmation required to continue; merging buttons does not merge
decisions. Most ordinary capabilities should complete in one round. With intent
and scoped authorization already present, aim for zero additional rounds.
Two or more rounds need a concrete missing fact, consequential choice or authority
boundary for each extra round. High value may justify deeper collaboration, but
does not itself justify mandatory friction. Keep voluntary exploration separate,
and record navigation, copying, waiting and recovery effort alongside round count.
Do not collapse failures, missing authority, uncertain observations, or actions
requiring judgment into a reassuring success summary. Read models select facts;
they must not invent execution, acceptance, or completion from prose or counts.

Owner-facing state follows four rules:

- **Decide in place.** When LoopX already owns the authority for an owner
  decision, the surface performs it: choose, confirm once with the stated
  effect, then read back the recorded outcome and its effect on dependent work.
  Do not hand the owner a CLI command to copy, a generic "apply" step, or a
  control whose effect differs from its label. When an action is truly
  unavailable here, say why in plain language and name where it can be done.
- **Liveness comes from observation.** Live treatment (a pulsing dot,
  "executing", "running") requires an observed active worker, session or turn.
  A stored status, a registration or a count is not activity; unknown stays
  visibly unknown.
- **Group by responsibility.** Collaboration surfaces place work under the
  member, Agent or owner responsible for it, so one glance shows who is doing
  what and who is blocked. Work with no resolvable owner goes into an explicit
  "other" group; it is never dropped.
- **Owner language first.** Primary copy uses the owner's words. Protocol ids,
  gate kinds, preview mechanics and executor profiles belong in a collapsed
  diagnostics disclosure.

Review realistic populated, quiet, blocked, and unavailable states on desktop
and mobile. Show before/after views, name what earns attention and what was
consolidated, and verify keyboard access and return to the original context.
Put the actual first viewport directly in the PR description's visual evidence
section, with its revision and data/build boundary. Follow the repository's
[First-Screen Review Evidence](../../AGENTS.md#first-screen-review-evidence):
ordinary authorized work reuses prior direction without another user approval;
refresh evidence when the visible result changes.

Review proportions as well as typography. Within a workspace view, align the
greeting, summaries, work list, and composer to one content frame. An overview
board may be wider than a conversation's reading column. Keep ready-state
runtime details alongside their model context, allow failures to expand, and
size empty summaries to their content rather than filling large decorative cards.

### Explanations On Demand

The primary view presents the result, its unit and scope, material uncertainty,
and the next useful action. A truthful interface does not need to repeat its
methodology in every component. Review the whole viewport for repeated scope,
source, method and limitation paragraphs before adding another note.

- Keep the smallest qualifier needed to read a value correctly beside it:
  selected sample, partial window, uncalibrated, unknown or historical. Keep
  failed reads, missing authority and consequences such as separate model usage
  visible at the affected value or action. Never hide these in help.
- Put optional definitions, denominator rules, sampling caveats and chart-reading
  explanations in one nearby information disclosure per related region. Prefer
  one shared explanation to an icon beside every metric or repeated footnotes.
  Put long provenance and diagnostic fields in the existing collapsed details.
- The same help must work on hover, keyboard focus and click/tap. It must remain
  readable when the pointer enters it, dismiss with Escape or an outside click,
  fit a narrow viewport, and not trigger the operation it explains. Do not rely
  on a native `title` alone, color alone or hover-only access.
- Preserve actual unknowns, denominators, negative results and evidence links.
  Reduce explanatory noise without replacing missing evidence with a reassuring
  summary. Keep chart keys that are necessary to decode the marks visible.

For example, research metrics can show “19 episodes · purposive sample ·
uncalibrated” once, with a nearby explanation of yes/eligible counts and unknowns.
They should not repeat a whole-study exclusion paragraph above and below the
same six values. A source-read failure still appears directly beside the values.

中文要点：结果、单位、必要范围标记与异常直接可见；范围、方法、分母和读图的重复
解释就近按需展开。整页去重，保留图例及真实未知；悬浮、键盘和触屏使用同一入口，
不能把失败、权限缺失或额外用量藏进提示。

## Source Of Truth

- Use this file as the default visual contract for all new LoopX UI work.
- Preserve existing product behavior, accessibility, information hierarchy,
  and public/private boundaries.
- When a task provides an explicit approved design source, screenshot, or
  Figma file, match that source while using these tokens for unspecified
  details.
- Do not introduce a second design language for one page or framework.
- Keep reusable tokens and primitives framework-neutral. React, static HTML,
  and future desktop applications should express the same system.

## Color

### Core Palette

| Token | Value | Role |
| --- | --- | --- |
| `--color-ink` | `#171717` | Primary text, primary CTA, darkest chrome |
| `--color-body` | `#4d4d4d` | Body copy and secondary navigation |
| `--color-muted` | `#8f8f8f` | Metadata, captions, low-emphasis copy |
| `--color-faint` | `#a1a1a1` | Placeholder and disabled text |
| `--color-canvas` | `#fafafa` | Default application and page background |
| `--color-surface` | `#ffffff` | Cards, inputs, menus, elevated panels |
| `--color-surface-soft` | `#f2f2f2` | Inset wells and subtle alternate bands |
| `--color-border` | `#ebebeb` | Default 1px structural hairline |
| `--color-link` | `#0070f3` | Links, focus, selected informational state |
| `--color-danger` | `#ee0000` | Destructive or invalid state |
| `--color-warning` | `#f5a623` | Caution state |

Use near-black rather than pure black for standard text. Use pure black only
inside code or media surfaces where the stronger contrast is intentional.

### Accent Gradients

Color is a controlled accent, not general chrome:

- Develop: `#007cf0` to `#00dfd8`
- Preview: `#7928ca` to `#ff0080`
- Ship: `#ff4d4d` to `#f9cb28`

Marketing pages may blend these stops into one soft hero mesh. Do not repeat
the mesh in every section. Product and operator surfaces should prefer solid
semantic colors and monochrome structure.

### Dark Surfaces

Dark mode is an inverse of the same system, not a separate visual identity:

- use near-black canvas and slightly lifted neutral surfaces;
- retain the same spacing, radius, typography, and hierarchy;
- keep borders subtle and neutral;
- preserve semantic meaning and contrast;
- do not add neon glows, glossy gradients, or decorative shadows.

## Typography

Use **Geist Sans** for UI and prose and **Geist Mono** for code, data, compact
technical labels, and section eyebrows.

Fallbacks are owned per surface, because each bundles its own font files. A
stylesheet must reference the token rather than repeat the stack, and the token
must be defined on the surface that uses it — a bare `var(--font-mono)` with no
fallback drops the whole declaration when the token is missing, and the element
silently inherits the body face instead of failing visibly.

Dashboard (`apps/presentation/dashboard/src/styles.css`):

```css
--font-sans:
  "Geist Variable", "Geist", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
  sans-serif;
--font-mono: "Geist Mono Variable", "Geist Mono", ui-monospace, "SFMono-Regular", Menlo, Consolas, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", monospace;
```

Marketing site (`apps/presentation/site/src/styles.css`):

```css
--font-mono: "Geist Mono", "JetBrains Mono", "SFMono-Regular", monospace;
```

`scripts/check-css-custom-properties.mjs` rejects Dashboard CSS bare references
whose custom property has no definition in the surface inventory. Run `npm ci`
at the repository root and `npm ci --prefix apps/presentation/dashboard`, then
`npm run check:css-custom-properties --prefix apps/presentation/dashboard`.
The check uses the existing TypeScript parser for
JSX style objects, `CSSProperties`-typed objects and literal
`element.style.setProperty` calls. Comments, string examples and ordinary data
keys do not define tokens. PostCSS and its declared value parser distinguish
stylesheet declarations and variable functions from quoted examples, comments
and other declaration values. Parse errors fail the check.

This is a syntactic inventory: it does not prove runtime reachability, resolve
object spreads or aliases, or model CSS inheritance and selector scope. Tokens
that are genuinely optional may keep a fallback (`var(--pw-surface, #fff)`).

### Type Scale

| Token | Size / line height | Weight | Tracking | Use |
| --- | --- | --- | --- | --- |
| Display | `48px / 48px` | 600 | `-0.05em` | Marketing hero |
| Heading L | `32px / 40px` | 600 | `-0.04em` | Major section |
| Heading M | `20px / 28px` | 600 | `-0.02em` | Card or panel |
| Body L | `16px / 24px` | 400 | normal | Lead copy |
| Body M | `14px / 20px` | 400 | normal | Default UI copy |
| Body S | `12px / 16px` | 400 | normal | Metadata |
| Mono eyebrow | `12px / 16px` | 500 | `0.06em` | Uppercase technical label |
| Code | `14px / 20px` | 400 | normal | Code and CLI output |

Use 600 for headings, 500 for controls and labels, and 400 for body copy.
Avoid decorative italics, ultra-light text, and black weights.

The Personal Workspace, settings and answer reader share the Dashboard's
`--text-*` and `--leading-*` tokens. Use caption (`12px / 16px`) for metadata,
body (`14px / 20px`) for default copy and controls, lead (`16px / 24px`) for
reading copy, heading (`20px / 28px`) for panels, title (`24px / 32px`) for
operator page titles, and display (`32px / 40px`) for major headings and counts.
Prose uses `--leading-prose` (`1.7`) for multiline Chinese and English text.
Tokens use rem units so browser text preferences apply. Do not introduce
fractional pixel sizes or sub-12px UI copy. Themes share this scale and font
families; they may change color, borders and surfaces. Controls inherit the
same family, including textareas. CJK fallback names are shared by prose and
code; Latin code remains Geist Mono. This intentionally replaces the older
12px primary choices and 9–11.5px auxiliary copy with readable body and caption
roles across Goal, Task, Chat, settings and result surfaces.

![Personal Workspace proportions and typography](assets/personal-workspace-layout.jpg)

Illustrative synthetic state rendered with the workspace sidebar, header, brief
components and packaged styles. It shows mixed-language text, quiet summaries
and an unavailable Goal; it is a composition example, not runtime evidence.

## Spacing And Layout

Use a 4px base:

```text
4, 8, 12, 16, 24, 32, 40, 64, 96, 128
```

- Page container: approximately `1200px` max width.
- Desktop gutters: `24px` to `32px`.
- Mobile gutters: `20px`.
- Card padding: `24px`; larger panels may use `32px`.
- Section rhythm: `96px` to `128px` on marketing pages.
- Operator surfaces may use tighter `24px` to `40px` section rhythm.
- Grids should collapse predictably from 3-4 columns to 2 and then 1.

Whitespace is structural. Prefer space and hairlines over alternating saturated
background blocks.

## Shape And Depth

| Token | Value | Use |
| --- | --- | --- |
| Tight | `6px` | Inputs, app buttons, navigation controls |
| Card | `12px` | Standard cards and code blocks |
| Panel | `16px` | Large feature or pricing panels |
| Pill | `9999px` | Marketing CTAs, tags, avatars |

Use shapes by context:

- marketing CTAs use full pills;
- application and desktop controls use tight 6px corners;
- content cards use 12-16px corners.

Default elevation is a 1px hairline and no shadow. Floating menus and modals may
use a low-alpha layered shadow. Do not use heavy drop shadows.

## Components

### Navigation

- White or near-white surface with a bottom hairline.
- Compact wordmark, restrained links, and one clear primary action.
- Desktop navigation collapses behind an accessible menu trigger.
- Sticky headers may use a subtle backdrop blur; content must remain readable
  without it.

### Buttons

- Primary marketing: ink fill, white label, pill shape, minimum 44px target.
- Secondary marketing: white surface, ink label, hairline, pill shape.
- Application primary: ink fill, white label, 6px radius.
- Application secondary: white surface, ink label, hairline, 6px radius.
- Icon controls: circular or 6px square, with visible focus state.

Do not mix marketing pills and application squares in the same control group.

### Cards And Panels

- White surface on near-white canvas.
- 1px hairline before any shadow.
- Clear heading, concise body, and optional mono metadata.
- Use precise grids rather than masonry.
- Avoid decorative cards with no information or action.

- Theme and language choices use the same two-column grid in every workspace theme.
- Omit secondary descriptive lines under workspace page titles, navigation labels, and display choices. Preserve operational status, errors, and action outcomes.
- Capability navigation shows the localized name; omit the secondary internal identifier. Show Goal/machine badges only in mixed-scope catalogs, not when the page already selects one scope. Configuration forms use shared spacing and a full-width switch row; optional capability and field explanations live in a collapsed configuration-help section. Keep activation consequences and read-only restrictions visible. Goal and machine editors share the enable-row JSON entry point. Goal JSON accepts only registered editable fields and invalidates the previous preview whenever edited; applying still requires a new reviewed preview.
- Present one Capability Center with an explicit device-default / single-Goal target. Goal entry points preselect their Goal; global entry points never silently choose one. Target changes discard the previous editor draft and preview, fetch the selected configuration, and preserve the existing revision-checked write owner. Distinguish configuration ownership from the currently selected target and editor permission: show device-only, Goal-only or device-default-with-Goal-override from the capability contract, even for read-only capabilities; keep writability in the existing editor status, and never list a machine-only capability in Goal scope. Show the affected scope and effective source before changes; do not turn storage boundaries into competing navigation entries.
- Keep Steward a first-level destination for the host steward executor/model/effort and runtime grant. Navigation follows purpose; configuration scope does not dictate navigation. See the [per-capability scope decisions](../architecture/rfcs/desktop-execution-frontends-v0.md#settings-ownership-and-scope).
- Scope each settings catalog to its owner: Goal settings include only capabilities with Goal scope, including Goal-scoped read-only entries. Machine-only capabilities belong in machine or steward settings even when a shared API catalog also describes them.

### Forms

- White surface, ink text, hairline border, 6px radius.
- Labels remain visible; placeholders do not replace labels.
- Focus uses the blue link/focus token with sufficient contrast.
- Errors use text and iconography in addition to color.

- Settings titles omit eyebrow text. Settings occupy the dynamic viewport height. Capability detail cards shrink to their content and are capped by the available panel height. The settings sidebar, capability catalog, and capability detail scroll independently with contained overscroll. Other settings content scrolls inside the main panel. Narrow screens use a bounded horizontal capability catalog above the detail panel.

### Code And Terminal Surfaces

- Geist Mono or the approved mono fallback.
- Use either a white hairline code panel or a deliberate near-black terminal.
- Preserve selectable text and horizontal scrolling.
- Avoid fake terminal decoration when the content is not technical evidence.

### Status And Control-Plane States

- Use color as a secondary signal; always include text or an icon.
- Prefer compact badges and hairline panels.
- Keep goal, gate, owner, evidence, risk, budget, and next action visually
  distinguishable.
- Summarize many records with one hairline count strip of typed states: zero
  counts recede, states that need the owner take the danger color and an icon,
  and the scope of the counts (for example "this page only") sits next to it.
  Counts come from the same typed classification as the labels, never from
  prose.
- Metric cells name their window and scope. An absent measurement renders as
  an em dash with a "not measured" label, never as zero; owner-reported partial
  coverage carries a visible marker. The
  [Goal workspace RFC](../architecture/rfcs/goal-workspace-surface-v0.md)
  defines the per-Goal vitals, glyph set and page composition.
- Detail many owners with a grid of owner cards: identity, one status pill, the
  owner's own records, and identifiers behind a disclosure. A blocked owner's
  card may carry a tinted hairline; avoid filling the card with color.
- Never render raw private state, credentials, provider IDs, or machine paths.

## Motion

- Motion explains state transitions; it does not decorate idle content.
- Use 120-200ms control transitions and 200-350ms section transitions.
- Prefer opacity and small transforms.
- Respect `prefers-reduced-motion`.
- Do not animate layout continuously, pulse large surfaces, or create
  background motion that competes with reading.

## Accessibility

- Maintain WCAG AA contrast.
- Use semantic HTML and visible keyboard focus.
- Interactive targets should be at least 44px where practical.
- Do not encode status using color alone.
- Support keyboard navigation, reduced motion, zoom, and narrow viewports.
- Keep English and Chinese layouts equally readable.

## Responsive Behavior

- `<= 640px`: one-column layout, collapsed navigation, full-width primary CTA.
- `768px`: two-column content grids where useful.
- `1024px`: full navigation and 3-column product grids.
- `>= 1200px`: centered max-width composition.

Do not shrink diagrams or code until they become illegible. Reflow or enable
bounded horizontal scrolling.

## Target Compositions

The rules above say what to avoid. This section says what a finished
operator surface looks like, so a change can be judged against a composition
rather than only against a list of prohibitions. Each pattern names the
mechanism; copy the mechanism, not another product's styling.

### Goal workspace: a document, not a card grid

From top to bottom, one reading column with an optional right rail:

1. **Masthead.** Breadcrumb, title, one context line (agents, repository) and
   the single consequential control (Stop / Resume). Nothing else competes.
2. **Vitals.** At most six stats in one row. The value carries the weight
   (heading size, tabular numerals); the label and its qualifier sit beneath
   in one caption line. No cell borders: spacing separates stats, one hairline
   separates the row from the page. Unknown is an em dash with its reason,
   never zero.
3. **One hero.** On the Overview, the Goal's objective is set as heading-size
   prose without a card around it, followed by a segmented criteria meter and
   a hairline checklist. Everything below the hero recedes: smaller headings,
   ghost controls, a hairline instead of another bordered panel.
4. **Owner rail.** What needs the owner sits beside the hero as a hairline
   list: glyph, the decision in owner language, relative age, an arrow that
   opens it in place. A quiet Goal shows no empty rail.
5. **Folded composer.** Reading views fold the composer to one line that
   expands on request; conversation views keep it open.

The browser scenario `goal-showcase`
(`examples/personal-workspace-browser/goal-showcase.mjs`) renders this
composition on the synthetic showcase workspace
(`examples/personal-workspace-browser/showcase-workspace.mjs`): two agents,
measured usage, an observed live turn, owner decisions, a partly verified
contract and a delivery map. Take before/after screenshots from that state,
not from placeholder fixtures.

The same public synthetic workspace illustrates the composition below. The
before view is the earlier masthead proposal; the after view uses the packaged
Goal overview with the document hero, owner rail and folded composer. These
are composition references, not installed-App or model-acceptance evidence.

| View | Before | Proposed composition |
| --- | --- | --- |
| Desktop | ![Earlier desktop composition](assets/goal-workspace/before-desktop.png) | ![Goal overview on desktop](assets/goal-workspace/overview-desktop.png) |
| Phone | ![Earlier phone composition](assets/goal-workspace/before-phone.png) | ![Goal overview on phone](assets/goal-workspace/overview-phone.png) |

### Status glyphs

One glyph per state across lists, vitals and maps; color is the second signal.

| State | Glyph | Color |
| --- | --- | --- |
| Settled / quiet | filled dot | track neutral |
| Waiting or stopped | hollow ring | muted |
| Observed live | ring with a core and a rotating arc | state color |
| Verified | check in circle | green |
| Failing | cross in circle | red |
| Not yet verified | dashed circle | faint |
| Owner decision | hand | caution |

The rotating arc requires an observed active turn (see "Liveness comes from
observation"). Reduced motion stops the arc; the ring and core still read live.

### States that are designed, not defaulted

- **Loading** holds the final geometry with a quiet skeleton; a region does
  not collapse to one spinner and then jump.
- **Region failure** is one announced line (icon, plain cause, how to retry)
  sized to its content. Reserve banners for a whole-surface failure.
- **Unknown** stays visible as "—" with its reason; it never becomes zero or
  disappears.

### Motion vocabulary

Tokens live in the Dashboard root: `--motion-fast` (120ms controls),
`--motion-base` (200ms state swaps), `--motion-section` (280ms panels),
`--ease-out` and `--ease-standard`.

- A changed value swaps with a short fade and 3px rise; a view panel enters
  with opacity and 4px.
- Values use the short swap transition while their text changes directly to
  the new observation. Do not interpolate task counts, decisions, time or money:
  intermediate text would invent a state. Goal and usage-window changes, unknown
  values and reduced motion retain the same exact observation semantics.
- New list rows enter with opacity and a 4px rise; row arrows nudge 2px on hover.
- Infinite motion is reserved for an observed live state or an in-flight read.

### Reference research

References inspected in October 2026: the LobeHub creator walkthrough shows
composition; the Up.computer video was sampled for expansion and status changes;
Linear documents its review interaction; Agent Dashboard describes planned
behavior in a design specification. These are research inputs, not approved
design sources or evidence that LoopX has shipped the same interaction.

| Reference | Mechanism adopted | Not adopted |
| --- | --- | --- |
| LobeHub Goal and task detail ([source](https://github.com/lobehub/lobehub), [public walkthrough](https://x.com/arvin17x/status/2108966992149713125)) | Document layout; value-over-label stat row; objective as body prose; one glyph family with an animated running ring; four grey text levels; inline expansion of a failed row | Dark-first chrome; graph as a default view at high fan-out |
| Linear Reviews ([official interaction](https://linear.app/docs/diffs), [creator post](https://x.com/artman/status/2107130290053447824)) | Quiet segmented view switch; hairline file list with compact counts; generous reading column beside dense data | Code-review specific structure |
| Up.computer ([launch video](https://x.com/krl_grn/status/2107478644226396339)) | Status word as a quiet prefix in a list ("Working"); collapsible "worked for" and tool-call steps; composer with model and permission inline | Parallel chat panes as the primary layout |
| Agent Dashboard design spec ([source](https://github.com/wildware-uk/agent-dashboard/blob/main/docs/superpowers/specs/2026-08-25-agent-dashboard-design.md)) | New items animate in; an "N new" affordance instead of moving the reader; pending decisions pinned above the feed | Name-hashed avatar colors as identity |

## UI Reproduction Workflow

For feature development, carry one user task through the
[task-first delivery workflow](frontend-delivery.md#task-first-delivery--从用户任务开始).
A visually consistent composition must also eliminate unnecessary re-entry and
confirmation, identify the affected target, and show truthful state after action.


For UI implementation, migration, or reproduction:

1. Read this file before editing.
2. Inspect the real target surface, source code, screenshot, or Figma file.
3. Inventory behavior, states, breakpoints, assets, and copy.
4. Map unspecified visual details to these tokens.
5. Reuse existing LoopX primitives before creating new ones.
6. Validate desktop and mobile layouts in a real browser.
7. Capture screenshots for first-screen or high-fidelity changes.
8. Compare spacing, typography, borders, color, and interaction states—not
   only component presence.

When reproducing an existing approved LoopX surface, visual and behavioral
parity is required. Framework migration alone must not alter the UI.

## Do

- Use black-and-white precision with deliberate hierarchy.
- Let typography, whitespace, grids, and hairlines do most of the visual work.
- Reserve gradients for a single meaningful accent area.
- Keep operator interfaces calm, compact, and scannable.
- Use tokens rather than one-off color, radius, or spacing values.
- Preserve exact behavior during framework migrations.

## Do Not

- Do not add a second decorative system.
- Do not fill large surfaces with accent colors.
- Do not use glassmorphism, neon glows, heavy shadows, or excessive blur.
- Do not mix unrelated radius and button styles.
- Do not use generic component-library defaults without adapting them.
- Do not claim fidelity without browser validation and screenshot evidence.

## Review Checklist

- [ ] The task read and followed `docs/development/design.md`.
- [ ] Core tokens are reused rather than duplicated.
- [ ] Marketing and application controls use the correct shape language.
- [ ] Desktop and mobile layouts are validated.
- [ ] Keyboard, focus, contrast, and reduced-motion behavior are preserved.
- [ ] Repeated explanations are consolidated; help works on hover, focus and tap,
      while material qualifiers, failures and action consequences remain visible.
- [ ] Owner decisions complete in place with a readback; live treatment is
      backed by an observed active fact; protocol identifiers are not primary copy.
- [ ] English and Chinese content remain usable.
- [ ] Screenshots are provided for first-screen or fidelity-sensitive changes.
- [ ] No private data or local paths enter the UI or screenshots.

### Craft review

Run these against before/after screenshots of the showcase workspace, not a
placeholder fixture:

- [ ] The viewport has one hero; secondary regions visibly recede.
- [ ] No bordered card exists only to group; a hairline or spacing would do.
- [ ] Loading, empty, unknown and failed states are designed and sized to content.
- [ ] Changed values and entering rows move once, by token; idle content is still.
- [ ] Glyphs follow the status glyph family; color is never the only signal.
- [ ] Touched stylesheets use theme tokens, not new raw hex values.
