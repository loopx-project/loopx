# Shared research questions

Packaged Research screenshots use the public synthetic
`research-display-language` fixture: 30 recorded events, six analysis episodes,
long Chinese/English questions, negative evidence and mixed yes/no/unknown
judgments. Fixed provider output exercises the production SQLite, analysis job,
history and citation HTTP readers. No model or solver executes. These captures
do not qualify model translation, scientific judgments or native benefit.

Before: `7f01e00fa8a7951fbb7fe83e877e75c66391e204`. After: this change on
`3a1800b18f0f5ec30c9449130741762686f3595f`. The intervening main changes do
not touch the research presentation; dependency installation and packaging were
repeated on the new base. Both servers consumed the same frozen capture
(`sha256:86f8cb2847fa18c4b46f6f2b19b459c087d71811058ea07a49e50b029ac8dc64`).

- [Default first viewport](default-desktop.png): history and mixed metrics remain
  visible, with the density plot in the first viewport.
- Desktop [before](before-desktop.png) / [after](after-desktop.png), 1200 × 888:
  five copies of the same question become one complete question and comparable
  episode/verdict rows. The different control question retains its full wording.
- Narrow [before](before-mobile.png) / [after](after-mobile.png), 390 × 844:
  shared conditions, negation and uncertainty wrap completely instead of being
  repeated in five clipped rows.
- Selected episode [desktop](reader-desktop.png) / [narrow](reader-mobile.png):
  the complete question and rationale use 16px reading text. The desktop view
  also shows the original question after explicitly requesting it; original
  English evidence is not relabelled as a translation.
- [English narrow comparison](english-mobile.png): the same literal grouping
  preserves all six judgments and the independent control question.
- [Unavailable translation scope](source-unavailable-mobile.png): a mismatched
  source retains originals and its visible coverage warning.

The browser journey also checked 320, 640, 1000 and 1440px, keyboard expansion,
contextual help, complete originals and citation return to the initiating row.
Both languages' analysis keys, result projections and captured source bytes
remain unchanged. Browsing produced no mutation request. Public evidence uses
synthetic material; installed private research records are not published here.

合成数据下，共同问题只展示一次，片段与判读逐行比较；展开后完整问题、理由、引用和
原始问题仍可读。不同问题不合并，不据相似措辞猜短标题。检查了桌面、窄屏、键盘、
说明与引用返回；这些证据不代表模型译文、科研判断或实际收益已获独立验证。
