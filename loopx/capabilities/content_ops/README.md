# Content-Ops Capability

The content-ops capability is the product path for creator/operator workflows:
public handles, private connector gates, source items, angle candidates, draft
states, feedback signals, and publish gates.

Connector packets remain preview-level. Local item lifecycle, reference
preparation and optional video composition have their own delivery boundaries;
none grants publication authority.

## Creation outcomes / 创作结果

Start with the requested work: its audience, message, factual sources and desired
form. Use the existing conversation and artifact preview/revision path with the
host's authoring tools and reusable Content-Ops operations. HTML, games and
mini-worlds can be standalone works, source material or scenes within a video;
their mention does not require a new LoopX workbench or a fixed teaching model.
Choose content, interaction, scene structure and visual style for the brief.
Templates and renderer examples are starting points, not a universal composition.

Use specialized host tools for custom editable scenes, animation or interactive
projects when the brief needs them. Their rendered image/video assets can enter
the existing source-bound composition path; retain the editable project with the
deliverable. The optional renderer's supported scene kinds bound that renderer,
not the creative methods available to the Agent. This guidance adds no new
authoring command or runtime execution permission.

Qualify a creation technique by using it in a real requested work, retaining the
editable project and sources, and making a meaningful revision. When comparing
video techniques, use the same factual brief and communication objective, allow
each technique to shape the film, and review complete results for message,
visuals, sound, pacing and full production cost. Keep mechanical checks, creative
judgment, audience observations and untested claims distinct. Follow the shared
[task-first delivery guide](../../../docs/development/frontend-delivery.md#task-first-delivery--从用户任务开始)
for the frontend journey; artifact creation does not require its own editor.

从用户要完成的作品出发，明确读者、表达目的、事实来源和作品形式。HTML、Game、
Mini-World 既可独立交付，也可成为视频中的素材或镜头；按具体 brief 创作，保留
可修改工程，通过实际作品及修改后的结果验收。复用已有对话和产物预览、反馈、
修订入口。用同一事实与表达目标比较各项视频能力，允许各自发挥构图与叙事优势，
同时审阅完整成片的表达、画面、声音、节奏与全部制作成本；机械检查、创意判断、
观众证据和未验证项分别记录。

### Source-led selection and whole-film refinement / 全景选材与整片精修

For an engineering launch or retrospective, inventory the topic's full relevant
timeline before selecting examples. Include early architectural decisions,
mid-course corrections, outcome evidence and counterexamples. Distinguish a
proposal, a merged implementation and demonstrated adoption. A recent PR, a
large diff or a striking number is not by itself the strongest story. Keep a
compact selection rationale with the draft: the audience question, mistaken
assumption, changed mechanism, decisive evidence and why this example matters.
Update linked articles and companion posts when that selection changes.

When a canonical film already exists, inspect the complete film and its source
description first. Refine from that source unless the brief calls for a new work;
retain its identity, reusable footage, editable composition and an edit list.
Remove superseded candidates from the active review set without erasing their
source history. Do not quietly substitute an unrelated recent demo.

Review the full proposed edit, not only its opening or poster:

- Give each passage a distinct viewer question and observable payoff. Prefer
  a concrete action, result or contrast to repeated positioning. Cut duplication
  across narration, captions, slide copy and adjacent shots. Information density
  means more understood information, not smaller type, faster speech or a fixed
  quota of cuts per minute.
- Let the evidence determine framing and duration. Keep units, conditions,
  counterexamples and source boundaries legible; preserve a result's limitation
  when moving or shortening its shot. Judge screenshots at the actual viewing
  size, including a narrow player, rather than only at editing resolution.
- Listen to complete narration and joins, and watch caption entry/exit against
  the image. Check names, pronunciation, emphasis, pauses, abrupt audio changes
  and music masking. Decoder success, PCM peaks and a supplied transcript cannot
  certify this listening judgment. Report it as untested if not performed.
- Keep the baseline and candidate available for comparison. Record what changed
  across the whole film, remaining creative weaknesses and production cost.
  Distinguish technical readiness, editorial judgment and observed audience
  comprehension; do not label an untested candidate the highest-quality result.
- Validate playback in the intended review surface, including seeking across
  edit joins, captions and source/candidate switching. A local decoder can accept
  a mixed-source export that the browser rejects. Repair the encoding and repeat
  that actual playback path before calling the artifact technically ready.
  Check which audio tracks the player actually selects: separate narration tracks
  may be treated as alternatives rather than mixed together. For one intended
  soundtrack, render a mix containing every required layer and check narration
  at the opening and inserted shots; a nonzero full-film peak can hide silent
  passages. This does not replace listening to the complete result.

工程宣发先按完整时间线铺开材料，再选能说明核心机制、技术价值和实际结果的
案例；近期 PR、改动行数和醒目数字不能替代选材理由。选材改变时，同步修订
文章、配套帖与影片。已有正式素材时先完整检查原片及来源，再保留工程与剪辑
映射进行精修，退下的候选退出当前审阅目录。

整片逐段检查观众问题、画面证据与新增信息，删除跨旁白、字幕和镜头的重复。
提高信息密度不靠小字、加速语音或强行缩短停顿。条件、单位、反例和来源必须
仍能读懂。完整听看声音衔接、字幕节奏和实际观看尺寸；未做听审或观众理解
测试就保留未验证，不以成功导出代替创作质量。在实际审阅播放器中检查完整
播放、跨剪辑点拖动、字幕和版本切换；本地解码通过不代表浏览器兼容。
还应核对播放器实际选择的音轨：独立旁白轨可能被当作备选轨，而非同时
混音。只有一套配音时，导出包含全部所需声音层的混音，并核对开场和新增镜头
是否有旁白；整片峰值非零不能排除局部静音，也不能替代完整听审。以上
复用现有作品修订和交付检查，不新增自动接受、发布权限、强制审批轮次或
独立视频编辑器。

The capability also ships a provider-neutral item lifecycle for real local
operations queues. It preserves stable item identity, revision-bound approval,
delivery receipts, exact readback, and supersession without copying draft bodies
or provider credentials into LoopX state. See
[`content_ops_item_v0`](../../../docs/reference/protocols/content-ops-item-lifecycle-v0.md).

`loopx content-ops queue-status` projects caller-owned item files into one
read-only managed queue surface with state counts and a next action. Priority is
the order of the `--item-json` inputs; windows live in item approval and delivery
intent records. See
[`content_ops_queue_projection_v0`](../../../docs/reference/protocols/content-ops-queue-v0.md).

The built-in layout template library moves reusable presentation rules out of
writing skills. `template-list` and `template-show` expose four generic visual
systems; `layout-plan` records typed page roles before
rendering; `layout-check` rejects sparse, overcrowded, overflowing, colliding,
or role-incomplete page sets. See
[`content_ops_layout_plan_v0`](../../../docs/reference/protocols/content-ops-layout-v0.md).

## Implemented Surface

`loopx content-ops media` prepares source-bound private compositions and renders
them through the optional local `loopx-media` extension. The packaged
**Goal → Files → Content artifacts** workbench renders,
cancels and reads the same compositions, video, captions, transcript and source
checks. It reuses existing layout tokens and content-item revision/approval,
while keeping heavy image/audio/video computation in the optional distribution.
See the [composition contract](../../../docs/reference/protocols/content-ops-media-v0.md)
and [renderer installation and recovery](../../../packages/loopx-media/README.md).

Creation and requested changes stay in the current Goal conversation; the results
view returns to that conversation without sending a message. Capability settings
retain configuration only and do not load production records or start work.
Delivery requirements and checks reuse the existing Goal acceptance owner, also
used by non-media deliverables; renderer checks do not create an acceptance or
publication decision. This integration is Goal-scoped. Ordinary project
conversations without a Goal are not routed through a fabricated Goal.

`loopx content-ops generation` prepares immutable, priced cloud-footage intents
and explicitly submits/checks/collects a bounded Seedance 2.5 video through the
same optional provider, with a separate cloud-generation permission. The
**Generated footage** view shows drafts, upload/usage consequences, original
task readback and verified local files. It never repeats an ambiguous paid
submission or equates stopping observation with remote cancellation. Use the
saved movie as a source-bound excerpt in a local composition; precise text and
diagrams remain local. See the same provider guide for credentials, cost
estimates, unavailable states and recovery.

`loopx content-ops image` prepares and submits one Seedream 5.0 Pro image with
an optional immutable reference through the same extension. **Generated images**
shows the draft, consequence, unavailable/unknown states and verified downloads.
This synchronous route uses a separate image credential, has no cloud task
polling and recovers saved responses without repeating a paid request. Compose
the resulting hash-bound image with exact local text; creative/source acceptance
and billing remain separate from byte readiness. See the provider guide's
[image setup and recovery](../../../packages/loopx-media/README.md#optional-generated-images).

`loopx content-ops reference` reads an existing private source/style catalog,
prepares version-checked capture/correction artifacts, retrieves topic/structure
references, and prepares outlines with original credit and reuse conditions.
The same TypeScript owner runs in the packaged **Goal → Files → Content artifacts
→ Prepare source and style references** disclosure. Imports stay in browser page memory; downloads are private
review artifacts. This preview does not write the original catalog or activate
a source. Inventory reuses Material Lifecycle, while managed apply/rollback
still requires that owner's qualified source provider. See
[`content_ops_reference_v0`](../../../docs/reference/protocols/content-ops-reference-v0.md).

| Layer | Current path |
| --- | --- |
| Capability module | `loopx/capabilities/content_ops/` |
| CLI entry | `loopx content-ops ...` |
| Protocol docs | `docs/reference/protocols/content-ops-surface-v0.md` |
| Queue projection | `docs/reference/protocols/content-ops-queue-v0.md` |
| Layout contract | `docs/reference/protocols/content-ops-layout-v0.md` |
| Smoke | `examples/content-ops-*-smoke.py` |

## Safe Defaults

- Public sources are metadata-first.
- Private connectors enter through owner gates or compact approved counts.
- Raw chats, transcripts, credentials, logs, and local paths are not copied into
  public packets.
- Publishing remains blocked until an explicit user decision.
- Revising approved content invalidates the approval and any delivery intent.
- Provider delivery and readback receipts must match the approved revision and
  digest; the lifecycle helper performs no external write.
- Layout acceptance never implies content approval or publishing authority.
- JSON read from stdin (`-`) is refused before parsing above 16 MiB
  (16,777,216 bytes); `--max-input-bytes N` sets a different positive limit on
  the subcommand. The limit counts input bytes, not process memory, and does
  not apply to JSON files.
- Built-in cover pages require `0.82–0.94` meaningful vertical density; the
  template field is `density.role_overrides.cover`.
- Built-in `page_sequence` defaults require a cover first, make it the density
  maximum, and require at least `0.72` meaningful density on interior pages;
  the final page keeps its closing/CTA role limits.

## Connector-First Ops Pattern

For social and creator operations, start with a connector source map instead of
drafting from memory:

```bash
loopx value-connectors source-map --format json
```

This packet gives a newly connected agent the current read-first connector
catalog, including public GitHub metadata, content-ops public handles,
browser-backed X research, Agent-Reach source routing, and finance snapshot
probes:

```text
doctor -> read-only source map -> maturity score -> ops brief -> draft packet
       -> publish/audit record -> compact monitor
```

The pattern lets a newly connected LoopX agent reuse external signals without
turning LoopX into a raw platform archive or untracked publisher. Even when an
owner grants broad posting discretion, the agent should still record the exact
body, account/channel, source map, timing, and stop condition before an external
post.

## Social Browser Provider

`content-ops` owns the built-in `social_browser_x` provider because public
social observation, source promotion, draft preparation, and publish gates are
content outcomes. The provider supplies one shared source profile, install
check, and metadata-only connector trial. The `value-connectors` CLI remains a
compatibility facade and delegates those packets without changing their output.

The provider does not open a browser, read a timeline, or publish. A real
browser session remains owner-controlled, and every external write still needs
the exact account, body, media/link plan, source references, and stop condition.

### Bundled experience before X preparation / 发 X 前的经验种子

Every new Agent can read the versioned, public-safe
[`x-composer-preflight-v1.json`](experiences/x-composer-preflight-v1.json)
from either existing provider entry point, without a memory account or network:

```bash
loopx value-connectors source-map --connector social_browser_x --format json
loopx value-connectors install-check --connector social_browser_x --format json
```

The JSON `operating_experience` field contains the full seed, version, package
content digest, applicability, observations, procedure and memory initialization
recipe. Markdown output exposes the compact lesson and points to JSON. The
seed ships in the wheel, not only in the source tree. Read it before editing a
composer, and revalidate the final body/card after every subsequent edit.

新的 Agent 不需要继承旧会话，也不需要先有 memory provider，就能从以上入口读到
完整经验种子。它属于 `content-ops` 的内置 `social_browser_x` provider；不新增
capability、扩展、内核规则或第二套记忆存储。

The seed includes a negative publication result: entering the main URL last
restored its composer preview, but a published long post bound `card.rest_id`
to the first supporting URL in its `note_tweet` URL entities instead. Input
order, body order and published-card selection are distinct. Retain every
required link and try the intended URL first in the actual body; this remains
a mitigation **without a verified successful published readback**, not an X
contract. Unknown target binding stays unknown. A rich-text `fill` can leave old paragraphs behind;
scoped native select-all/clear/insert plus exact readback is the recovery path.
Composer readiness and published-post verification are separate evidence. Do
not remove requested links or publish a test merely to obtain a card.

这里保存的是包含失败反例的操作经验：最后输入主链接只能改变编辑器预览，曾经
在发布后仍绑定到第一个合作者链接。保留多个链接，把目标链接放到正文第一个
URL 位置，是待发布后验证的缓解方案，不是已证实的修复。输入先后、正文顺序和
发布端选卡必须分开。不得为验证卡片擅自发帖，也不得用删链接代替解决问题。

### Initialize an Agent's own memory / 初始化自己的记忆

Bundled discovery is implemented; automatic seed import is intentionally **not**
performed. A host can initialize its own provider through the existing
Reward Memory `scoped_feedback` adapter, after corpus-owner authorization and
current-artifact review. Repository publication is not permission to write a
user's provider, and another Agent's private corpus is not a shared seed store.

1. Read `operating_experience` and deduplicate by the configured Goal/Agent scope
   plus seed id, version and `content_digest`. Review newer revisions rather
   than overwriting local corrections or reviving retired memories.
2. Resolve the Agent's existing
   [Reward Memory configuration](../reward_memory/README.md). Keep provider
   credentials and scope references in its owner-local configuration.
3. Prepare one `scoped_feedback_reward_memory_event_v0` using the seed's
   `content_summary`, `target_class=procedural_experience`, and source kind
   `reviewed_learning_card`. Map the seed's applicability, observations,
   procedure and limits into the required `procedural_experience_contract_v0`;
   bind its evidence refs to the reviewed seed revision. Use the seed
   id/version/digest as a stable source
   reference; fill workspace/project/user/peer and surface from the configured
   corpus. Set `requested_action_scopes=[]` and `raw_content_captured=false`.
   The configured standing policy still reviews the event. Set
   `current_artifact_verified=true` only after actual verification; package
   presence alone does not satisfy it. Never copy a ready-made verification flag.
4. Use the existing scoped import and require its exact provider readback:

   ```bash
   # event.json contains adapter, event and observed_at; preview first.
   loopx reward-memory ingest-event --goal-id <goal> --agent-id <agent> \
     --input event.json --format json
   # Only under the corpus owner's existing write authorization:
   loopx reward-memory ingest-event --goal-id <goal> --agent-id <agent> \
     --input event.json --execute --format json
   ```

5. For subsequent automatic retrieval, use the existing
   [`agent_workflow.turn_admission`](../agent_turn_recall/README.md) surface,
   configured to the compatible corpus with automatic recall enabled. Name
   actual X preparation in the selected Todo so its bounded query can retrieve
   the relevant experience. Memory availability is not proof of recall, and
   recall is not proof that the Agent applied or verified the lesson.

经验种子读取、写入个人 memory、下一轮召回、当前操作验证，是四个不同结果。
未配置或不可用时，直接参考仓库种子并如实报告，不冒称“已入库/已召回”。后续
平台行为变化应由新的验证经验修订或淘汰旧记录；不要提升为全局强制偏好。

No frontend/Lark configuration changes are introduced: these commands only
read packaged public guidance and retain existing memory settings and gates.
There are no new background imports, browser operations or external writes.
