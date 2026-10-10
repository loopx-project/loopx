# Content-Ops Capability

The content-ops capability is the product path for creator/operator workflows:
public handles, private connector gates, source items, angle candidates, draft
states, feedback signals, and publish gates.

Current implementation remains preview-level. It is useful because it gives real
connector and review surfaces a safe packet format before raw material is copied
or published.

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

`loopx content-ops reference` reads an existing private source/style catalog,
prepares version-checked capture/correction artifacts, retrieves topic/structure
references, and prepares outlines with original credit and reuse conditions.
The same TypeScript owner runs in the packaged **Capabilities → Goal → Reference
styles** workbench. Imports stay in browser page memory; downloads are private
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
