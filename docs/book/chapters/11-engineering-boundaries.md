# 验证、兼容与安全

一个 Dev Book 只有在读者能复现、能判断成功，并且不会被引导跨过权限边界时才算完成。本章收束
项目接入与开发者贡献的验证策略。Extension 采用独立 lifecycle 检查，但仍属于开发者贡献的
一种交付路径。

## 本章目标

读完后，你应该能：

- 为项目接入和不同类型的开发者贡献选择合适的验证层；
- 区分版本兼容、readiness 与业务正确性；
- 在公开提交前检查 private state；
- 知道哪些内容应该留在本书、官方文档或项目事实源。

## 四层验证

### 1. Artifact validation

确认文件和 schema 自洽：

- Markdown 可以构建；
- 内部链接存在；
- JSON 与 TOML 可解析；
- request/response 满足 JSON Schema；
- fixture 可以从头创建。

主书：

```bash
python3 -m pip install -r docs/requirements-docs.txt
python3 examples/dev-book-publication-smoke.py
mkdocs build --strict
```

当前站点由 LoopX monorepo 的 MkDocs Material 发布链路构建。依赖范围以
`docs/requirements-docs.txt` 为准；Book 导航、双语路由、官方首页入口和 Labs 排除边界由
`examples/dev-book-publication-smoke.py` 守护。依赖变更后运行：

```bash
python3 -m pip check
mkdocs build --strict
```

不要只验证 Markdown 能被单独解析；还要验证它在 LoopX 的统一 `mkdocs.yaml` 导航、GitHub
Pages base path 和首页 Learn 路径中可发现。

书内 standalone Extension 示例：

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e './standalone-extension[test]'
python3 -m pytest standalone-extension
```

### 2. Product-surface validation

确认教程使用的是发布物真实表面：

```bash
loopx --version
loopx doctor
loopx doctor --deep
loopx start-goal --help
loopx capability list --format json
loopx extension --help
```

命令存在不代表完整流程已验证。Host automation、visible Goal 和 Extension activation 需要各自的
readback；`doctor --deep` 还会启动并探测发布物携带的 TypeScript Effect runtime。迁移中的
Python facade 成功返回，也不单独证明 TypeScript semantic owner、runtime decoder 和 durable
effect path 都已通过。

### 3. Lifecycle validation

项目接入至少验证：

- reconnect 复用已有状态；
- status 能找到 active Goal；
- local state 被 Git 忽略；
- Host activation 可观察；
- quota 与 selected Todo 一致。

Extension/package lifecycle 贡献至少验证：

- package entrypoint 可解析；
- doctor 成功且无 effect；
- install 生成 revision-bound state；
- disable 后不能 run；
- enable 重新 doctor；
- invalid request fail closed；
- upgrade 失败不破坏当前 revision。

Control Plane、Capability、Provider、Host/Runner 或 Projection 贡献至少验证：

- 预期决策来自独立审阅的 invariant，不来自当前实现输出；
- unit/contract test 覆盖正例、反例与非法状态；
- focused smoke 或 public-safe replay 经过真实协议链；
- agent-facing output、scheduler 或 writeback 等受影响 consumer 得到对应检查；
- Capability 有真实 caller、outcome contract 和 Domain State owner；
- Provider 只返回 bounded observation/effect/readback，不获得 Goal authority；
- Host/Runner 保持 typed request/result、独立 validation 与真实 runtime readback；
- Projection/Dashboard 只消费 typed public-safe read model，不创建 browser write authority；
- Docs/fixtures 绑定公开 contract 和维护触发器，不复制 private runtime state；
- `loopx canary premerge --from-git-diff` 或等价风险集合覆盖跨 surface 变化；
- PR 只包含同一协议结果所需的 product、docs 与 durable validation。

### 4. Outcome validation

最后检查读者目标，而不只是命令退出码：

- 项目接入后，Agent 是否真的从同一 canonical state 恢复？
- Control Plane 或 Capability 改动是否保持 authority、precedence、replay 与 recovery invariant？
- Provider/Host 是否通过真实 readback 和 independent validator 证明结果？
- Projection、Docs 与 fixtures 是否仍指回同一事实源？
- Extension 是否返回稳定、正确的 domain result？
- 有权限的动作是否被拒绝或正确路由？
- 文档是否让读者知道失败后怎么恢复？

## 兼容性不是一个版本号

Extension compatibility 至少有四层：

| 层 | 示例 |
| --- | --- |
| package | Python version、dependency range |
| LoopX API | `requires_loopx_api = ">=1,<2"` |
| wire protocol | `loopx_text_stats_extension_v0` |
| domain schema | request/response schema version |

升级 package version 不应静默改变同一 schema 的含义。破坏性 wire contract 应使用新 protocol 或
schema version，并为 caller 提供迁移路径。

## Public/private boundary scan

公开提交前检查：

```bash
git status --short
git diff --name-only
git ls-files --others --exclude-standard

loopx check \
  --scan-path README.md \
  --scan-path chapters/
```

如果你按照书内示例生成了可运行目录，也要扫描这些路径：

```bash
loopx check \
  --scan-path README.md \
  --scan-path standalone-extension/
```

人工复查以下内容：

- credentials、token、cookie；
- 本机绝对路径；
- `.loopx/`、`.loopx/goals/` 或 runtime state；
- raw Agent transcript、trajectory、verifier output；
- 私有 issue、内部链接和未经脱敏的组织叙事；
- 临时探针和生成日志。

`.gitignore` 不能替代扫描。已经被跟踪的文件不会因为新增 ignore 自动消失。

## 文档的 authority 分工

| 内容 | 放置位置 |
| --- | --- |
| 学习顺序、概念解释、恢复思路与 scaffold 导读 | `loopx-book` |
| 完整 CLI 参数、协议与 release behavior | LoopX 官方仓库 |
| 产品代码、durable fixture 与 smoke | 对应 LoopX 或 Extension 源码仓库 |
| 当前项目 Goal、Todo、Gate 与 evidence | 项目本地 LoopX state |
| commit、PR、CI、外部资源事实 | 对应外部系统 |

本书不复制完整 reference。高漂移命令只保留完成任务所需的最小路径，并指向 `--help` 和官方文档。

## 文档维护触发器

每次 LoopX minor release 后优先复查：

- installer 与 `doctor`；
- `connect` / `start-goal`；
- Host surface 名称；
- Codex App heartbeat、Codex CLI Goal activation 和 Runtime Connector Catalog；
- TypeScript migration RFC 的 shipped baseline、active phase 与 facade exit condition；
- core protocol、state machine、bounded-context owner 与 quality catalog；
- Extension manifest、doctor、run 与 lifecycle；
- 书内步骤能否在当前官方 scaffold 与命令表面上复现。

理论章节只在公开 contract 改变时更新。不要因为内部文件重构就重写用户心智模型。

## 发布前 checklist

### 主书

- [ ] 首页第一屏说明读者、价值和两条实践主线；
- [ ] 中文为主，代码与必要术语保留英文；
- [ ] 六章基础覆盖 Session、Goal、state、work graph、Turn、recovery 与运行边界；
- [ ] 项目接入覆盖 Codex App 与 Codex CLI；
- [ ] 开发者贡献覆盖 Control Plane、Capability、Provider、Host/Runner、Projection/Docs/fixtures；
- [ ] 贡献内容按 placement、协议、不变量和证据组织，而不是函数列表；
- [ ] Extension 作为贡献子路径，示例基于当前官方 scaffold 可复现；
- [ ] `python3 examples/dev-book-publication-smoke.py` 成功；
- [ ] `mkdocs build --strict` 成功；
- [ ] internal links 与 public boundary scan 通过；
- [ ] 首页预览已由 owner 审阅。

完成这些检查后，GitHub Pages workflow 才应从 `main` 发布站点。Pages 是展示面，不是内容或
LoopX 状态的事实源。
