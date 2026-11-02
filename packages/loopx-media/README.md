# Content-ops media provider

`loopx-media` is an optional distribution for the existing **content-ops**
capability. It renders an admitted composition and can execute explicitly submitted,
bounded cloud video or image intents. It does not choose sources, run commands from
a brief or publish a movie. Shared TypeScript admission and transitions
remain in LoopX core.
Pillow, FontTools, FFmpeg and platform speech are specialized local computation.

From a source checkout, install the executable and register its manifest:

```sh
uv tool install ./packages/loopx-media
loopx extension install --manifest packages/loopx-media/extension.toml --execute
loopx extension doctor loopx-media --execute
```

The LoopX process must have `loopx-media` on its PATH. Installation explicitly
enables the local extension. No renderer is installed, enabled or downloaded
by `content-ops media prepare`, page loading or a missing-provider read.
The manifest requests `content_media_write` for private composition output
and the separate `content_media_generate` permission for cloud generation.
Ordinary render requests do not use credentials or call the network.
Use the existing extension state owner; there is no second media setting.
An isolated environment is also supported if its executable is on that PATH.

An Agent prepares the story, source SHA256s and timing through
[`content-ops media`](../../docs/reference/protocols/content-ops-media-v0.md).
In the packaged App, open **Goal → Files → Content artifacts**. Select the prepared composition, render, then view the video,
captions, transcript and source boundaries in the same workbench. The CLI
exposes the same operation. Rendering is local work; it grants no publication
approval. Revise the brief through the Agent rather than re-entering source
paths or protocol JSON in the browser.

**Return to conversation** preserves the selected Goal and prepares no model call.
Use it to give the Agent feedback and prepare another revision. **Goal delivery checks**
opens the existing Goal acceptance interaction shared with other deliverables;
opening it only reads its current requirements and receipts. A local render or
cloud image never grants semantic acceptance, independent review, adoption or
publication authority. This packaged production route currently requires an
existing Goal; it does not create one for an ordinary project conversation.

Artifact preview/download URLs bind the saved revision and file SHA256. A changed
composition revision or different bytes makes that link unavailable rather than
serving a replacement. Immutable image/footage identities keep their original
verified outputs. Existing unpinned URLs remain compatible current-file reads.
Opening saved media does not start rendering, generation or provider polling. An explicit
render observes that operation while this view remains open; leaving the view or
a failed read stops observation without cancelling the provider.

Supported atoms are title scenes, measured text wrapping, animated node
diagrams, hash-bound image/video excerpts, timed captions, supplied audio,
macOS system speech, H.264/AAC encoding, a first-frame poster and full video
decode. Images retain their orientation; video excerpts retain aspect ratio.
Installed fonts must cover the visible text, with CJK line breaking supported.
RTL shaping and arbitrary font discovery are not qualified. macOS speech is
optional; other platforms require supplied audio or explicit silence.
No cloud voice or game engine is bundled here. The bounded Ark video and image
routes below are explicit and optional; neither is a render fallback.

The default excerpt and Blender layouts display the title and source boundary; nonempty
`body` text is explicitly refused. Preserve a needed qualifier in those visible
fields, a separate title scene or timed captions before preparing a new revision.
The renderer also refuses a stale/direct request with unsupported body text;
it cannot return ready while silently dropping that text.

## Picture framing

For a source-led composition, an Agent can set `framing: "full_frame"` on an
image/video excerpt or Blender shot. Version 0.3.0 fits the complete picture
without cropping and keeps its source boundary in a separate strip below it.
The studio header, drawn shot title and progress bar are omitted; the title
remains in the timeline/source readback. Include needed titles in the artwork,
a title shot or timed captions. Each shot can choose independently. Omitted
framing or `"editorial"` keeps the original canonical plan and layout.

Use the explicit package upgrade/reinstall and extension upgrade/doctor route
below for an existing provider. The core refuses full-frame readiness when an
older renderer omits its framing readback. Retry the unchanged plan after the
upgrade. The workbench displays the choice and retains its existing render,
cancel, retry and verified-download journey. To roll back the composition,
prepare a new revision without framing and render it. No global setting,
source classification or publication authority changes. See the
[bilingual contract](../../docs/reference/protocols/content-ops-media-v0.md#per-shot-picture-framing).

中文：0.3.0 在原 content-ops 镜头中加入可选“大画面”构图。素材保持完整比例，
来源说明另置底栏；需要观众看到的标题由素材、标题镜头或字幕承载。Agent 准备
分镜，工作台沿原入口制作和查看。已有安装应显式升级可选包及 manifest 并运行
doctor；移除字段、保存新修订并重制即可恢复原构图。默认计划和发布边界不变。

Public synthetic packaged-workbench captures (macOS, source qualification;
not an installed-App or comprehension claim):

| Editorial | Full frame | Narrow, scrolled to movie |
| --- | --- | --- |
| ![Editorial](examples/framing/editorial-desktop.png) | ![Full frame](examples/framing/full-frame-desktop.png) | ![Narrow](examples/framing/full-frame-narrow.png) |

## Optional Blender scenes

For real 3D geometry, install the same provider with its optional Blender extra
in a Python 3.11 environment, then register and doctor the current manifest:

```sh
uv tool install --python 3.11 './packages/loopx-media[blender]'
loopx extension install --manifest packages/loopx-media/extension.toml --execute
loopx extension doctor loopx-media --execute
loopx content-ops media prepare --goal-id GOAL \
  --plan-json packages/loopx-media/examples/blender-handoff.json \
  --source-directory .
```

If this provider is already installed, use the package manager's explicit
upgrade/reinstall operation before registering the new manifest revision with
`loopx extension upgrade --manifest packages/loopx-media/extension.toml --execute`.
The registered executable and its Python environment are pinned. Install the
extra in that environment and run doctor; changing the calling process's PATH
does not replace a registered executable. Doctor checks the pinned installation,
not whether some other environment has Blender. A move to another environment
needs explicit extension registration; doctor cannot rebind an active revision.
Preparation does not install Blender. The ordinary install still supports 2D;
a missing Blender extra fails only an attempted 3D scene. Repair that local
dependency and retry the unchanged prepared composition through the same CLI
or **Content artifacts** workbench. No second provider setting is needed.

An Agent selects a `blender` shot and supplies declarative objects, camera,
lights, materials and position keyframes. The shared TypeScript owner admits
those fields and includes them in the composition digest. The optional provider
runs actual Blender 4.5 LTS/Cycles on CPU in a separate process. Uploaded blend
files, arbitrary Python, add-ons and external asset discovery are unsupported.
Blender 5.x uses a different compositor API and is not qualified by this provider.

The workbench previews the composed movie and downloads **Blender project and
passes**. The verified ZIP retains each `scene.blend`, its input, the replay
builder, RGBA frames and a 16-bit depth pass. Depth maps Blender Z distance 0–50
to 0–65535 and clamps values beyond 50. Scene output paths are relative; opening
the blend with scripts disabled does not require embedded code. A second
process reopens each saved project and checks its geometry, keyframes, camera
and timeline before the composition can become ready. The normal renderer
then composites the RGBA frames with the selected layout and verifies the
complete encoded timeline. This does not establish the illustration's factual
truth, explanatory quality or audience understanding.

The versioned example is a five-second synthetic artifact round trip, not a
recorded Agent run or a finished creative direction. Reuse a verified result
as a digest-bound video excerpt in a larger composition instead of rerendering
unchanged 3D footage. Keep source boundaries and the editable project.
Reusing verified output preserves exact bytes. Re-rendering an editable scene
is separate: CPU rendering can vary in the last color bit between processes.

The renderer refuses a short excerpt, changed source, missing glyphs, text
overflow or speech longer than its cue. It does not fill missing footage,
silently truncate text or speed up speech to hide those failures. Prepare a
new revision for story/source/timing changes. Repair a local dependency and
run doctor again before retrying an unchanged brief. Cancellation is requested
in place and read back after the worker stops; a host interruption is recovered
by an explicit retry. An intact completed result is reused without encoding
again. Damaged result bytes cannot be downloaded as verified output.

Captions use a bounded plain-text subset. Single line breaks, ordinary
ampersands and quotes work. Internal blank lines delimit subtitle cues;
angle brackets and entity syntax are not supported by this SubRip export.
These inputs are refused before preparation and by the renderer. Use separate
timed cues for paragraphs and spell out markup or comparisons rather than
producing a subtitle file that silently loses or reinterprets accepted text.

## Bounds and review

For source selection, information density, narration and complete-film judgment,
use the capability's [whole-film refinement workflow](../../loopx/capabilities/content_ops/README.md#source-led-selection-and-whole-film-refinement--全景选材与整片精修).
The technical checks below qualify encoding and source integrity; they do not
replace that creative review or prove audience understanding.

Plans are limited to 90 seconds, 30 shots, 20 local assets and 80 ordered
caption cues, with at most 1920×1080 at 30 fps. Each source is at most 64 MiB;
all source snapshots together are at most 128 MiB. The render has a 120-second
process budget. A complex valid plan can exceed the local machine's budget;
that is a failed attempt, not a reason to mark a partial movie ready.
Each Blender scene has at most 16 box/sphere objects, 3 area lights, 24 connections
and 120 frames; a composition can include at most 3 scenes. Scene rendering,
reopening, pass checks and composition share the existing 120-second process
budget. Failed or cancelled attempts never substitute a diagram or frozen frame.

The result records exact output digests, decoded frames, render wall time,
PCM peak/clipping measurements when audio exists, and untested creative,
listening and audience-understanding judgments. These mechanical checks do
not establish narration quality, factual truth, source rights, learning gain,
virality or a better film. Review the actual result before sharing. Existing
content-item approval and delivery intent are invalidated when the plan or
rendered bytes change. The outputs remain private until separately authorized.

Disable new renders with the existing owner:

```sh
loopx extension disable loopx-media --execute
```

Existing verified results remain readable. Re-enable and run doctor after a
dependency repair. Prepared files and failed attempts remain in the registered
project's private `.local/content-media/` directory for inspection; disabling
does not delete them. There is no automatic cleanup of source evidence.

## Focused validation

Core tests do not need this distribution. For source changes, first run the
repository-invariant and lint commands used by CI:

```sh
uv run --extra test python -m pytest -q --no-cov \
  tests/test_stage2c_dependency_lock.py tests/architecture/test_top_level_module_budget.py
uv run --extra test python -m ruff check \
  tests loopx/canary loopx/control_plane loopx/domain_packs loopx/presentation
```

Then exercise the actual optional extension, encoder, source reuse, recovery
and HTTP byte serving after installing the executable in an isolated environment:

```sh
LOOPX_MEDIA_RENDERER_TEST=1 uv run --extra test --with-editable ./packages/loopx-media \
  python -m pytest -q tests/test_content_media.py
node --test tests/control_plane_ts/content_media.test.ts
```

The opt-in test uses synthetic local sources and no model or cloud service.
Its optional package dependency supplies the real FFmpeg subtitle consumer;
decoded VTT/SRT text must preserve accepted line breaks, ampersands and quotes.
The source qualification checks the editable module and executable environment
so a cached wheel cannot silently stand in for the source under review.
It is integration evidence, not a subjective or comparative creative study.

For an explicit source qualification of the optional Blender computation,
use an isolated project environment so the normal interpreter stays intact:

```sh
UV_PROJECT_ENVIRONMENT=.local/blender-test-env \
LOOPX_MEDIA_BLENDER_TEST=1 \
uv run --python 3.11 --extra test --with-editable './packages/loopx-media[blender]' \
  python -m pytest -q tests/test_content_media.py
```

The opt-in test uses the real extension and HTTP route, inspects the ZIP,
opens the blend in an independent bpy process and checks stored scene state.
It performs no cloud/model calls or public uploads. Report the actual bpy
version and platform; this command is not a claim of cross-platform delivery.

## Optional generated footage

An Agent can prepare a **content-ops generation** plan, then submit one bounded
Seedance 2.5 video task and collect it as a hash-bound source for the existing
composition. This provider supports 4–10 whole seconds at 480p/720p, text-only
or a first frame and optional last frame. Reference images must be explicitly
public-safe PNG/JPEG, 16:9, 300–1920 pixels wide, 300–1080 pixels high
and at most 2 MiB each. Alpha is
composited on white and encoded as RGB PNG; original and transmitted hashes
remain in the private submission journal. This does not open another model or
reuse unrelated account credentials.

Load `ARK_API_KEY` only into the authorized LoopX host process environment.
`ARK_BASE_URL`, if supplied, must be the standard
`https://ark.cn-beijing.volces.com/api/v3` endpoint. Do not place a key in the
plan, command arguments, extension manifest, browser or repository. A missing
credential preserves the prepared intent without submitting it. Upgrade through
the existing extension lifecycle after installing this package; do not edit a
saved binding by hand. Existing 0.2 providers still render local compositions;
they are unavailable for the new generation permission.

```sh
loopx content-ops generation prepare --goal-id GOAL \
  --plan-json footage.json --source-directory ./authorized-frames
# Read generation_id and revision from prepare. This next command submits one
# paid provider task; prior scoped authorization and a current price are needed.
loopx content-ops generation submit --goal-id GOAL \
  --generation-id GENERATION_ID --expected-revision 1
loopx content-ops generation check --goal-id GOAL \
  --generation-id GENERATION_ID --expected-revision 1
loopx content-ops generation collect --goal-id GOAL \
  --generation-id GENERATION_ID --expected-revision 1
```

The Agent prepares the immutable prompt, reference SHA256s, audio/watermark/
last-frame choices and a dated official price quote in
`content_ops_generation_plan_v0`. The quote contains `cny_per_million_tokens`,
`checked_at`, `source_url` and `max_estimated_cny`; submission refuses a quote
older than 24 hours or a nominal estimate above that declared bound. This is
an estimate check, **not a guaranteed billing cap, invoice or authorization**.
Token usage returned by the service is shown separately. Read actual account
charges when a complete financial total is required. Parameters belong in the
structured plan; Ark's weak prompt parameter suffixes are refused.

![Versioned works in the Goal artifact view](docs/workspace-after-desktop.png)

Packaged Goal artifact view with synthetic local works, using the real backend.
The [390px view](docs/workspace-after-narrow.png) keeps preview and download
with the requested work. This fixture checks delivery mechanics; it does not
qualify live generation or creative quality.

In **Goal → Files → Content artifacts**, the same workbench
shows the prepared footage, external-upload/usage consequence, submit button,
original-task checks, missing-provider/key state, saved video and last-frame
download. It does not ask the owner to copy filesystem paths or re-enter the
Agent's complete brief. Automatic status checks are limited to five minutes
and can stop locally; opening the page or reading local drafts never submits
or polls the cloud. Local inspect/list retain private source locations and
cloud result URLs internally; the workbench projection has neither.

Each submit/check/collect is a short operation, within the existing 120-second
extension boundary. Generation continues remotely across host restarts. A
successful POST's task id is fsynced in a provider journal before stdout returns.
The parent saves its intent before the effect. Duplicate clicks never POST
again; after a lost response, check the journal-backed original task. If no task
id was recovered, the status stays `submission_unknown` with an explicit
reconciliation requirement. There is no automatic paid retry. A genuinely new
shot requires a new prepared intent after reviewing the original outcome.

## Optional generated images

Version 0.4 adds a single-image path in the same **Content artifacts**
workbench. Install or explicitly upgrade this distribution and register the
current manifest using the lifecycle above. Load a separately authorized
`ARK_IMAGE_API_KEY` into the LoopX process; optional `ARK_IMAGE_BASE_URL` must
equal the standard Ark endpoint. The video `ARK_API_KEY` is never borrowed.
Neither the browser nor a prepared plan stores or displays credentials.

An Agent prepares `content_ops_image_plan_v0` with title, prompt, explicit
width/height, watermark choice, `references` and a current quote. This bounded
path pins `doubao-seedream-5-0-pro-260628`, opaque PNG, no layer decomposition
and standard prompt optimization. It accepts text or one public-safe PNG/JPEG
reference, at most 4 MiB, with relative path, exact SHA256 and opaque provenance.
Reference alpha is composited on white; the private journal retains both the
original and uploaded PNG hashes. Output dimensions are 720–2560 per side,
with total area 921,600–4,624,220 pixels and aspect ratio 1:16–16:1.

```sh
loopx content-ops image prepare --goal-id GOAL \
  --plan-json image.json --source-directory ./authorized-images
# This next command performs one paid request under the current authorization.
loopx content-ops image submit --goal-id GOAL \
  --image-id IMAGE_ID --expected-revision 1
loopx content-ops image inspect --goal-id GOAL --image-id IMAGE_ID
# Recover an already saved response; this command never generates another image.
loopx content-ops image collect --goal-id GOAL \
  --image-id IMAGE_ID --expected-revision 1
```

The quote has `cny_per_image`, `checked_at`, official `source_url` and
`max_estimated_cny`; it expires after 24 hours. Verify the current model and
[price table](https://ark.volcengine.com/docs/model-pricing) for the actual
dimensions. The nominal estimate does not grant spending authority or guarantee
an invoice limit. The supported first reference is free under that rate basis;
other pricing modes and multiple outputs are outside this path.

The synchronous request has no cloud task ID, polling or cancellation. A
durable submission journal precedes POST; a saved response precedes stdout.
When received, exact HTTP response bytes and their digest are persisted before
interpretation, so local interpretation failure can be recovered without a new
POST. Header/request-ID observations and sanitized error classes are private
diagnostic evidence. Loss before a complete response is saved remains unknown.
After timeout or malformed response the status remains `submission_unknown`.
Repeated submit does not POST again. `collect` only reads that intent's saved
response and can repair damaged local output. If no response was saved, the
external outcome needs reconciliation before a genuinely new intent is chosen.
Missing provider/credential leaves the draft prepared and sends nothing.

The workbench displays the brief, upload/usage consequence, status, nominal
cost and verified `original.png` and `generated.png` downloads. Full PNG decode,
exact dimensions and core-owned hash readback qualify local byte readiness;
creative quality, visible watermark, source faithfulness and actual invoice
remain separate judgments. Reuse `generated.png` as a hash-bound composition
asset; render precise labels, diagrams and subtitles locally. A generated
illustration must not be presented as a recorded product or benchmark trace.
Disabling the same extension stops new image/video effects and keeps saved
results readable. No separate image service or settings owner is introduced.

Images use the same Goal artifact view and backend as the video example above.
The disposable saved-response fixture uses a locally rendered poster to check
collection and download, **not live Seedream output or creative acceptance**.
Unavailable credentials retain the draft; an unconfirmed external request has
no paid resubmit control. [Unavailable Goal acceptance information](docs/workspace-checks-unavailable-narrow.png)
remains an explicit gap beside the works, rather than becoming media-owned
acceptance state.

Ark's DELETE both cancels queued work and erases finished task records, without
a documented atomic queued-only precondition. The provider therefore retains
the task and reports `atomic_cancellation_unavailable`; it does not race a GET
against a destructive DELETE. Stopping local checks does not cancel the task
or its charges. A failed/expired task stays failed/expired, and remote success
is separate from verified local collection. A fresh GET obtains expiring asset
URLs; downloads have origin, byte and time bounds, followed by full video
decode, measured duration, hashes, poster and optional last-frame validation.
The original task is never deleted during collection or retry.

Use collected `generated.mp4` as an ordinary SHA256-bound excerpt source in
`content-ops media prepare`; retain `generation:<id>` as provenance and add
precise text/diagrams/captions locally. Generated footage is illustrative;
encoding does not prove continuity, visible watermark, creative quality,
audience understanding or publication approval. Inspect those separately.
Disable or roll back through `loopx extension disable/rollback`; preserve the
private source/intent/artifact files. No global credential configuration or
publication state changes as a side effect.

Primary provider contracts:
[create](https://ark.volcengine.com/region:cn-beijing/docs/create-video-generation-task-api),
[query](https://ark.volcengine.com/region:cn-beijing/docs/get-video-generation-task-api),
[cancel/delete](https://ark.volcengine.com/region:cn-beijing/docs/cancel-or-delete-video-generation-tasks-api).
No paid requests run in the default test suite.
