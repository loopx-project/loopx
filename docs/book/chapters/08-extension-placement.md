# 先选择正确的放置位置

"要扩展 LoopX"不自动意味着"新建 Extension"。先判断用户结果、调用合同与生命周期，才能决定
能力应放在 Capability、Provider、Extension，还是项目内部 helper。

## 从一个坏的结局开始

一个团队要给 LoopX 接第二种行情数据源，于是新建了 `loopx-market-connector` package，写一份
manifest，声明一个 `[[provides]]`，`loopx capability list` 里立刻多了一行 `market-connector`。

三周后回头看，这一行的状态是：

```text
market-connector: declared=true, installed=true, enabled=true, ready=true
                  callers=0, resolver=none, domain policy=none
```

它可发现、可路由、能通过 doctor，但没有任何 caller 用它的结果，没有 resolver 归一化它的输出，
也没有一段 domain policy 规定它的 transition。控制面因此背上一份没有真实 caller 的合同：每个
新增的 Provider 都要考虑"是不是也该注册成 capability"，而唯一能回答这个问题的人已经换项目了。
第二周埋下的是另一个版本。有人发现三个内部模块都要做同一种状态归一化，于是把那段代码提成
`loopx/capabilities/state-normalization/`，理由是"涉及多个文件"。半年后这个 capability 只有一个
内部 caller，却要求 manifest、doctor、版本兼容和 catalog registration 一路维护。

两个坏结局的共同点：**放置决策在写代码前就做错了，而错的方式都是让抽象的边界宽于真实调用者的
需求。** 一个多出没有 caller 的公共合同，一个多出没有外部消费者的安装与生命周期成本。

## 为什么"以后再补 caller"行不通

自然的反应是先注册、后接线。但顺序反了会在控制面留下具体的代价：

- **catalog 是权威读模型。** 一份声明会进入 `loopx capability list`，被 dashboard、agent 和 review
  读取。读者无法从这一行区分"已发布的合同"和"先占位的名字"，于是要么照着它写调用，要么逐渐
  不再信任 catalog；
- **registration 决定 routing。** 一旦有条目，决定"谁该处理这个结果"时它就会成为候选，而它没有
  domain policy 可以裁决 transition；
- **安装成本立刻生效。** capability 落地即意味着一份要长期维护的版本兼容窗口，而它换来的收益
  （被 caller 使用）此刻并不存在。

反方向的错误同样常见：把只服务当前项目的 helper 注册成 capability，期望"将来别人能复用"。将来
是否复用无法验证，安装成本却是现在就要付的。

放置决策的作用是在动手前暴露这两种错误。它产出的是一段 rationale，而非更多设计文档。

## Capability 与 Extension 是两个维度

**Capability** 描述调用者可以获得什么结果，以及结果要满足什么合同。

**Extension** 描述一个实现如何被交付和管理，包括安装、启停、升级、回滚与兼容性。

真实仓库里这两个维度各自有归属路径：

```text
loopx/capabilities/<capability>/   caller-facing contracts and core providers
loopx/extensions/                  extension lifecycle and bundled providers
packages/<package-id>/             independently installable distributions
```

`loopx/extensions/` 随 LoopX wheel 一起发布，`packages/` 的子目录有自己的打包元数据，不进入
wheel；仓库根部刻意没有 `extensions/` 目录，因为那个重名会掩盖"这是可导入的 LoopX 代码"还是
"这是可单独安装的产物"。

两个维度不合并，它们通过 capability/provider registry 组合：

```text
Capability: caller-facing outcome contract
      ^
      | implemented by
Provider
      ^
      | delivered by
Extension: package and lifecycle
```

每个注册的 capability 声明三个 provider-facing 字段：`origin`（`builtin` 或 `extension`）、
`visibility`（`public` 或 `internal`）、`provider_id`（`loopx-core` 或 extension manifest id）。
重复的 capability 或 provider id 会 fail closed。

因此一个 Extension 可以：

- 提供一个新的 Capability；
- 实现一个已有 Capability；
- 只暴露自己的 bounded standalone command。

一个 Capability 也可以由 LoopX core 内置 Provider 实现，不需要 Extension。

## 案例：财经发现 Extension

当前 `loopx-finance-value-discovery` 是一个容易被名称误导的真实案例。它处理财经研究 packet，
但当前 manifest 没有 `[[provides]]` 或 `[[implements]]`，也不会向 Capability catalog 注册
`finance-value-discovery`。

它确实声明了 `[[presentation_surfaces]]`（`investment-research`，`visibility = "public-safe"`），
这属于 Extension 自己的呈现合同。呈现面与 capability 无关：把结果画成一张 dashboard，不产生
可被 routing 的 domain 结果。

官方 placement 指南让 Finance value discovery 保持 standalone extension 形态：公开市场、披露和新闻
采集可以留在该 extension 内，直到出现真实的跨 Provider LoopX 合同；value connector 协议也明确不允许
凭空发明 `finance-value-discovery` Capability。本章以下判断以当前 manifest、catalog readback 和
managed runtime 为准。

它的 placement rationale 是：

```text
capability_id: none
provider_id: loopx-finance-value-discovery
origin: extension
placement: separately activated package
reason: deterministic reducer over caller-supplied frozen public-safe evidence;
        independent package and lifecycle; no provider-neutral caller contract yet
```

为什么不是 Capability：

- 公开调用合同目前是这个 Extension 自己的 `finance_value_discovery_extension_v0`；
- input 是调用方已经冻结的 `finance_value_discovery_input_v0`，并非"帮我发现投资机会"这类宽泛请求；
- provider 只输出有界研究 packet；
- 没有多个可替换 Provider 共享的 caller outcome、resolver 和 domain policy；
- 当前 Capability catalog 不承诺 `finance-value-discovery`。

为什么适合作为 Extension：

- package、版本、doctor、启停和 upgrade 可以独立于 LoopX core 管理；
- manifest 与 runtime 的 permissions 都为空；
- reducer 不自动拉取行情，不读取账户或持仓，不发起交易，也不产生持续监控；
- 同一份 frozen public-safe evidence 可以得到确定性结果；
- generic `extension run` 不会绕过任何外部 effect authority。

它的当前边界可以画成：

```text
public evidence collector or human review
  -> frozen finance_value_discovery_input_v0
  -> loopx-finance-value-discovery Extension
  -> bounded finance_value_discovery_packet_v0
  -> human / Goal decides whether a successor is justified
```

安装、启用和运行是三种不同证明：

```bash
# package entrypoint 进入当前 Python environment
python3 -m pip install ./packages/loopx-finance-value-discovery

# extension runtime 记录并激活经过 doctor 的 manifest revision
loopx extension install \
  --manifest packages/loopx-finance-value-discovery/extension.toml \
  --execute \
  --format json

# managed runtime 处理一个已经冻结的公开证据输入
loopx extension run loopx-finance-value-discovery \
  --input-json packages/loopx-finance-value-discovery/examples/paypal-debeta-discovery.json \
  --execute \
  --format json
```

这些命令要求 provider 源码包可用；它当前不是 bundled Extension，LoopX 不会替用户下载 package。
`extension list` 证明 activation state，executed doctor 证明当前 revision 的 readiness，示例 run
证明 request/response contract。三者不能互相替代。

package 还必须安装在运行 `loopx` 的同一 Python environment，并让
`loopx-finance-value-discovery` entrypoint 出现在当前 `PATH`。只调用某个 venv 里的 `loopx`
绝对路径、却没有让同一 venv 的 provider entrypoint 可解析时，doctor 会返回
`entrypoint_missing`；这是正确的 fail-closed 行为。

### 什么时候应升级成 Capability + Provider

如果将来 LoopX 要向多个财经数据或研究 Provider 暴露稳定、provider-neutral 的调用结果，就应先
定义 Capability contract，例如统一的 input、evidence freshness、authority、failure、readback
和 successor policy。之后这个 package 可以通过 `[[implements]]` 成为其中一个 Extension
Provider。

不能反过来因为 package 名称含有"财经发现"，就先注册一个没有真实 caller 和 resolver 的
Capability。数据采集也不应偷偷进入这个零权限 reducer；公开市场、财报和新闻采集需要自己的
Provider 边界、来源 freshness、license 与 credential Gate。

## 四个候选位置

### 1. 项目内部 helper

如果代码只服务当前项目，没有独立调用合同、安装需求或生命周期，就放在最近的 owning module。

例如当前项目需要把两种内部状态转换成统一 dict，但没有外部调用者，也没有独立版本兼容要求。
把它注册成 Capability 或打包成 Extension 只会增加 manifest、doctor 和升级成本。共享代码说明
可能存在 helper，它并不说明需要独立安装和生命周期。

### 2. 现有 Capability 的 Provider

如果调用者需要的结果已经由一个 Capability 定义，新实现应进入该合同，而不是创建同义 Capability。

Provider 可以是：

- built-in：随 LoopX core 一起发布；
- extension-delivered：由独立 Extension 提供。

是否访问外部系统并非判断 Extension 的唯一条件。一个和 core 同生命周期、由 core 始终维护的
connector 仍可以是 built-in Provider。

### 3. 新 Capability

只有当 LoopX 调用者需要 provider-neutral 的稳定结果合同、catalog identity 和 routing surface
时，才创建新 Capability。

新 Capability 至少需要：

- 清晰的 caller outcome；
- 稳定 id 与 versioned protocol；
- 真实入口或调用点；
- domain validation 与 transition policy；
- focused validation；
- catalog registration。

仅仅"未来可能有多个 Provider"不足以让抽象提前进入产品。

### 4. Standalone Extension

如果能力：

- 有独立 package 与版本；
- 需要独立安装、启停、升级或回滚；
- 有一个有界 request/response command；
- 不需要进入现有 Capability；
- 直接调用不需要任何权限；

那么 standalone Extension 是合适的起点。

本书的 `loopx-text-stats` 就属于这一类：它根据 request 中的文本计算统计值，不读文件、不访问
网络、不修改外部系统，也没有跨 Provider 的产品合同。

## 按顺序做 placement decision

官方指南要求按顺序回答五个问题，本书把它展开成六个。顺序本身重要：先定结果，再找 owner，最后
才决定交付形态。

1. **用户结果是什么？**

   不要用 `connector`、`adapter`、`sink` 这类实现机制代替结果名称。

2. **最近的现有 owner 能否拥有它？**

   如果现有 Capability 已经定义相同结果，扩展它。

3. **LoopX core 是否必须始终发布这个实现？**

   是则考虑 built-in；否则考虑 Extension。

4. **是否需要独立生命周期？**

   独立依赖、版本、启停、凭据或 provider ownership 通常指向 Extension。

5. **是否只是内部 helper？**

   没有独立调用合同就留在 owning module。

6. **是否有权限或外部 effect？**

   有则不能通过 generic standalone runner 绕过 Capability/domain policy。

### 平台级机制放哪里

在六个问题之外还有一条容易混淆的分支：如果待添加的只是所有 Extension 共享的注册与生命周期
机制，它属于 `loopx/extensions/`，不属于任何 provider package。它不构成 capability，也不需要
自己的 manifest。把这类机制塞进某个 provider，会让下一个 provider 被迫依赖前一个的私有结构。

## 记录最小 rationale

在实现前写一段短记录：

```text
capability_id: none
provider_id: loopx-text-stats
origin: extension
placement: standalone package
reason: bounded deterministic command with an independent lifecycle;
        no provider-neutral LoopX capability is needed
```

如果 Extension 实现已有 Capability：

```text
capability_id: <existing-capability>
provider_id: <extension-id>
origin: extension
placement: independently packaged provider
reason: reuses the caller contract but needs independent dependencies
        and activation lifecycle
```

记录的目的并非生产更多设计文档，而是在动手前暴露错误抽象。对于小改动，这段 rationale 可以直接
进入 Todo、PR 描述或 commit history。

## 代价与边界

放置决策并非免费的，它换来的是"抽象边界与真实 caller 需求对齐"，代价需要讲清楚。

**代价一：决定之后不能随手改交付形态。** 一个 package 一旦有独立版本与安装步骤，回退到
core 内置就要处理已有用户的升级路径。

**代价二：Extension 用户必须显式做事。** 独立生命周期意味着用户要自己安装、enable 并通过
doctor；core 内置的能力随安装即用。一份 manifest 被声明进 catalog read 时状态是
`declared=true, installed=false, enabled=false, ready=false`，它不会因为存在就可用。

**代价三：公开合同需要维护。** 新 Capability 应有稳定的调用者 outcome、真实入口和验证；一个 Provider 也可以满足这些条件。独立安装需求本身不足以建立新 Capability，provider 数量也不是准入门槛。

**边界一：这条决策管的是 LoopX 产品面的归属，不覆盖别人自己的仓库结构。** 独立的 provider 发行
放在自己的 package 或 repository，LoopX 只要求 manifest 与生命周期合同。

**边界二：`value-connectors` 属于兼容面。** 它是既有的 CLI 与 protocol surface，不应作为新工作的
公共 capability owner。新 profile 应迁到 `issue-fix`、`content-ops` 这类结果 capability，或者迁到
`loopx-finance-value-discovery` 这类 standalone extension，再退役该兼容面。

**边界三：这条决策不判断值不值得做。** 它只回答"放在哪"。一个结果本身是否有价值，由 Goal 与
acceptance 回答。

## 反例

### "这是外部 API，所以创建 connector Capability"

传输机制并非用户结果。先找谁需要这个 API 返回的结果，以及现有 Capability 是否已经拥有它。

### "先生成 `[[provides]]`，以后再接调用入口"

manifest 可发现但不可调用，会制造假的产品表面。先建立真实 caller contract、resolver、policy
和 validation，再声明 Capability。

### "standalone runner 能启动进程，所以也可以发消息"

generic runner 要求 manifest 和 runtime 的权限都为空。发消息、写文件、发布或管理资源是 effect，
必须进入能检查 authority 与 scope 的 Capability/domain command。

### "多个文件共用代码，所以抽成 Extension"

共享代码只说明可能存在 helper，不说明需要独立安装和生命周期。抽象应该跟随 change reason，
而不是文件数量。

## 本书示例的决定

`loopx-text-stats` 的 placement：

| 字段 | 决定 |
| --- | --- |
| `capability_id` | `none` |
| `provider_id` | `loopx-text-stats` |
| origin | `extension` |
| kind | standalone |
| permissions | `[]` |
| managed entrypoint | `loopx extension run` |

覆盖这条决策的可执行证据是 `examples/capability-extension-placement-doc-smoke.py`、
`tests/capabilities/test_capability_extension_registry.py` 与
`tests/capabilities/test_capability_extension_registry.py::test_duplicate_capability_fails_closed`：
第一项核对放置文档与目录归属；第二项核对内置 catalog 的来源，以及 extension 声明的 Capability 以
`declared=true, installed=false` 组合进 registry；第三项核对一个 id 被声明两次时 registry fail closed。
（`examples/capability-extension-registry-smoke.py` 当前在本机 main 上因内置 capability 清单写死而过期，
不要把它当成可依赖的证据入口。）

如果你要设计的不只是 standalone package，而是 Explore、Domain State、Capability Pack、
multi-agent preset、Provider 或 presentation 的组合，继续阅读
[Control-Plane Course 第 11 讲](/loopx/docs/development/control-plane-course/11-extension-layer/)。
它解释这些扩展面怎样复用 Kernel，而不是创建第二套 Goal、Todo、Quota 或 Scheduler。

## 不变式

六句可以自己核对的话。

1. **一个 capability 的存在理由是它的 caller outcome，不是它的目录名。** 找不到 caller、
   resolver 和 domain policy，它就不该出现在 catalog 里。
2. **capability 与 extension 是两个维度，一次改动可以只落在其中一个上。** 看到新 package
   就直接推断新 capability，是放置错误最常见的形式。
3. **共享代码并不构成升级成 capability 的理由。** 判据是独立调用合同与生命周期，而非文件数量。
4. **manifest 可发现并不等于合同可调用。** `[[provides]]` 与 `[[implements]]` 只有在真实入口、
   resolver 和 validation 就位后才应写入。
5. **Extension 的独立生命周期是用户可见的成本。** 声明它之前先确认这份成本换来了 caller
   需要的独立版本或凭据边界。
6. **effect 不能经由 standalone runner 绕过 authority。** 任何写文件、发消息、发布或管理资源的
   能力都必须进入 Capability/domain command。

下一章会从官方 `extension init` 生成这套结构，再只修改 request/response domain contract。
