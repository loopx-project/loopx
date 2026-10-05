# 实验性 CLI 前置切片；M1 仍未完成

[实现 PR #5385](https://github.com/loopx-project/loopx/pull/5385)，提交
`ad478f4753b5cfeaa5ee5248fceb8a06c8c28c2b`，新增默认关闭的 TypeScript/Node
源码 CLI：选定飞书文本 → 语义提议 → 逐项摘要审阅 → 规范 User Todo 创建/修改
→ 私有 CLI 读回。对应 [RFC PR #5384](https://github.com/loopx-project/loopx/pull/5384)。
本次记录时，两者均为未合并提案。

实现复用规范 writer/provider/receipt 和 registry witness owner。仅接入已有私有
存储，以及项目本地 object registry 中显式 active、精确实例、无注册 Agent 的
Goal；不支持的配置直接拒绝。来源版本、配置和 registry 变更使旧审阅失效。
批次后续提议可刷新状态，无需再次调用模型。截止时间保存为审阅及 Todo note
元数据，不安装提醒或完成效果。

实现提交的 91 项定向 Node 测试通过，包含九项功能用例和真实 CLI/HTTP/隔离
文件存储流程，PATH 中无 Python。飞书与模型响应由测试替身提供。全量测试
3,530 通过、21 失败、31 跳过；未修改的 `f49b4a0` 复现相同 21 项失败。最终
修改重跑了定向验证。类型检查与基线相同，保留 53 项错误。不能据此宣称全库
检查通过。

M1 仍为**部分实现、尚未验收**。桌面配置与返回、持久提议生命周期、本人身份
核验、真实飞书/模型兼容性、语义评估和安装版读回仍待实现或验证。CLI 私有文件
只用于临时审阅交接，不替代持久提议服务。下一归属为 personal-workspace/
work-items 与飞书扩展，需要接入既有类型化对话契约并完成 M1 用户流程。
M2/M3 验收保持不变。未读取真实账号数据或发送消息。

checkout 没有可用运行态 Todo registry；本记录关联代码证据及既有 RFC 后继
工作，不宣称已领取运行中的任务。
