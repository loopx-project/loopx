# Research worker readback: packaged first viewports

These screenshots use a disposable synthetic Goal and Agent. They contain no
live workspace, model session, private trace or user data.

- Before: packaged dashboard built from upstream
  `82f9c09bd61e26e458cecfd4cffeab2350359381`.
- After: the candidate source in this change, with the packaged bundle's
  source-file fingerprints verified against the checkout.
- Viewports: desktop `1512 × 982`; mobile `390 × 844`.
- Displayed locale: `zh-CN`. The same smoke also exercises `en` at both sizes.
- Browser: headless Chromium 151, with a CJK font installed.
- Preview boundary: locally served packaged dashboard; no hosted preview.

## Changed states

| State | Desktop | Mobile |
| --- | --- | --- |
| Before: no worker observation | [First viewport](research-worker-before-zh-CN-desktop.png) | [First viewport](research-worker-before-zh-CN-mobile.png) |
| Paused: the original replan obligation remains unresolved | [First viewport](research-worker-paused-zh-CN-desktop.png) | [First viewport](research-worker-paused-zh-CN-mobile.png) |
| Unknown: the original request requires reconciliation | [First viewport](research-worker-unknown-zh-CN-desktop.png) | [First viewport](research-worker-unknown-zh-CN-mobile.png) |

The observation sits in the existing Goal overview, before its acceptance and
work sections. It states the next useful action and keeps complete costs
unknown. Original operation details are expandable; the existing tasks link
and Goal Stop control remain available. It does not replace current admission
or claim that the Goal has finished.

## Reproduction and behavioral checks

Run the existing packaged browser entry point with a working Playwright browser:

```bash
LOOPX_PERSONAL_WORKSPACE_SCENARIO=research-worker-readback LOOPX_PERSONAL_WORKSPACE_PACKAGED=1 \
  node examples/personal-workspace-browser-smoke.mjs
```

The scenario uses `tests/control_plane/research_worker_readback_fixture.py` to
produce an actual worker observation through the File provider and real CLI,
then reads it through the Chat HTTP API and packaged page. Both states, locales
and viewport sizes are exercised. Checks cover horizontal overflow, unexpected
browser errors, original identity details, and task navigation. Missing,
corrupt and differently scoped associations hide stale operation identity.
Reads and navigation leave canonical Goal state and dispatch counts unchanged;
the existing Stop action actually stops the synthetic Goal without another
worker dispatch.

An absent optional Agent endpoint may return its typed session-preparation
error; this is handled separately from business writes. The fixture does not
install a model provider, invoke a paid model or claim to measure complete
provider costs.

## Measured method comparison in the existing results view

The follow-up also reads a completed synthetic method comparison through the
same packaged Chat server and its real Explore HTTP API. It uses the File
case produced by `tests/control_plane/test_research_comparison_cli.py`:
three search families measure their actual generated expressions, the same
original Todo adopts one, and the unchanged owner verifier checks deployed
source on withheld inputs. The test separately covers SQLite and an additive
positive control that retains the original expression.

| View | Capture |
| --- | --- |
| Desktop results, selected adoption and original completed Todo | [Packaged viewport](comparison-adopted-desktop.png) |
| Mobile viewport: measured losses and adoption explanation | [Result viewport](comparison-adopted-mobile-result-viewport.png) |
| Mobile viewport: source and original completed Todo | [Scope viewport](comparison-adopted-mobile-scope-viewport.png) |

These captures show the current candidate source after the final merged-source CLI comparison, using
the existing results layout. Both mobile images capture the actual
390 × 844 viewport at overlapping scroll positions: its 346-pixel content
region cannot show the full 477-pixel evidence reader at once. No layout,
DOM or image cropping was applied. The readback
checks the recorded 54 / 16 / 0 training losses, the original Todo's `done`
state, source references and adoption explanation. Navigation and reading
append no work or settlement history. The original training observations keep
their then-unknown heldout result; a later finding records the independent
verification and bounded adoption. The interface still distinguishes a saved
claim from independently certified adoption.

Generate the source with the source-checkout Python environment:

```bash
python -m pytest --no-cov tests/control_plane/test_research_comparison_cli.py
```

The finite program-search Host is deterministic. Equal score-call budgets do
not establish equal grammar-generation CPU, model tokens, money, or improved
EdgeBench performance. No model provider is used by the readback.

**中文：** 新增截图读取实际合成实验完成后的原 Explore/Todo 数据，展示三种
方法的测量、所选程序的采用说明和原任务完成状态。两张手机图分别记录实际
视口中相互重叠的滚动位置，连续展示结果和关联任务，没有修改页面或裁切。
查看与导航没有追加执行或结算；历史实验中的未知与后续已验证
结果分别保留。该证据不等于真实模型或完整成本的效果对照。
