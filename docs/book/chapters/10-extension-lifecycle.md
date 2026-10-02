# 生命周期与 managed runtime

package 安装与 LoopX activation 是两个独立阶段。LoopX 不负责下载任意 package，也不允许 caller
传入任意 executable；它管理一个经过 doctor、绑定 revision 的 provider lifecycle。

## 升级成功，为什么仍要检查调用方

假设 provider 的新版本把 `result` 从对象改成字符串。命令 `loopx extension upgrade --manifest standalone-extension/extension.toml --execute` 的 readiness probe 即使通过，也不能证明每个消费者都理解了新结果。

升级前需要验证新旧 request/response 的兼容边界，保留历史 receipt 的读取方式。同时区分 Python environment 中的 package 与 LoopX 记录的 active revision；二者不是一个原子提交。

如果新 package 已替换旧文件，activation probe 失败只能保持旧的 activation 记录，不能把磁盘文件自动还原。恢复可用性需要先恢复匹配 package，再按生命周期读回。

## 1. 安装 Python package 与激活的分界

继续使用[上一章](09-extension-scaffold.md)已安装 LoopX 与 provider 的环境，从 `standalone-extension` 的父目录执行：

```bash
. .local/book-extension-venv/bin/activate
extension_state_dir="$(mktemp -d)"
extension_state="$extension_state_dir/state.json"
```

本章所有 lifecycle 命令都使用这个独立 state file。不要在后续小节重新创建它；只在开始一次全新练习时创建新目录。

上一章的 package 安装已让 `loopx-text-stats` 出现在该环境中，尚未进行 LoopX activation。首次跳读到这里时，先完成上一章的环境、实现和测试步骤。

直接检查 provider 自身：

```bash
loopx-text-stats --doctor
loopx-text-stats < standalone-extension/examples/request.json
```

直接执行适合开发调试。面向用户的支持路径是 `loopx extension`，理由在错误那一节：直接跑
entrypoint 会绕过 managed runtime 固定的 timeout、input limit 与 output limit。

## 2. 预览并安装 Extension

先预览：

```bash
loopx extension install --state-file "$extension_state" \
  --manifest standalone-extension/extension.toml \
  --format json
```

再执行：

```bash
loopx extension install --state-file "$extension_state" \
  --manifest standalone-extension/extension.toml \
  --execute \
  --format json
```

`--manifest` 与 `--bundled` 是互斥的必需项：随 wheel 发布的 provider 用 `--bundled <id>` 选取，
独立发行的 provider 用 `--manifest <path>` 指向自己的 manifest。

install 会：

1. 读取 declarative manifest；
2. 检查 API compatibility 与 permissions；
3. 解析已安装 entrypoint；
4. 运行只读 doctor；
5. 记录 validated manifest snapshot 与 revision；
6. 激活该 revision 并置 `enabled`。

它不会：

- 从网络下载 package；
- 运行任意 caller executable；
- 授予新权限；
- 把 provider output 存进 activation state；
- 读取你的项目 Goal。

这里需要精确区分两件事。"默认关闭"描述的是**声明**：一份 manifest 被读进 catalog 时报告
`declared=true, installed=false, enabled=false, ready=false`，它不执行任何东西。而一条通过
doctor 的 `install --execute` 会在同一次写入里把 `enabled` 置为 `true`。所以谨慎的说法是：
**在没有显式 `--execute` 之前，一切都不生效。**

install 并不幂等：重复安装同一个 id 会以 "already installed" 失败，把同一个 revision 再作为
upgrade 提交会以 "revision is already active" 失败。

## 3. 查看与复查 readiness

```bash
loopx extension list --format json --state-file "$extension_state"
loopx extension doctor loopx-text-stats --execute --format json --state-file "$extension_state"
```

`doctor --execute` 会真实运行 probe。readiness 绑定 active manifest revision 与 resolved runtime
identity，identity 覆盖 entrypoint 文件内容、所选 Python interpreter、`python_module` 的来源，
以及 extension 自己声明的 view validator。任何一项变化都会让旧 doctor proof 失效；LoopX 自身的
内置 validator 被刻意排除，因此升级 LoopX 不会连坐所有 extension。

doctor 的状态是一条阶梯，而不是布尔值：

```text
entrypoint_missing          -> identity 无法解析
doctor_not_configured       -> doctor_args 为空
probe_required              -> 可解析，但没有 --execute
provider_unavailable        -> probe 非零退出
entrypoint_changed_during_probe -> probe 前后 identity 不一致
ready                       -> 可用
```

失败的 doctor 会清除 stale readiness，但不会自动切换 revision。它等待环境修复和新的 probe。

## 4. 通过 managed runtime 调用

先预览：

```bash
loopx extension run loopx-text-stats --state-file "$extension_state" \
  --input-json standalone-extension/examples/request.json \
  --format json
```

执行：

```bash
loopx extension run loopx-text-stats --state-file "$extension_state" \
  --input-json standalone-extension/examples/request.json \
  --execute \
  --format json
```

managed runtime 固定：

- extension id 与 active revision；
- entrypoint 与 args；
- stdin/stdout JSON protocol；
- timeout，取自 manifest 的 `timeout_seconds`（1 到 120）；
- permissions；
- request input limit；
- stdout/stderr limit。

caller 不能附加 shell args 或替换 executable。超时或输出超限会终止 provider 的整个 process
group，避免子进程在 LoopX 报告停止后继续运行。

`run` 只支持：

- enabled；
- doctor-ready；
- 有 runtime；
- 没有 `[[provides]]` / `[[implements]]`；
- 未声明任何 permission；
- request 满足 bounded protocol；
- caller 显式 `--execute`。

任何不满足条件的 Extension 都应 fail closed。

## 5. Disable 与 enable

```bash
loopx extension disable loopx-text-stats --execute --format json --state-file "$extension_state"
```

disabled Extension 仍可在 lifecycle state 中观察，但不是 dispatch candidate。此时 `extension run`
应失败。

重新启用：

```bash
loopx extension enable loopx-text-stats --execute --format json --state-file "$extension_state"
```

enable 不会信任旧 readiness；它会重新运行 doctor，成功后才设置 enabled bit。失败时它保持
disabled，并撤销失败 artifact 的 doctor proof，避免留下"已启用且看似 ready"的假状态。

## 6. Upgrade 与 rollback

升级前先修改 package 与 manifest version，并把新 package 安装到同一 environment。然后预览：

```bash
loopx extension upgrade --state-file "$extension_state" \
  --manifest standalone-extension/extension.toml \
  --format json
```

执行：

```bash
loopx extension upgrade --state-file "$extension_state" \
  --manifest standalone-extension/extension.toml \
  --execute \
  --format json
```

upgrade 在切换 active revision 前验证并 probe 新 manifest。失败 probe 保留当前 activation revision，但不回滚已经替换的 package。旧 entrypoint identity 改变时，旧 readiness 也可能失效，需恢复匹配环境并重新检查。

回滚：

```bash
loopx extension rollback loopx-text-stats --execute --format json --state-file "$extension_state"
```

rollback 先按 previous revision 记录的 manifest 与入口位置 probe **当前安装的** entrypoint，通过后再
切换。它不是任意 Git checkout 回退，而是 activation state 中已验证 revision 的生命周期转换。probe
通过不证明旧版本 package 已经装回：若环境里仍是新 package，rollback 后 LoopX 记录的是旧 revision，
实际运行的却是新代码。回滚前后应安装与目标 revision 匹配的 package，再用 `run` 读回实际行为。activation state 保留最近的 validated revision 快照，
数量有限（当前保留 5 个）。

`rollback_available=false` 表示没有记录可回滚的 revision，例如首次安装后。它不能诊断环境是否损坏。

如果存在 rollback target 但其 doctor 失败，先检查或重新安装与目标匹配的 package，再执行 rollback 和 doctor；doctor 通过时也要确认已安装的是匹配版本。若没有历史 target，需要按明确选定的版本准备新的安装/升级方案；修复环境本身不会生成回滚历史。

## 7. 检查练习状态并清理

再次运行 `loopx extension list --state-file "$extension_state" --format json`，确认 active revision、enabled 和 doctor 状态与预期一致。停止练习时可以 disable，并退出虚拟环境：

```bash
loopx extension disable loopx-text-stats --execute --format json --state-file "$extension_state"
deactivate
```

临时目录可能包含本机 runtime identity，应保留在本地。确认不再需要回滚证据后，由你删除本次练习目录；不要删除默认用户 runtime state。

## 何时不能使用 standalone `run`

以下需求必须进入 Capability 或 domain command：

- 读写文件；
- 访问需要授权的 API；
- 发送消息；
- 发布内容；
- 管理外部资源；
- 修改项目状态；
- 需要 action/scope authority 的任何 effect。

effectful dispatch 由 Capability 在 domain policy 检查后创建 request-bound execution envelope，
它绑定四个字段：

- `action`；
- `scope`（有大小上限）；
- `extension`（id 与 active revision）；
- `request_digest`（对去掉 envelope 之后的裸 request 求摘要）。

envelope 不是 service credential，也不替代外部系统自己的 authorization。caller 自带 envelope、
scope 变宽、request 改变或 revision 不匹配都必须 fail closed。

## 代价与边界

生命周期把"扩展是否生效"变成可审计的状态，代价在于它增厚了运维面。

**代价一：升级是一个两半的动作。** LoopX 管 revision，package manager 管 environment，两者要靠
人同步。升级前先把新 package 装进同一 environment，否则 probe 探测的还是旧文件。

**代价二：managed runtime 限制了 provider 的自由度。** 不能换 executable、不能加 args、timeout
与输出都有上限。需要长时运行的 provider 不适合这条路径。

**代价三：revision 历史是有界的。** activation state 只保留有限个 snapshot，久远版本无法回滚。

**边界一：managed runtime 不替你做 sandbox。** Extensions 是 trusted executable code，权限声明
不会把它变成操作系统级别的隔离。

**边界二：LoopX 不负责分发。** 它不下载、不 build、不安装 package，也不创建凭据或启动服务。

**边界三：readiness 与 authorization 是两件事。** doctor 通过只说明当前 revision 可以被调用，
它不赋予任何 effect 权限。

## 具名失败：这些约束拦住了什么

**有权限的 Extension 在调用前就被拒绝。** `test_extension_run_rejects_any_declared_permission_before_invocation`
参数化遍历多种 permission 字符串，断言错误信息匹配 "standalone extension run grants no"，并断言
标记文件从未被写出：拒绝发生在 provider 启动之前，而非之后。

**超时会终止整个进程组。** `test_extension_run_terminates_provider_on_timeout` 断言
`failure_kind == "timeout"`、`exit_code is None`，并且孙进程的标记文件在 kill 之后不存在。

**失败 upgrade 与失败 enable 的写入不同。** `test_failed_upgrade_keeps_the_active_revision` 验证 upgrade 的新版本 probe 失败时保留原 activation revision/history；该失败在修改 activation state 之前发生，不清除原 proof 字段。

`test_failed_enable_remains_disabled_and_rejects_current_artifact` 验证失败 enable 保持 disabled，并撤销当前 artifact 的 doctor proof。批量 doctor 的失败与 readiness 清理另由 `test_enabled_extension_doctor_batch_keeps_failed_provider_closed` 覆盖。

对应测试：`tests/extensions/test_extension_runtime.py`。

## 故障定位

| 症状 | 优先检查 |
| --- | --- |
| `entrypoint_missing` | package 是否安装在运行 `loopx` 的同一 environment |
| install preview 成功但 list 没变化 | 是否遗漏 `--execute` |
| "already installed" | 该 id 是否已经安装过；install 不做幂等合并 |
| doctor stale | executable、interpreter、module source 或 view validator 是否变化 |
| run 报 disabled | 运行 `enable --execute` 并查看 doctor |
| run 拒绝 permissions | 该 Provider 是否应进入 Capability/domain command |
| upgrade 未切换 | 新 revision doctor 是否失败，或 revision 是否已经 active |
| rollback 不可用 | 是否记录了 previous revision（首次安装后没有） |

生命周期失败时修复 contract 或环境，不要绕过 managed runtime 直接把 provider 当作已激活。

## 不变式

1. **package 安装与激活是两个阶段。** pip 成功不改变 activation state，install 成功也不安装
   package。
2. **没有显式 `--execute` 就不生效。** 预览、声明和 doctor 都只读；只有 `--execute` 写状态。
3. **readiness 绑定 revision 与 runtime identity。** executable 或 interpreter 一换，旧 proof
   立刻失效。
4. **失败的 activation probe 保留原 revision 记录。** 它不恢复被 package manager 改动的文件，不能独自保证旧程序可用。
5. **standalone `run` 只服务零权限 provider。** 一旦声明 permission，正确路径是 Capability 或
   domain command。
6. **rollback 切换的是记录中的 revision，而非磁盘上的 package。** 恢复可用性需要人把环境对齐到
   那个 revision。

本章把 Extension 从"能跑"变成"可审计"。下一章换一个方向：当一次改动要进入 LoopX core 本身，
判断标准与责任划分如何变化。
