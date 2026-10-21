# Private content composition v0

The existing `content-ops` capability owns video preparation, render attempts
and result readback. The TypeScript owner is
`loopx/control_plane/capabilities/content_media.ts`. Python handles private
files and transport; the optional `loopx-media` extension performs specialized
image, audio and video computation. This is a local composition slice, not a
new generic capability or a replacement publication lifecycle.

The active callers are the Agent-facing CLI and the Goal's packaged **Content
production** workbench. A prepared brief is shared between them. The Agent
absorbs source discovery, identifiers, hashes and timing. The owner selects a
prepared composition, starts/cancels its render and reads the result in place;
there is no browser source-directory or JSON-entry step. Lark can convey the
request to an Agent but does not gain an independent renderer or upload action.

## Plan and source scope

`content_ops_media_plan_v0` contains:

| Field | Meaning |
| --- | --- |
| `title`, `width`, `height`, `fps` | Name and bounded output geometry/clock; dimensions must be even. |
| `layout_template_id` | Existing content-ops layout template, default `monochrome-editorial`; style tokens enter the digest. |
| `shots` | Ordered distinct `id`, `kind`, frame-aligned `seconds`, `title`, optional `body`, `source_ref`, explicit `boundary`. |
| `assets` | Distinct id, relative `path`, exact `sha256`, `image`/`video`/`audio`, `source_ref` and `local_private`/`public_safe` source classification. |
| `captions` | Ordered nonoverlapping `start`, `end`, `text`, `language` cues inside the composition. One sequential caption track is emitted. |
| `narration` | Explicit `silent`, `supplied` with audio `asset_id`, or `system_speech` with installed `voice` and optional words-per-minute. |

Caption text is plain text. Single line breaks are supported; an internal
blank line is refused before preparation because it terminates a subtitle
cue. Use single line breaks or separate timed cues instead. Angle brackets
and entity syntax (such as `&lt;` or `&#60;`) are also refused: literal markup
and entity preservation is not qualified for this SubRip export. Write spoken plain text instead. The
renderer repeats these format checks, escapes ordinary ampersands in VTT,
and preserves plain text in SRT and the transcript. This bounded subset is
qualified with the native browser track and real FFmpeg subtitle decoding.

Shots are `title`, `diagram` (two to five `nodes`), `excerpt` (image/video
`asset_id` and optional `start_seconds`) or `blender` (declarative `scene`
geometry). These are composition-local rendering choices. They do not extend
Goal/Todo state. Likewise prepared/rendering/ready/failed/cancelled/interrupted
describe private render attempts, never published content or Agent liveness. An
old saved running attempt is shown as interrupted after its bounded execution
window; a read does not restart it.

Preparation takes an explicit local source directory. Absolute paths,
traversal, symlinks and wrong source digests are refused. The prepared record
contains immutable private source copies; editing the original afterward does
not change an admitted composition. No URL is fetched. A `source_ref` records
provenance supplied by the caller; a `public_safe` label does not prove rights,
truth, current product behavior or public publication permission.

The canonical digest binds story, sources, source boundaries, layout tokens,
geometry, timing, captions and narration. Canonical plans can be revalidated
without changing that digest. A changed plan requires the expected revision
and invalidates old content approval. Existing layout tokens are reused;
static page-density rules do not certify a timed movie or its learning effect.

`body` is supported for title and diagram shots. Excerpt and Blender layouts
show a title and source boundary, and reject nonempty body text at admission
and rendering. A qualifier must be retained in a supported visible field,
separate title scene or timed caption; it must not silently disappear. Existing
prepared spatial plans with nonempty body now fail explicitly and need a new
revision that preserves that meaning in a supported field.

## Execution and readback

```sh
loopx content-ops media prepare --goal-id example-goal \
  --plan-json examples/content-ops-media-brief.json --source-directory . \
  --format json
loopx content-ops media render --goal-id example-goal \
  --composition-id COMPOSITION_FROM_PREPARE --expected-revision 1 --format json
loopx content-ops media inspect --goal-id example-goal \
  --composition-id COMPOSITION_FROM_PREPARE --format json
```

`list` discovers the latest 100 private compositions; `cancel` requires the
current composition and revision. An already running render is returned without
a second effect. Cancel is a request until the worker settles. After a failed,
cancelled, interrupted or damaged attempt, explicit render can retry. Intact
completed bytes are reused. A changed story uses `prepare --composition-id …
--expected-revision …` first. The render lock spans the actual effect, in
addition to revision-bound record writes.

The existing extension lifecycle owns installation, enablement, doctor,
entrypoint identity and the `content_media_write` permission. Loading the page
or preparing a plan never installs a provider. Disabled/unavailable extensions
block new effects while preserving prepared sources and verified downloads.
See [installation, dependency and disable instructions](../../../packages/loopx-media/README.md).

The private `content_ops_media_renderer_v0` envelope carries the validated plan,
layout tokens, digest, frame count, immutable snapshots, exclusive attempt
directory and attempt-scoped cancel flag. Only a verified installed extension
receives those paths. The result projection accepts fixed output names and
exact SHA256/byte counts; paths and undeclared provider fields are discarded.
The core reads every actual file before committing `ready`. Required outputs
are `video.mp4`, `poster.png`, `captions.vtt`, `captions.srt` and `transcript.txt`,
plus `narration.wav` for audible modes. Full decode must match the admitted
timeline, geometry and narration mode. Audio checks report peak and clipped
PCM samples; subjective listening remains untested.

Both plan and output bytes bind the existing `content_ops_item_v0`. Producing
or re-encoding a movie revises its content digest and clears any approval and
delivery intent. This renderer neither approves nor posts. Publication and
its account/channel/timing/readback remain with the
[existing item lifecycle](content-ops-item-lifecycle-v0.md).

## HTTP boundary and failure behavior

The loopback Chat server exposes `POST /api/chat/content-media` for list,
inspect, render and cancel. Render/cancel use the current revision; the server
binds the registered Goal/source route and rejects a different selected Goal,
foreign origin or filesystem fields. Rendering runs in a bounded background
worker so the page remains responsive. Polling occurs only while a render is
pending and stops after a terminal readback.

`GET /api/chat/content-media/file` serves only fixed verified files from that
composition, with byte ranges for playback, no-store and same-origin resource
policy. It refuses escaping filenames, non-ready and damaged artifacts; it
does not serve the private manifest or source files. Browser error projections
do not include provider stderr or private paths.

Missing provider, changed source, stale revision, text/glyph overflow, short
footage, invalid narration timing, cancellation, stopped host and damaged bytes
remain distinguishable from success. Repair the source/brief as a new revision
or repair the existing extension and retry. No failure creates publication
authority, fills missing footage, infers a creative pass or changes a Goal.

Mechanical rendering, creative quality, audience understanding and operations
impact have separate evidence. Future generated media and interactive
learning providers should reuse the nearest content owner and comparison
method; they are not silently activated by this protocol.

## Blender shot and project readback

The optional `loopx-media` provider can compute a `blender` shot inside the same
composition. `scene` is a bounded declarative object: an orthographic camera
with start/end position and target, box/sphere objects with material values
and optional position keyframes, area lights and connections between object
IDs. The shared `content_media` TypeScript owner validates it; provider Python
does not choose the work, sources, publication state or execution authority.
See the runnable [synthetic example](../../../packages/loopx-media/examples/blender-handoff.json)
and [activation/recovery guide](../../../packages/loopx-media/README.md#optional-blender-scenes).

Scene IDs and geometry are bound to the plan digest. Position keys must start
at frame 1, end at the scene's final frame and increase strictly; the first
position must match the object's declared initial position. A scene supports
at most 16 primitives, 3 lights, 24 connections and 120 frames. At most 3 Blender
scenes fit inside a composition. Existing dimension/timeline/output/process
bounds apply unchanged. Arbitrary scripts, uploaded blend files and external
asset references are rejected. Ordinary scene-free plans retain their existing
normalized structure and digest.

Blender 4.5 LTS/Cycles/CPU produces actual RGBA and depth PNG sequences; depth is
16-bit Z distance normalized over 0–50, with farther values clamped. A fresh
process opens the saved blend with scripts disabled and checks its geometry,
animation, camera and timeline. Ready results additionally require the exact
admitted scene IDs/frame counts, both complete passes, reopen checks and the
fixed `blender-project.zip` artifact. The archive contains relative-path blend
projects, exact scene inputs, the replay builder and frames. It is subject to
the same digest/readback/download scope as the movie; it cannot be attached
to a scene-free result to bypass admission.

The existing workbench renders/cancels/retries the composition and exposes the
verified project download. Installation of the Blender extra is explicit;
missing bpy does not disable ordinary 2D work. A failed 3D attempt retains its
prepared input and supports retry after dependency repair. No fallback replaces
3D with a code projection. Mechanical readiness still does not establish
creative quality, audience understanding or publication approval.

## Per-shot picture framing

An `excerpt` or `blender` shot can opt into `framing: "full_frame"` with
`loopx-media` 0.3.0. The source fits the available width/height without cropping
or changing its aspect ratio; a small image may be enlarged. Its complete
`boundary` is wrapped in a separate measured strip below the picture. Neither
that strip nor the optional player captions are baked over source pixels.
The shot title remains timeline/source metadata; put a title that the viewer
must see in the source artwork, a title shot or timed captions. The fixed studio
header, drawn shot title and progress bar are omitted for this shot. The
selected layout's background/text tokens still apply. This is a rendering
choice, not an audience-understanding or creative-quality judgment.

Omitted framing and explicit `"editorial"` normalize to the original plan
structure and retain the existing digest/layout. Other values and framing on
non-spatial shot kinds are refused. Nonempty spatial `body` remains refused.
Full-frame framing changes the source-bound digest and needs a new revision;
it cannot inherit earlier content approval. The result additionally requires
`qa.full_frame_shots` to match the admitted ordered shot IDs. An older provider
that ignores the field cannot produce a ready result; explicitly upgrade the
optional package/manifest and retry after doctor. Default framing remains
compatible with the older provider. No core setting or global installation is
changed by preparing a full-frame plan.

The Agent prepares this choice in the same brief; the packaged workbench shows
“Full frame” beside its timeline/source entry and renders, cancels, retries and
reads back the same composition. Users do not enter source paths or JSON again.
Disabling the existing extension blocks new renders and keeps verified movies
readable. To return to editorial framing, prepare a revision without the field
and render it; an older LoopX core will refuse a plan containing the new field.

中文：`excerpt` 与 `blender` 镜头可选 `framing: "full_frame"`。素材按原比例
完整放入较大的画面区，必要时放大；来源说明在独立底栏完整换行，工作室页眉、
片内标题和进度条不再绘制。标题仍在分镜与来源信息中，需要观众看到的标题由素材、
标题镜头或字幕承载。省略该字段或显式选择 `editorial` 保留原计划摘要与构图。
此选项需要显式升级 `loopx-media` 0.3.0、注册新 manifest 并运行 doctor；旧渲染器
若忽略新字段，结果会被拒绝就绪。修改构图需新修订，仍会撤销旧内容批准。
工作台沿现有入口显示“大画面”并制作、取消、重试和查看；停用扩展阻止新制作但
保留已校验影片。回到旧构图时，移除字段后保存新修订再制作；旧版核心不会接受
含新字段的计划。该选项不改变来源可见性、发布权限或全局配置。
