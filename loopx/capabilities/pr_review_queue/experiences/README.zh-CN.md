# 仓库中的评审经验

这些公共来源随 Git 版本保存，由 `pull-request-review` 持有。
`experiences/<owner>/<repository>/` 下的有效 JSON 使用现有
`procedural_experience_contract_v0`，不新增记忆 schema 或可写 provider。
通过经过 review 的 PR 替换或删除来源来更新、退休经验；每个案例只保留一个有效版本，
历史由 Git 保存。

[#5944 案例](pr-5944-review-frame.md)分别保存机器评审和维护者指引下的模型评审。
[蒸馏经验](loopx-project/loopx/pr-5944-v1.json)包含适用条件、观察、归因、未来行为、
局限与公共证据引用。历史 verdict 标签保留在案例文档中，召回不读取这份文档。
self-repair 指向同一来源，不另存一份经验正文。

## 让已开启的 review Agent 使用

在 Goal 能力中心现有 **Reward Memory 实验**编辑器中启用已注册的 review Agent，
应用其版本化配置。配置须包含 `automation.automatic_recall=true`，以及明确的
`pull_request_review.review` surface，并为它指定现有 corpus ids 和 recall profile。
保留正常的 provider preflight 和精确回读。参见
[Reward Memory 配置](../../reward_memory/README.zh-CN.md)；本切片不新增开关、
provider 写入或自动导入。

在原有私有配置中添加如下 surface，将 corpus id 替换成已有 owner 的 id，
并在对应 corpus 和 standing policy 的 `surface_ids` 中包含此 surface：

```json
{
  "surface_id": "pull_request_review.review",
  "adapter": "scoped_feedback",
  "corpus_ids": ["reviewed_experiences"],
  "ingest_corpus_id": "reviewed_experiences",
  "recall_profile": {
    "profile_id": "pr_review",
    "mode": "function_boundary",
    "max_queries": 1,
    "limit": 3
  }
}
```

通过现有 Goal 编辑器或 CLI 应用并回读：

```sh
loopx configure-goal --goal-id GOAL \
  --reward-memory-config .loopx/config/reward-memory/experiment.json \
  --reward-memory-agent REVIEWER --execute
```

```sh
loopx reward-memory experiment-status --goal-id GOAL --agent-id REVIEWER --format json
loopx pr-review --goal-id GOAL --agent-id REVIEWER --repo loopx-project/loopx \
  --target-exact-head NUMBER@HEAD_OID --format json
```

可执行 review 行可能包含 `repository_experience.decision` 和
`repository_experience.guidance`。内置 reader 只搜索所选仓库的打包文件，
校验现有经验契约，并在投递最多三条匹配经验前精确回读文件。
它组合现有 corpus、candidate、recall 与 TypeScript decision owner。
BM25 只排列词汇匹配，不判断适用性或 verdict。SDK 的 `provider_call_count`
计数本地 reader 调用；这条路径不调用或写入外部记忆 provider。

review skill 在判断当前 diff 前读取建议，在现有 `problem_context` 判断中，
以当前 head 证据说明采用、拒绝或不适用。`context_delivery_verified=true` 只证明
packet 投递；`semantic_disposition=null`、`decision_consumption_complete=false`
和 `utility_verified=false` 保留，直到各自有独立证据。
普通五段式评审仍返回原有会话；此内部来源 reader 不另建产品页面，也不授予 Agent 权限。

这是明确披露的 opt-in 行为扩展：已资格化、开启自动 recall 并配置 review surface
的 Agent 会收到匹配的仓库建议。关闭、未绑定、其他 surface、配置过期、未资格化和
评审其他仓库时保持原 packet。仅 inventory 或 merge-readiness 行不新增 recall。
无效或读取中变化的文件保留空 guidance，不形成用户 gate。
JSON 和 Markdown 可回读空结果及其 decision；检索失败不被标成经验已采用。

关闭时设 `automation.automatic_recall=false` 并重新应用现有配置，或移除 review
surface 后重新应用。清除整个实验：
`loopx configure-goal --goal-id GOAL --clear-reward-memory-config --execute`。
降级移除此 reader，无 provider 或持久运行状态需要迁移。仓库发布、review/comment、
push、merge 与跨 Agent 写入仍遵循原授权边界。

## 下一步证据

当前案例交付的是已整理经验与可运行的上下文投递路径，尚未证明评审质量提升。
后续 pilot 必须固定同一评审口径、产物、模型和预算，对 held-out 案例隐藏旧 verdict，
保留分歧、误阻断、成本和人工注意力。验证后的采用与工作结果，才可进入现有
[效用归因 RFC](../../../../docs/architecture/rfcs/post-outcome-memory-utility-attribution-v0.zh-CN.md)。

[English](README.md)
