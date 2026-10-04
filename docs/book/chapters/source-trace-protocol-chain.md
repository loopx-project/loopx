# 沿一条协议链定位实现

## 从一个静默的字段改名开始

先看一次没有报错的坏结局。它最危险的地方在于：没有任何一步失败。

```text
周一 14:02  一位贡献者认为 required_decision_scopes 这个名字太长。
            他把它重命名得更短，同时更新了写入 schema、CLI 写入路径
            和手边能看到的测试。
周一 14:40  受影响的 unit test 全部通过。改动范围看起来收敛在
            source 一侧，于是他提交了 PR。
周一 16:10  CI 全绿，reviewer 看到 diff 只有 schema 与 writer，
            PR 合并。
周二 09:31  Host 开始一轮新工作。frontier 里出现一个 publish Todo。
周二 09:31  它的 required scope 字段在新名字下不存在。reader 对缺失字段
            的处理是“视为空集合”，于是这个 Todo 显示为没有 scope requirement。
周二 09:34  “没有 scope requirement”意味着没有任何 Gate 覆盖它。
            quota 选择它，TurnEnvelope 打包它，Host 执行它。
周二 09:41  首页上线。
周二 11:20  用户发现首页已经发布，但自己的 approval Gate 仍然是 open。
周二 11:26  回看 run history：这条链上每一步的返回值都是成功。
```

从代码角度看，这位贡献者的改动做得相当规矩：改了写入方，改了 schema，跑了测试。他的错误在于只追踪了字段的**生产者**，没有追踪它的**消费者**。字段名同时是两侧的合同，改一侧就是改合同，而投影层的缺失字段回退把这次违反合同的行为翻译成了一次合法状态。

这个场景说明本章的核心动作：**追一条链路，直到你能说出每一处消费该事实的地方，以及当事实缺失时每一处会退化成什么。**

理解一个控制面行为，只看最终 status 或者某个 reducer 的输出都不够用。你需要从 source fact 出发，检查它经过哪些协议变换，最后形成什么 effect 与 receipt。

## 为什么“改完跑测试就行”不够

自然的做法是：改字段，跑测试，全绿就提交。问题出在测试覆盖的范围和链路覆盖的范围并不一致：

- **测试覆盖的是你想到的消费者。** 单元测试断言的是被改模块的行为，它不会替你发现另一个模块正在用不同方式读同一个字段。
- **消费关系跨语言、跨层。** 同一个字段可能被 schema 校验、被 parser 归一化、被 projection 重命名、被 renderer 格式化。这四处会分别演进，彼此并不引用。
- **缺失字段有默认值，而默认值会做出决定。** 缺失被回退成空集合、`None` 或空串时，系统不会报错，它会继续用这个默认值参与判定。这是坏结局最隐蔽的来源。
- **成功返回值不是证据。** 每一层都成功，合起来仍可能是错的。

所以调试的问题要换掉。不要问“最终 JSON 为什么不对”，要问“**第一处违反不变量的边界在哪里**”。

本章使用一个可复用场景：

> 发布首页需要用户批准；与此同时，修复内部链接检查器不依赖这项批准，Agent 仍应继续。

这个场景把 Gate scope、工作图、interaction channel、scheduler、bounded Turn 和 evidence 串在同一条链上。它也说明为什么“有一个 open user todo”不能被压缩成“整个 Goal 停止”。

## 读完之后你应该能

- 从一个可观察错误反推 source、projection、policy、effect 与 receipt；
- 使用协议字段和不变量设置源码阅读断点；
- 区分 source correctness、projection correctness 与 decision correctness；
- 识别同一字段在不同协议层被重新解释的风险；
- 为一条跨模块行为画出最小、可验证的协议链。

## 场景的不变量

先不读代码，写出期望语义：

```text
G1 covers publish_homepage
T1 requires publish_homepage
T2 requires no user decision

therefore:
  user_channel must surface G1
  agent_channel must not run T1
  agent_channel may run T2
  scheduler must preserve active work
```

禁止结果包括：

```text
G1 blocks the whole goal
G1 is hidden because T2 can run
T1 runs because the user was merely notified
host reconstructs another action from status prose
```

这四条禁止结果分别暴露过度阻塞、交互丢失、authority 泄漏和 Host 越权。

## 协议链全景

```text
typed Todo / Gate facts
  -> normalized todo and gate projection
  -> decision_scope coverage
  -> agent-scoped frontier
  -> quota precedence
  -> interaction_contract
  -> TurnEnvelope
  -> one bounded Host effect
  -> independent validation
  -> event / run / evidence writeback
  -> fresh projection
```

每个箭头都是合同边界。调试时不要问“最终 JSON 为什么不对”这样宽泛的问题；应定位第一处违反不变量的边界。

## 第 1 站：Source facts 是否足够表达意图

先检查 Todo 与 Gate 的 typed facts。下面是**为解释而简化**的形状，不是可直接导入的配置：

```json
{
  "todos": [
    {
      "todo_id": "publish-homepage",
      "task_class": "advancement_task",
      "required_decision_scopes": [
        "public_claim:action:bilingual_homepage"
      ]
    },
    {
      "todo_id": "repair-link-checker",
      "task_class": "advancement_task",
      "required_decision_scopes": []
    }
  ],
  "gates": [
    {
      "todo_id": "approve-homepage",
      "task_class": "user_gate",
      "decision_scope": "public_claim:action:bilingual_homepage",
      "status": "open"
    }
  ]
}
```

这里由 [`decision_scope_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/decision-scope-v0.md)
拥有 scope coverage 语义。最重要的在于：

- Gate 明确声明 kind、granularity 与 scope key；
- 被保护 Todo 明确声明 required scope；
- 独立 Todo 不继承无关 Gate；
- `user_action` 或自然语言提醒不被提升为 authority。

如果 source 只有“等待用户确认首页”一行 prose，系统无法可靠判断它是否阻塞链接修复。此时应补齐 source contract 或产生 repair，不应让 projection 猜测全局权限。

### Source 阅读断点

在源码中先找 Todo contract、Gate lifecycle 与 decision-scope schema，而非 status renderer。回答：

1. 字段从 CLI/event/workbench 的哪个受控入口写入？
2. missing、unknown 或 malformed scope 是 reject、repair 还是 compatibility fallback？
3. Gate resolve 时只消费被覆盖 scope，还是顺手清空全部要求？
4. retry 是否由 stable decision/event identity 保证幂等？

第 2 问正是开头那个坏结局的入口。如果缺字段会被回退成空集合，那么改字段名就等于悄悄删掉 scope requirement。如果这些问题没有答案，继续向下读 quota 只会放大歧义。

## 第 2 站：Projection 是否保留了关系

Todo 和 Gate 会进入多个只读表面：

- status summary；
- agent-scoped frontier；
- task graph；
- attention queue；
- quota input。

[`task_graph_projection_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/task-graph-projection-v0.md)
可以展示 `blocks`、`requires_decision`、`validates` 或 handoff relation，但它不创建 authority。

在本场景中，projection 至少要保留：

```text
G1 -> blocks -> T1
T2 -> independent of -> G1
```

如果它只输出：

```text
open_user_gate_count = 1
```

却丢失 scope relation，后面的 policy 不可能区分 scoped block 与 global block。

### Projection 的三种故障

| 故障 | 表现 | 正确处理 |
| --- | --- | --- |
| Source 缺字段 | Gate 无 scope | typed repair 或 concrete user/controller question |
| Parser 丢字段 | Source 有 scope，projection 没有 | 修 read model，增加 parity fixture |
| Renderer 隐藏关系 | JSON 正确，Markdown 只写“waiting” | 修显示；不要改变 source 或 policy |

这三种故障看起来都像“状态不对”，但 owner 和验证完全不同。

### Projection 阅读断点

围绕 read-model contract 搜索，不围绕 HTML 或 Markdown 文案搜索：

1. projection 输入是否来自 canonical/public-safe source；
2. malformed、duplicate 与 truncated 数据是否带 diagnostics；
3. agent scope 是否保留 current、other-agent 和 unclaimed 的区别；
4. projection 是否 side-effect free；
5. renderer 是否只消费已完成的 typed payload。

修 renderer 不应改变 runnable frontier；修 parser 也不应悄悄赋予 Gate 全局 scope。

## 第 3 站：Policy 如何决定当前 frontier

现在把 projection 交给工作选择。候选集合应依次经过：

```text
open work
  -> dependency and resume
  -> gate scope
  -> claim and agent scope
  -> capability
  -> workspace guard
  -> freshness
  -> runnable frontier
```

本场景中：

- `publish-homepage` 因匹配 Gate 不可运行；
- `repair-link-checker` 不依赖该 Gate，仍在 frontier；
- Gate 本身继续进入 user channel。

这不是“安全绕过 Gate”。安全路径没有执行被 Gate 覆盖的 action。

### Ordered policy，而非零散布尔值

Quota 需要处理的不只是本场景，还可能同时看到：

- projection health failure；
- autonomous replan obligation；
- agent handoff wait；
- due monitor；
- capability gap；
- throttling 或 pause。

因此正确问题并非：

```text
if open_gate_count > 0, should_run = false?
```

而是：

```text
在当前 source facts 与 precedence 下，
哪个 typed interaction mode 拥有这一轮？
```

规则应能解释 first match、抑制条件和最终 reason。特别要测试负向规则：为什么存在 open Gate 时，独立 work 仍可运行；为什么存在 runnable advancement 时，不应凭空产生 monitor-derived replan。

### Policy 阅读断点

以 [State Machines](https://github.com/huangruiteng/loopx/blob/main/docs/product/core-control-plane/state-machine.md)
和 [Control-Plane Rule Seam Map](https://github.com/huangruiteng/loopx/blob/main/docs/product/core-control-plane/rule-seam-map.md)
为地图，确认：

1. 输入 facts 是否已经 normalized；
2. Gate、repair、replan、monitor 与 runnable work 的顺序是否显式；
3. `False` 决策是否也有命名规则和反例；
4. 最终结果是否由一个 authoritative interaction contract 表达；
5. scheduler 是否消费最终结果，而非重新检查低层 flags。

函数名可以帮助你找到当前 builder，但不能替代这五个检查点。

## 第 4 站：Interaction Contract 保留两个 channel

本场景的关键输出并非一个 `should_run`：

```json
{
  "user_channel": {
    "action_required": true,
    "selected_action": "review_homepage_preview"
  },
  "agent_channel": {
    "must_attempt": true,
    "selected_todo_id": "repair-link-checker"
  },
  "cli_channel": {
    "next_command_kind": "bounded_delivery"
  }
}
```

这个形状同样是**为解释而简化**。它表达的协议事实是：

```text
user needs to know about G1
and
agent still has independent T2
```

两个 channel 不一致并不矛盾。相反，把它们合并成一个 `action_required` 才会丢失信息。

`interaction_contract` 完成仲裁后，Host 不应读取 status prose，再自行决定“既然有人等确认，就先不运行”或“既然 Agent 能运行，就不用展示 Gate”。

## 第 5 站：TurnEnvelope 只承载已决定的下一轮

[`turn_envelope_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/turn-envelope-v0.md)
是 quota decision 上的 bounded read model。它应携带足够信息，让执行者知道：

- Goal、Agent 与 selected Todo；
- 当前 action 与 authority boundary；
- allowed write/effect scope；
- validation 与 writeback obligation；
- diagnostics 或 cold-path references。

它不应：

- 重新做 Gate coverage；
- 从自然语言选择另一个 Todo；
- 把完整 active state 或 raw transcript 塞进热路径；
- 因为 Host 支持某能力就授予权限。

如果 Envelope 选择了 `repair-link-checker`，后续 Turn 必须绑定这项 causal frontier。Host 的可恢复 session 不能把它替换成之前准备发布首页的旧动作。

## 第 6 站：LoopX Turn 形成 bounded effect

[`loopx_turn_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/loopx-turn-v0.md)
把一轮执行约束为：

```text
decide
  -> prepare
  -> invoke one bounded host segment
  -> independently validate
  -> write back
  -> spend at most once
```

对链接检查器修复来说，Host 可以编辑代码并运行聚焦测试，但不能顺便发布首页。即使同一 session 里早已准备好发布命令，当前 Envelope 也没有授予该 effect。

Turn 的关键身份至少应绑定 Goal、Agent、Todo、decision revision 和 idempotency/effect identity。中途停止必须产生 typed failure 或 resumable phase，而非靠读取 transcript 猜执行到哪里。

### Effect 阅读断点

围绕 Host contract 检查：

1. request 是否 typed、bounded 且包含 authority；
2. Host capability 是否显式声明；
3. dry-run、failure、timeout 与 denied authority 是否可区分；
4. result 是否绑定原 proposal/effect identity；
5. raw output 是否被限制在 local adapter state。

不要把“Host 命令返回 0”直接当成 Goal acceptance。

## 第 7 站：Validation 与 Receipt 闭合链条

Host 自报“修好了”只是一项候选结果。独立 validator 应检查本轮 postcondition，例如：

- 目标链接问题消失；
- 没有修改首页发布状态；
- 相关测试通过；
- diff 与 selected Todo scope 一致。

成功后，writeback 可以形成：

```text
run snapshot
  + validation receipt
  + artifact or commit ref
  + todo lifecycle event
  + quota spend event (when accountable)
```

失败也要形成有用状态：

```text
typed blocker
  + failed validation
  + safe retry/replan/repair route
```

Receipt 必须属于当前 revision。旧 commit 上的 link check 不能证明新改动通过；一次 host success 也不能替代独立 postcondition。

## 第 8 站：Fresh replay 验证没有投影漂移

写回后重新读取：

```text
canonical events
  -> status projection
  -> quota decision
  -> next frontier
```

健康结果应该是：

- `repair-link-checker` 已完成并带 validation evidence；
- 首页 Gate 仍 open；
- `publish-homepage` 仍被同一 scope 阻塞；
- quota 不会再选已完成的链接修复；
- scheduler 根据剩余 frontier 决定等待、运行其他工作或请求用户。

如果 run history 显示完成，但 status 仍列为 open，这是 projection gap；如果 status 正确，但 quota 继续选择旧 Todo，这是 decision/replay gap。不要手工修改两个展示面让它们“看起来一致”。

## 用协议矩阵代替调用栈笔记

完成一次源码追踪后，留下这样的矩阵：

| 边界 | 输入 | 拥有的不变量 | 输出 | 证明 |
| --- | --- | --- | --- | --- |
| Todo/Gate source | lifecycle command/event | typed scope and writer authority | canonical facts | schema/contract test |
| Projection | canonical facts | deterministic, read-only, diagnostic | normalized relation | parity fixture |
| Policy | normalized facts | ordered precedence, fail closed | interaction mode | decision table |
| Envelope | final decision | no re-arbitration | bounded request | contract test |
| Host | bounded request | capability and authority match | candidate result | fake-host smoke |
| Validator | candidate + source | independent postcondition | receipt/blocker | focused smoke |
| Writeback | receipt + revision | idempotent durable transition | event/run/spend | replay test |

这张表在函数移动后仍有用；一张调用栈截图通常很快过期。它也是开头那份 PR 缺的东西：一列“输入”就能暴露出 rename 的消费者还没找齐。

## 常见误读

### 误读一：从最终 Markdown 反推 authority

Markdown 是面向人的 projection。它可以省略字段，不能授予权限。

### 误读二：看到 Gate 就停止所有工作

只有 scope coverage 能决定 action 是否被阻塞。

### 误读三：把独立 fallback 称为 bypass

Fallback 没有越过 Gate；它选择了 Gate 不覆盖的另一条 frontier。

### 误读四：Host 成功就是 Todo 完成

Host result 仍需独立验证、writeback 和 fresh replay。

### 误读五：修一个函数就完成跨层协议变更

如果 source、projection、decision 和 receipt 的合同都受影响，只改一个分支往往会留下第二种解释。

## 代价与边界：追踪不是免费的

完整追踪一条链比读一个函数慢得多，而且它并不总是必要的动作。

**代价一：链路越长，追踪成本越高。** 本章的示例只有八站，真实链路可能穿过两三种语言和一次进程边界。把每一处修改都追到底，会让一处两行的修复也要读五份文档。可操作的做法是：先按影响面判断这次改动是否触及合同边界。只改 renderer 的排版时，不必重走 source 与 policy。

**代价二：你读到的代码是此刻的，文档是某次维护时的。** 追踪结束时留下的矩阵，会在下一次迁移中部分失效。它的价值在于记录下**当时**的边界归属，让后来者知道哪一处需要重新确认，而非给出永久答案。

**代价三：追踪会给出信心，而信心可以被局部完整误导。** 走到第八站、看到 fresh replay 正常，会让人以为链路完整。开头那个坏结局正好相反：每一步都成功，链却断了。

**边界一：什么时候读源码比读协议文档更可靠。** 当你要判断“这一版实际怎么处理缺失字段”时。回退值、默认值和解析顺序写在代码里，协议的概述通常不会覆盖每种 malformed 输入，而这类细节往往正是坏结局的落点。

**边界二：什么时候读协议文档比读源码更可靠。** 当你判断 authority、scope coverage 或某次变更的合法性时。一个字段可能在四五处被读写，只有一份是 canonical；逐个读实现容易把某个 consumer 的局部行为当成合同本身。

**边界三：追踪能定位，不能授权。** 走完一条链不会让你有资格修改它。是否属于你、是否需要 maintainer 同意、切片是否过大，仍由公开贡献入口与 maintainer 判断。前一章讲的就是这条边界。

## 具名失败：两个能自己跑的检查

开头的坏结局可以被压成两条可以在干净 checkout 上执行的检查：

```bash
grep -rn "required_decision_scopes" loopx/ | wc -l
grep -rn "required_decision_scopes" loopx/ --include=*.ts | wc -l
```

第一条给出这个名字出现过的全部位置：schema、writer、parser、projection、fixture、renderer。rename 的合格标准，是这组位置的每一处都被重新确认过一遍，而非只改掉产生它的那几行。第二条提醒你两侧的语言边界：语义所有者可能已经在另一侧，只改 Python 会留下一个仍在读旧名字的 consumer。

第二条检查针对缺失字段的回退：

```bash
grep -rn "or \[\]\|or ()\|or {}\|\.get(.*, \[\])\|\.get(.*, {})" loopx/control_plane/todos/ | head -20
```

这些写法本身都合理，它们只是提醒你：**只要缺失字段会被回退成空集合，一次字段改名就能在没有任何报错的情况下删除一条 authority requirement。** 这与前述“每一层都返回成功”的坏结局来自同一个机制。

## 本章检查表

面对一个控制面 bug，依次确认：

- [ ] 第一处 source fact 是否完整、typed 且由合法 writer 产生？
- [ ] Projection 是否保留 relation、diagnostics 与 agent scope？
- [ ] Policy 是否使用显式 precedence 和 negative rules？
- [ ] Interaction Contract 是否分别表达 user、agent 与 CLI 责任？
- [ ] TurnEnvelope 是否只承载已仲裁结果？
- [ ] Host effect 是否绑定 capability、authority 与 proposal identity？
- [ ] Validator 是否独立于 Host 自报结果？
- [ ] Writeback 是否幂等，并在 fresh replay 后形成正确 frontier？

## 不变式

追踪结束后，下面几条应该能独立成立：

1. **每个被改名或改语义的字段，都要能列出全部消费者。** 只改生产者不算完成，缺失字段的回退值算一次消费者。
2. **链上每一层返回成功，不构成链路正确的证据。** 判断依据是第一处违反不变量的边界，而非最终返回值。
3. **缺失字段的默认值会参与判定。** 因此必须明确写出它是 reject、repair 还是 compatibility fallback。
4. **authority 只有一份 canonical 来源。** 多处可以读同一个字段，只有一处拥有它；读取方被误当成 owner 是跨层变更最常见的解释残留。
5. **判断 authority 时以协议文档为准，判断当前行为时以源码为准。** 两者冲突时按冲突类型选权威，并让同一次改动把另一方更新掉。
6. **追踪只负责定位，不负责授权。** 走到链尾不会改变这次改动是否属于你。

下一章不再只读这条链，而是实际设计一条 Control-Plane 规则变更：从 invariant 和 decision table 开始，再选择最小实现切片。
