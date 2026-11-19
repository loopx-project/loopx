# Agent Collaboration

[中文指南](#中文指南) · [Runnable example](../../../../examples/collaboration-delivery/README.md)

**Give Agents different responsibilities, then check the work they deliver together.**
Useful collaboration requires more than sending a message: a receiver must use
the right artifact, a reviewer must check the current requirements, and the
result must reach the person who requested it.

The current [three-Agent allocation example](../../../../examples/collaboration-delivery/README.md)
makes that chain inspectable. An analyst models the constraints, a builder
implements a solver, and a reviewer independently checks it. The owner then
changes the requirements; the team revises, reviews again and returns both
results to the original conversation.

## Follow a complete delivery

The example allocates limited stock across six orders, subject to regional
capacity, demand and a budget. It has a deterministic answer, so acceptance
does not depend on whether the final explanation sounds convincing.

1. **Model the problem.** The builder consumes the analyst's versioned
   `outputs/model.json`, rather than just acknowledging a message.
2. **Implement and review.** Inspect `solver.py` and `outputs/review-r1.md`.
   Independent review plus a separate exhaustive verifier check the actual allocation.
3. **Incorporate a correction.** Reserve stock and an East-region minimum change
   the accepted answer. Inspect the updated inputs and `outputs/review-r2.md`:
   the new version must be reviewed again.
4. **Return the result.** Inspect `outputs/final.md` and `conversation.json`.
   The result reaches the original conversation; repeating the return drain
   adds no duplicate delivery.

The initial allocation has value **84** at cost **1000**. After the correction,
it has value **83** at cost **990**. These are fixture acceptance values, not
performance claims. The runnable guide contains the exact allocation vectors
and verification commands.

Each phase uses a fresh `dsh` execution session. Registered Agent identities,
durable Inbox records and versioned files carry the work between sessions.
The example controller explicitly starts phases and transfers an allowlisted
set of artifacts through Git; the Agents assess requests, write the artifacts,
request peer work and report their conclusions.

## Try it

From a LoopX source checkout, inspect the commands and prepare an isolated run
without calling a model:

```sh
uv sync --extra test
uv run --extra test python examples/collaboration-delivery/demo.py --help
uv run --extra test python examples/collaboration-delivery/demo.py prepare
```

`prepare` requires a new `.local/allocation-demo` directory and creates a
disposable Goal, registry and three worker worktrees. It does not use your
active Goal. Follow the [live-run instructions](../../../../examples/collaboration-delivery/README.md#run)
to install the `deepseek-harness` extra, configure credentials through your
normal setup and execute the seven bounded model phases. Only explicit
`run ... --execute` calls invoke models and incur model costs. Stop on a failed
phase, inspect its evidence, and use the documented repair path before continuing.

This example covers **same-host, same-Goal collaboration**, including dependent
artifacts, independent review, revised acceptance and result return. Its
initial owner request is seeded by the controller. The example's browser
screenshots are separate synthetic UI fixtures, not screenshots of live model
outputs. See its [boundaries and recovery](../../../../examples/collaboration-delivery/README.md#boundaries-and-recovery)
for supported scope, stopping and tool removal.

## Go deeper

- [Semantic delegation and peer review](../../../../loopx/capabilities/manager_context/README.md#semantic-delegation-and-peer-review): requests, receiver decisions, artifacts and result return.
- [Project coordination](../../../reference/project-coordination.md): the roles of a personal steward, project coordinator and registered workers.
- [Runtime connector catalog](../../../integrations/runtime-connector-catalog.md): runtime-specific integration boundaries.
- [Early cross-runtime implementation/review design](cross-runtime-impl-review-demo.md): the Claude Code / Codex role contract and dry-run fixture. Retained as a design reference, not the current live delivery example.

<a id="中文指南"></a>

## 中文指南

**让多个 Agent 分工协作，交付可验收的结果。**
协作不止是“消息已送达”：接收方要真正使用正确版本的材料，审阅者要按当前
要求检查，最终结果还要回到发起人手里。

当前的[三 Agent 资源分配案例](../../../../examples/collaboration-delivery/README.md#中文操作说明)
把这条链路落在一个可以独立验算的问题上：analyst 建模，builder 实现求解器，
reviewer 独立复核；用户随后修改约束，团队重新计算、再次审阅，最后在原对话
返回两轮结果。

### 看什么，才算协作发生了

1. **建模与采纳。** 看 `outputs/model.json`：builder 实际使用 analyst
   的版本化模型，而不只是回复“收到”。
2. **实现与复核。** 看 `solver.py` 和 `outputs/review-r1.md`：reviewer
   独立审阅，另有穷举校验器检查分配结果，不能以 Agent 自称通过代替。
3. **吸收用户修订。** 看新输入和 `outputs/review-r2.md`：新增库存预留
   和东区最低分配量后，更新解并按新要求再次审阅。
4. **回传与读回。** 看 `outputs/final.md` 和 `conversation.json`：
   原对话收到结果；重复运行回传服务不产生重复投递。

问题包含六个订单、有限库存、区域容量、需求量和预算。第一轮应得到价值
**84**、成本 **1000**；用户修订后应为价值 **83**、成本 **990**。这是演示
输入的验收值；完整分配向量和校验命令见案例说明。

每个阶段启动全新 dsh 会话，持久身份、Inbox 和版本化文件连接前后工作。
控制器明确启动各阶段，并通过 Git 交接指定文件；Agent 自己读取和判断请求、
产出材料、发起同伴协作及提交结论。

### 如何开始

在源码目录运行上面的三个准备命令：`--help` 查看入口，`prepare` 创建全新的
`.local/allocation-demo` 隔离目录，其中包含独立 Goal、registry 和三个 worker
worktree，不使用你的活跃 Goal，也不调用模型。

随后按[完整运行说明](../../../../examples/collaboration-delivery/README.md#run)
安装 `deepseek-harness` extra，并用自己的凭据管理方式配置模型。七个显式的
`run ... --execute` 阶段才会调用模型、产生费用。阶段失败就停下，检查证据并按
修复流程重试，不把失败跳过当成验收通过。

这个例子覆盖的是**同主机、同 Goal 的产物依赖、独立审阅、要求修订和结果回传**，
不是 Claude/Codex 跨 runtime 实测，也不是无人值守团队调度器。初始请求由
控制器写入；配套浏览器截图是独立的合成 UI fixture。
停用工具、停止执行及完整边界见[恢复说明](../../../../examples/collaboration-delivery/README.md#boundaries-and-recovery)。

进一步阅读：[语义交办与同伴审阅](../../../../loopx/capabilities/manager_context/README.md#semantic-delegation-and-peer-review)、
[项目协作角色](../../../reference/project-coordination.md)、
[runtime 接入目录](../../../integrations/runtime-connector-catalog.md)。原有的
[Claude Code / Codex 实现—审阅 demo](cross-runtime-impl-review-demo.md) 保留为
早期设计与 dry-run 参考，不再作为当前实战案例的入口。
