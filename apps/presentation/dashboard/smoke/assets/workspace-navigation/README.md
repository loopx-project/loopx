# Workspace navigation reading evidence

Synthetic populated workspace, including 30 Goals and a linked read-only archive.
The packaged Chat bundle is served by the existing production Chat entrypoint;
API discovery/permission descriptors are synthetic. These captures contain no
private operating state and do not establish an installed App result.

The baseline is `c10d8c2b4a20747e6eb77a188fd8e870b3be19de`. It reproduces the
missing host Settings entry while viewing the archived Goal. The after views
use the accompanying source and the same fixture. Desktop is 1440×900, low
window 1280×540, and narrow 390×640. Narrow captures show the opened sidebar.
The default footer now has Settings and a compact search icon on one row.
The independent Goal list scrolls above it; update status retains its own row.
Restriction views show an explicit Goal target and its original read-only
boundary, without local management forms. Repeated matching target/source
labels are shown once. Scope and source remain available.

Reproduce after a Chat build with `npm run smoke:workspace-command-palette` in
the dashboard package and `LOOPX_PERSONAL_WORKSPACE_PACKAGED=1`. The navigation
scenario covers EN/ZH, low/narrow windows, real browser touch events, keyboard,
IME-shaped events, focus, outside dismissal, internal selection/drag/scroll,
no click-through and zero write requests. It does not certify native macOS IME
or touch hardware. `LOOPX_NAVIGATION_CAPTURE_BASE=1` is only the baseline
capture/reproduction mode; it does not run the new behavior assertions.

Whole-page judgment: the stable lower row makes Settings discoverable while
reading the archive and removes a competing navigation card. Actual values,
required permissions, action outcomes and recovery remain direct. Coverage,
missing-observation and methodology explanations use contextual glyphs, with
complete hover, focus and tap readback. The empty/partial Overview no longer
stacks duplicate disclaimers. Help uses body text and stays within the viewport.
Research captures use the real read-only HTTP owner with synthetic measurements
and long analysis; judgments, unknown values and original citations are retained.
The unanalysed-scope glyph sits beside the result heading. The expanded phone
view shows the original full rationale and evidence. This is whole-page visual
inspection and interaction evidence, not a human comprehension study or a
qualification of every setting, platform or native input method.

The glyph and research phone captures are 390×844. The research desktop is
1280×900. Reproduce research views with the packaged `research-history` browser
scenario. Both before and after captures are synthetic; archived source names
are fixture labels, not a claim that benchmark work was executed.
