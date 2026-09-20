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
Do not collapse failures, missing authority, uncertain observations, or actions
requiring judgment into a reassuring success summary. Read models select facts;
they must not invent execution, acceptance, or completion from prose or counts.

Review realistic populated, quiet, blocked, and unavailable states on desktop
and mobile. Show before/after views, name what earns attention and what was
consolidated, and verify keyboard access and return to the original context.
Record this in the PR's visual evidence section. The repository first-screen
preview approval gate still applies.

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

Fallbacks:

```css
--font-sans: "Geist", "Inter", "Helvetica Neue", Arial, sans-serif;
--font-mono: "Geist Mono", "JetBrains Mono", "SFMono-Regular", monospace;
```

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
- Capability navigation shows the localized name and a compact Goal or machine scope badge; omit the secondary internal identifier. Configuration forms use shared spacing and a full-width switch row; optional capability and field explanations live in a collapsed configuration-help section. Keep activation consequences and read-only restrictions visible. Goal and machine editors share the enable-row JSON entry point. Goal JSON accepts only registered editable fields and invalidates the previous preview whenever edited; applying still requires a new reviewed preview.

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

## UI Reproduction Workflow

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
- [ ] English and Chinese content remain usable.
- [ ] Screenshots are provided for first-screen or fidelity-sensitive changes.
- [ ] No private data or local paths enter the UI or screenshots.
