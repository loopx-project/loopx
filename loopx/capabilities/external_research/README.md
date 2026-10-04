# External Evidence Research / 外部证据研究

`external-evidence-research` is LoopX's provider-neutral contract for turning a
decision-bound research question into compact, auditable evidence. It unifies
two provider classes without pretending they are the same implementation:

- a host method such as the `external-research` skill; and
- a connector provider such as an official-document search integration.

`external-evidence-research` 是 LoopX 面向决策的通用外部证据合同。它统一两类
provider 的调用与回执语义，但不把两者伪装成同一种实现：

- host 提供的研究方法，例如 `external-research` skill；
- connector provider，例如官方文档搜索连接器。

## Contract / 合同

The lifecycle is:

1. `discover`: project method and connector inventory plus current readiness,
   while explicitly keeping registry presence, execution, and evidence coverage
   false unless separately observed;
2. `plan`: bind **object + user activity + decision** and required evidence
   kinds to one provider that is currently declared, installed, enabled, and
   ready, then content-address the normalized request, candidate inventory,
   selection, and execution envelope as `plan_id`;
3. provider execution: the selected host method or connector reads external
   sources under its own adapter and permission boundary;
4. `receipt`: require the caller-presented provider receipt to echo `plan_id`,
   reconstruct and verify the canonical plan, then record the receipt
   observation without treating it as provider execution attestation or
   claiming evidence coverage, admission, or automatic promotion;
5. `admit`: validate the exact request/provider identity and source-level
   provenance, bind the complete receipt digest, then record the parent agent's
   admit/reject decision;
6. downstream projection: pass only compact findings, limitations, direct
   references, evidence basis, dates, and content digests;
7. `retire`: revalidate the complete content-addressed admission, then retire
   rejected evidence immediately, or admitted evidence only after every
   admitted source reference appears in downstream readback.

生命周期为：`discover` 只读投影 method/connector 库存与当前 readiness，并明确区分
registry presence、真实执行和证据覆盖；`plan` 绑定“对象 + 用户活动 + 决策”并选择当前
真实 ready 的 provider，并用 `plan_id` 对规范化请求、候选库存、选择和 execution envelope
做内容寻址；provider 在自己的权限边界内执行；`receipt` 回传并校验 `plan_id`，只记录
caller 提交的回执，不把它冒充 provider 执行证明、证据覆盖、采纳或自动晋升；`admit` 校验请求、provider、完成时间、
完整 receipt digest 与逐来源 provenance，并记录父 Agent 的采纳/拒绝；下游只投影紧凑证据；被采纳的来源全部完成
下游读回后才可 `retire`，且 retirement 会先重验完整 admission identity。

The connector registry is only inventory and telemetry. A connector row marked
`supported` is projected as `ready=false` until a current provider lifecycle
readback proves installation, enablement, and readiness. Registration never
counts as execution or evidence coverage.

Connector registry 仅拥有库存与遥测。即使 connector 标为 `supported`，在当前
provider 生命周期读回证明 installed/enabled/ready 之前仍投影为 `ready=false`。
注册不等于调用，更不等于证据覆盖。

## CLI / 命令行

```bash
loopx external-evidence discover \
  --connector-registry \
  --format json

loopx external-evidence plan \
  --objective "Compare current behavior" \
  --user-activity "Choose an implementation" \
  --decision "Whether to adopt it" \
  --evidence-kind current_behavior \
  --evidence-kind counterexample \
  --provider-inventory-json providers.json \
  --format json

loopx external-evidence receipt \
  --plan-json plan.json \
  --receipt-json receipt.json \
  --format json

loopx external-evidence admit \
  --plan-json plan.json \
  --receipt-json receipt.json \
  --decision admit \
  --reason "Direct source answers the decision" \
  --admit-source https://example.com/original \
  --format json

loopx external-evidence retire \
  --admission-json admission.json \
  --downstream-source https://example.com/original \
  --format json
```

Provider inventory is an observation, not authority. A ready provider row uses
protocol `external_evidence_research_v0` and carries explicit `declared`,
`installed`, `enabled`, and `ready` booleans. Raw pages, transcripts, cookies,
credentials, and private notes remain provider-private.

## Ownership and product surfaces / 归属与产品入口

- The TypeScript contract owns request identity, provider admission, provenance
  validation, parent admission, compact projection, and retirement readiness.
- Python adapts the existing CLI and effect-runtime transport; it does not
  reimplement those decisions.
- Managed Turn callers can invoke the same effect-runtime methods:
  `external_evidence.discover`, `external_evidence.plan`,
  `external_evidence.receipt`, `external_evidence.admit`, and
  `external_evidence.retire`.
- CLI `readback` renders the same validated plan, source, parent decision,
  actual deepresearch-ledger coverage and retirement as Markdown. Existing
  conversation answer/report and Lark Markdown transports consume that output;
  no new UI configuration or evidence state machine is introduced.

TypeScript 是 discovery 真值边界、请求身份、provider 准入、provenance 校验、父 Agent 采纳、紧凑投影与
退休条件的唯一语义 owner。Python 仅适配 CLI 与 effect-runtime transport。Managed
Turn 复用同一方法；CLI `readback` 的同源 Markdown 可由现有会话答复/报告和 Lark Markdown 运输路径展示，不新建 registry 或状态机。

## Public GitHub method / 公开 GitHub 方法

The bundled `method:public-github` provider performs anonymous, bounded HTTPS
GETs only. It reads UTF-8 files from explicitly selected full commit SHA URLs.
No token, cookie, private repository, branch-head URL, redirect, proxy credential,
raw page persistence or automatic admission is used. Repository public visibility
is checked at planning and again for each execution read. A saved ready row alone
cannot authorize or prove a successful read.

该内置 method 只做匿名、有界 HTTPS GET，只接受显式选择的完整 commit SHA 文件 URL。
规划和执行均检查仓库当前公开性；不使用 token、cookie、私有仓库、分支 head、重定向、
代理凭据、原文持久化或自动采纳。保留的 ready 行本身不证明执行成功。

```bash
loopx external-evidence plan --public-github \
  --objective "Inspect public source" --user-activity "Choose a source" \
  --decision "Whether a literal is present" --evidence-kind literal_match \
  --source https://github.com/OWNER/REPO/blob/FULL_COMMIT_SHA/README.md \
  --search-term LoopX --format json > plan.json
loopx external-evidence execute --plan-json plan.json --execute --format json > execution.json
loopx external-evidence readback --plan-json plan.json --receipt-json execution.json
# The parent separately inspects findings and chooses admit/reject.
loopx external-evidence admit --plan-json plan.json --receipt-json execution.json \
  --decision admit --reason "Direct source answers this bounded decision" \
  --admit-source https://github.com/OWNER/REPO/blob/FULL_COMMIT_SHA/README.md \
  --format json > admission.json
loopx deepresearch start --project research --question "Inspect public source"
loopx external-evidence readback --plan-json plan.json --receipt-json execution.json \
  --admission-json admission.json --project research --execute --format json
loopx external-evidence readback --plan-json plan.json --receipt-json execution.json \
  --admission-json admission.json --project research
```

`execute --execute` authorizes source reads only. `readback --execute` authorizes
writing already explicitly admitted compact sources to the **existing** research
ledger. It requires the active research question to match the plan objective.
Retries for the same admission are idempotent. Wrong questions, unrelated existing
sources and exhausted source budgets remain blockers; partial projection stays
retained until every admitted source is actually read back with matching lineage.
Omit `--execute` for read-only projection; omit `--public-github` to avoid the
provider readiness probe. There is no persistent provider enablement to uninstall.
Original sources remain available on partial, empty and failed results. Literal
matches prove neither semantic conclusions nor evidence completeness.

`execute --execute` 只授权读取来源；`readback --execute` 只把已明确采纳的紧凑证据
写入现有研究账本，且要求研究问题与 plan objective 相同。同一 admission 可幂等重试；
问题不匹配、已有不相关来源或预算耗尽仍为 blocker。部分下游投影保持 retained，
直至全部已采纳来源以匹配 lineage 实际回读。省略 `--execute` 可只读回读；省略
`--public-github` 不探测该 provider。没有持久开关需要卸载。部分、空或失败证据
保留原始来源退路；字面匹配不证明语义结论或证据完整性。

Real qualification (anonymous network reads, disposable synthetic ledger):
`uv run --extra test python examples/public-github-evidence-live-smoke.py --execute-public-provider --source https://github.com/OWNER/REPO/blob/FULL_COMMIT_SHA/README.md`.
Packaged conversation readback: set `LOOPX_PERSONAL_WORKSPACE_PACKAGED=1` and
`LOOPX_PERSONAL_WORKSPACE_SCENARIO=external-evidence-readback`, then run
`node examples/personal-workspace-browser-smoke.mjs`.
Live authenticated connector and Lark delivery qualification remain separate;
this method grants no connector credentials or outbound-message authority.

真实验收会进行匿名网络读取并使用一次性合成账本；打包会话验收沿用上述环境变量和命令。
真实带凭据 connector 与 Lark 送达资格仍是独立边界；本方法不授予 connector 凭据或外发消息权限。
