# Workspace status access failures

**状态：已实现并完成聚焦验证。** 关联设计记录：[PR #4222](https://github.com/huangruiteng/loopx/pull/4222)。
本修复只区分逐 Goal 状态读取中的权限拒绝，不修复操作系统权限，也不扩展故障分类体系。

## 动机、归属与范围

目录可以正常响应时，一个 Goal 的状态依赖仍可能因文件权限失败。此前所有 HTTP 5xx
都显示“本地服务暂时不可用”，既误导远程状态来源的排查，也会自动重试确定的权限拒绝。

链路：`GET /status.json?goal_id=...` → `ChatStatusRequestMixin._status`
→ `collect_status` → 状态依赖读取或 Effect runtime → 错误响应 → 逐 Goal 提示。

归属仍是现有 Dashboard 状态读取与展示边界：
[chat/status_api.py](../../../loopx/chat/status_api.py)、
[workspace-progressive-status.ts](../../../apps/presentation/dashboard/src/data/workspace-progressive-status.ts)
和 [i18n.tsx](../../../apps/presentation/dashboard/src/features/personal-workspace/i18n.tsx)。
复用 [Effect runtime](../../../loopx/control_plane/effect_runtime.py) 的类型及诊断码；
没有新增 capability、provider、扩展、通用错误框架或第二套运行时规则。
前瞻整理仅是该 API 边界内的私有分类函数，不顺带重构状态投影或重试机制。

## 错误契约与兼容性

只有查询校验通过、明确携带单个 `goal_id` 且没有 `view` 的请求适用新分类。
HTTP 仍为 **500**；固定公开错误文案不变，仅增加：

```json
{"ok":false,"error":"LoopX status could not be projected for the workspace.","error_code":"workspace_status_access_denied"}
```

不返回异常文本、堆栈、账户、绝对路径或令牌。分类不匹配错误消息关键词：

- 本地异常识别 `PermissionError` 或 `EACCES` / `EPERM`。
  Windows 存在非空 `winerror` 时优先用原生值：仅整数 5/65 视为权限拒绝。
  19（写保护）、32/33（共享/锁冲突）及非法值保持普通失败，不借用历史权限 cause。
- 远端结构化异常必须同时是 `EffectRuntimePermanentIOError` 和
  `diagnostic_code == "io_permission_denied"`。其他远端类型或诊断码直接停止分类，
  不把所有永久 I/O 都视为权限不足。
- 异常链最多检查 8 个节点，按对象身份去环；显式 `__cause__` 优先。
  仅 `EffectRuntimeStartupError` 的 `runtime_launch_failed` /
  `runtime_request_failed` 允许沿未抑制的隐式 `__context__` 查找权限原因。
  普通历史 context、`raise ... from None` 不构成权限证据。

前端只在 HTTP >= 500 时识别该结构化错误码。错误体按实际字节最多读取 16 KiB，
不信任 Content-Length；复用单次请求的 30 秒超时。读完、超限或取消后释放读取器。
HTML、旧版、未知码、空体及读取/JSON 解析失败仍归为 `service`；
超时维持 `timeout`，切换来源或取消批次不得产生迟到结果。

| 前端类别 | 行为 |
| --- | --- |
| `access`（新增） | 提示检查状态来源运行账户的文件/目录访问权限；停止浏览器自动重试，保留手动重试 |
| `service` | 其他 5xx；中性提示“Goal 状态读取失败”，仍最多尝试 3 次 |
| `timeout` / `network` | 原有超时/连接失败分类及最多 3 次尝试不变 |
| `revision` | HTTP 409 或修订不一致；原有目录重新同步行为不变 |
| `scope` | 其他非成功响应（包括 401/403）或 Goal 集合不匹配；不等同于文件权限 |
| `invalid` | 成功响应解析或校验失败等原有兜底不变 |

**行为变化只有已识别权限拒绝的浏览器自动重试被停止**，以及上述中英文诊断文案。
Effect runtime 自身重试、全量状态兼容入口、目录入口、HTTP 200 健康诊断、
Chat 操作、审批、状态机、Goal 注册信息和文件权限均不改动。
不自动提权、重启或修改目录所有权。来源及目录修订不变时，手动重试失败 Goal
仍保留已成功的同伴快照；来源或修订变化继续服从原有隔离栅栏。

## 仍未细分的故障

`service` 仍可包含注册数据解析、依赖/安装、运行时启动、通信/协议、非权限结构化
I/O、未知投影异常及上游 5xx。这不是封闭的运行时枚举，也不是每项均实机复现。
依据见 [registry.py](../../../loopx/registry.py) 与
[collection.py](../../../loopx/control_plane/status/collection.py)。
仅当异常最终形成逐 Goal 请求的 5xx 才进入此提示；内部已处理的健康诊断不在本范围。

## 验证与回退

可重复的聚焦验证：

- `python -m pytest tests/test_chat_server_cors.py -q`：真实 HTTP handler 下的分类、
  脱敏、因果链边界及目录/全量入口不变；分类故障由测试注入，不能替代真实 ACL。
- Dashboard 目录下 `npm run smoke:workspace-progressive-loader`：直接测试生产 loader，
  覆盖错误体上限、未知/损坏响应、超时、取消、成功响应校验及 HTTP 类别优先级。
- `npm run build` 后 `npm run smoke:workspace-progressive`：packaged chat 浏览器验证，
  覆盖中英文、桌面/移动端、失败隔离、修订栅栏、权限请求次数和手动恢复。
  此 smoke 的 HTTP 响应受控，不冒充真实后端 E2E。
  需要 Playwright 浏览器；可用 `LOOPX_PYTHON_BIN` 指定服务静态资源的 Python。

另已在可丢弃的合成工作区中通过真实 `serve_chat` 和 Windows ACL 验证：
只拒绝一个 Goal 的运行历史文件读取，目录及同伴 Goal 仍为 200；
受影响 Goal 为固定脱敏 500/专用码，浏览器只请求一次；
恢复 ACL 后手动重试得到 200，成功同伴不重复加载。原 ACL 已恢复。
临时脚本、路径、截图和运行证据留在忽略目录，不进入公开仓库。
此结果不代表对全部文件系统、操作系统或远端提供方做了端到端验证。

回退应同时撤回后端分类、前端识别/文案和对应 packaged chat bundle；
没有持久数据或权限迁移。旧版前端仍把新增码当普通 5xx，旧版后端仍可由新版前端兜底。
