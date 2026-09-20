# LoopX Developer Book

从控制面协议到可交付开发。

面向外部开发者的双语 Dev Book：先理解 LoopX 的状态、权限与 Turn，再选择接入现有项目或参与
开发者贡献。

[English edition](/loopx/docs/book/en/)

<div class="grid cards" markdown>

-   :material-map-marker-path: **控制面基础**

    从 Session、Goal 和状态投影出发，理解工作图、权限、Turn、恢复与运行边界。

    [:octicons-arrow-right-24: 从基础开始](chapters/01-from-session-to-loop.md)

-   :material-source-repository: **项目接入**

    把接入任务交给 Agent，验收 Goal、identity 与 Git 边界，再从 Codex App 或 Codex CLI 启动。

    [:octicons-arrow-right-24: 接入现有项目](chapters/05-connect-existing-project.md)

-   :material-source-branch: **开发者贡献**

    按协议和 owner 选择 Control Plane、Capability、Provider、Host、Projection 或 Extension 等贡献路径。

    [:octicons-arrow-right-24: 参与开发者贡献](chapters/source-protocol-map.md)

-   :material-school: **Control-Plane Course**

    面向准备进入 Kernel、CLI、状态投影或调度实现的开发者，提供 Showcase、decision table 与源码领读。

    [:octicons-arrow-right-24: 进入开发者课程](chapters/12-control-plane-course.md)

</div>

## 这本书解决什么问题

普通 Agent 会话擅长完成一次上下文内的推理与执行，但真实开发工作会经历中断、压缩、
交接、等待和外部状态变化。LoopX 把这些过程中的目标、工作队列、权限、证据和恢复条件
放进项目拥有的控制面。

本书不复制 LoopX CLI reference，也不要求读者成为 Kernel 核心贡献者。它提供一条稳定的
学习路径，让外部开发者能够判断：

- 当前任务只需要一次普通会话，还是需要持久 Goal；
- 什么时候需要 LoopX 的项目级 Todo、Gate、Evidence、Quota 与恢复合同；
- 如何让 Agent 安全接入自己的项目，并验收它没有越过 Goal、identity、权限与 Git 边界；
- 如何从协议与不变量出发定位贡献 owner、修改实现并组织验证；
- 如何判断一项能力应进入 Core、Capability、Provider、Host、Projection，还是作为 Extension
  独立交付。

## 三条并列学习主线

完成第一部分后，可以按需求选择：

1. **接入现有项目：** 从[连接你的 Git 项目](./chapters/05-connect-existing-project.md)开始；
2. **开发者贡献：** 从[开发者贡献地图与协议入口](./chapters/source-protocol-map.md)开始。
3. **Control-Plane Course：** 准备深入 Kernel 实现时，从
   [独立课程章节](./chapters/12-control-plane-course.md)开始。

三条主线共享同一套控制面基础，但互不依赖。项目接入不要求修改 LoopX；开发者贡献也不只属于
Kernel 核心维护者；课程则把 Dev Book 的机制模型进一步推进到实现、规则与验证。你可以沿 Control Plane、Capability、Provider、Host/Runner、
Projection/Docs/fixtures 或 Extension/package lifecycle 中的一条边界完成贡献，其中
Extension 只是可独立版本化和交付的一种路径。

## 当前验证基线

- 正文格式：Markdown；
- 站点生成器：MkDocs Material；
- 在线发布：GitHub Pages；
- LoopX 发布锚点：`v1.1.0`；
- 运行时前提：Python 3.11+ 与 Node.js 22.18.0+。

协议解释以 LoopX 官方公开合同为事实源。易变化的命令仍以对应发布物、官方文档和当前
`--help` 为准；本书负责教学顺序与心智模型，不成为另一份完整命令参考。

`v0.5.4` 已把 Effect Program、Turn settlement、Todo completion、quota delivery routing、
workspace causality 与 scheduler state 等关键语义放到 typed TypeScript owner 后面。Python CLI
仍负责迁移期 transport、兼容投影、外部 Provider 调用和部分持久化写回；这不是两套可独立演进的
控制面。当前 `main` 的后续迁移阶段以
[TypeScript Control-Plane Migration RFC](/loopx/docs/architecture/rfcs/typescript-control-plane-migration-v0/)
为准，不应倒推为 `v0.5.4` 已发布行为。
