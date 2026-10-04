# Replan planning before explicit selection / 显式选择前的重规划规划阶段

## Problem / 问题

A hard replan can be due before an agent has chosen any work. Previously its
recommended Todo could become the heartbeat settlement identity immediately,
without an explicit selection command. Reading owner preferences and choosing
another eligible Todo in that same Turn then conflicted with an identity the
agent never chose. Ordinary multi-candidate planning already kept its receipt
unbound; hard replan planning must preserve that distinction too.

硬重规划可能在 Agent 尚未选择工作时触发。旧路径会立即把推荐 Todo 绑定为
心跳结算身份，却没有给出显式选择命令。随后读取 owner 偏好并在同一 Turn
选择另一条合格 Todo，就会与从未真正选择过的身份冲突。普通多候选规划已经
保留未绑定回执；硬重规划的规划阶段同样必须区分推荐与选择。

## Existing-owner flow / 现有权威下的流程

1. An unbound, runnable Turn-scoped hard-replan guard with a Todo inventory projects the
   existing `action_portfolio` and `selection_command`, even for a sole
   candidate. Its recommendation is `default_not_binding`; there is no
   settlement plan yet. Preference reads do not bind the Turn.
2. An explicit Todo choice is requalified by the existing delivery frontier.
   A genuine hard replan retains that choice as deferred, not as delivery
   authority. The generated same-Turn guard reentry contains neither a Todo
   argument nor a replan identity argument.
3. The existing retained-selection reducer consumes the provisional portfolio.
   If the current projected Todo matches the retained choice, the established
   Todo-bound replan route resumes. If it differs, the established autonomous
   replan route resumes without binding the recommendation; the Todo choice
   waits for a fresh Turn after replan closeout.
4. Validation, durable semantic replan writeback and one quota spend use the
   resulting exact identity. A different explicit identity after binding still
   fails; replay cannot spend twice.

1. 未绑定且可执行、具有 Turn 身份及 Todo 规划清单的硬重规划检查，通过现有投影给出
   `action_portfolio` 和 `selection_command`，单候选也不例外。推荐仍是
   `default_not_binding`，此时没有结算计划；读取偏好不会绑定 Turn。
2. 真实选择由既有交付准入重新判断。真正的硬重规划会保留并延后选择，不授予
   正常交付权。生成的同 Turn 恢复命令不携带 Todo 或重规划身份参数。
3. 既有保留选择 reducer 消费临时 portfolio。当前投影 Todo 与真实选择一致时，
   恢复既有 Todo 绑定重规划；不一致时，恢复不绑定推荐 Todo 的自主重规划，
   原选择等待重规划结算后的新 Turn。
4. 验证、持久语义重规划写回和单次扣额使用最终确切身份。绑定后切换身份仍被
   拒绝；重放不会二次扣额。

## Boundaries and verification / 边界与验证

TypeScript's existing planning-packet and retained-selection reducers own the
new decision. Python transports current routing/receipt facts and applies the
typed result. This adds no store, scheduler, identity owner, configuration
field, or financial authority. Bound receipts, monitor-only lanes, admission
gates and Todo-less autonomous replans retain their existing behavior.
Unscoped diagnostic reads have no Turn receipt to bind and keep their existing
output instead of requiring a receipt-selection recovery.

新增判断由 TS 现有规划 packet 和保留选择 reducer 持有。Python 只传递当前
路由/回执事实并应用类型化结果。不新增存储、调度器、身份权威、配置字段或
金融授权；已有绑定、纯监控、准入 Gate 和无 Todo 自主重规划保持既有行为。
没有 Turn 身份的诊断读取没有待绑定回执，保持原有输出，不增加回执选择恢复步骤。

The changed entry points are `quota should-run` and its managed Turn envelope.
This is worker planning, not a new owner-facing Todo picker or capability
setting: the frontend and Lark configuration schemas/controls do not change.
The existing interaction contract supplies selection and recovery commands;
the real CLI regression also verifies the Turn envelope's action/writeback
boundary. No browser interaction or Lark delivery is claimed by these tests.

变化入口是 `quota should-run` 及其托管 Turn envelope。这是 worker 规划，
不是新增 owner 任务选择器或能力设置，故不改前端/飞书的配置 schema 和控件。
现有交互合同提供选择与恢复命令；真实 CLI 回归同时验证 Turn envelope 的
执行/写回边界。这些测试不冒充浏览器交互或飞书投递验收。

Acceptance covers initial periodic replan, preference read before choice,
deferred recovery, retained/default mismatch, exact identity conflicts,
semantic-delta rejection and writeback followed by exactly-once spend. An
installed-CLI synthetic baseline must demonstrate the original early-binding
failure before the source qualification is treated as a repair. Source/PR
qualification does not establish installation or original-consumer adoption.

验收覆盖首次周期重规划、选择前偏好读取、延后选择恢复、真实选择与推荐不一致、
确切身份冲突、缺少语义增量的拒绝，以及写回后的单次扣额。必须用已安装 CLI
的隔离合成场景证明旧的提前绑定失败，才将源码验证视为修复。源码/PR 验收
不代表已经安装或原请求方已经采用。
