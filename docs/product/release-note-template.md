<!--
Copy this file into the draft GitHub release body. Replace every angle-bracket
placeholder, remove instructional comments, and omit empty detailed groups.

Order rule: the scan-first `## At a Glance` section comes first (headline,
upgrade, highlights with usage posture, replan fixes, contributors). The
detailed groups and the bilingual Release Decision come after, so a reader who
only wants "what changed and how to use it" never has to scroll.

The headings, decision fields, usage fields, and no-change declarations below
are asserted by `examples/release/release-readiness-doc-smoke.py`; do not
rename or remove them.
-->

# LoopX vX.Y.Z

## At a Glance

**Illustrated release guide:** <Verified companion Feishu document URL, owned
by the maintainer's personal user account.>

<One outcome-led headline for the release, e.g. "更安全、更会收口、更容易被
运营看见" / "Security hardening, semantic replan closeout, and a clearer
operator surface." Keep this readable in 15 seconds.>

### Upgrade

<Existing installs upgrade explicitly; fresh installs use the bootstrap
command. Full commands live in `## Install / Update` below.>

```bash
loopx update check         # 只读检查安装 owner 与新版本
loopx update apply         # 由当前安装 owner 升级到 vX.Y.Z
loopx --version && loopx doctor
```

### Highlights

<3-6 bullets. Each bullet = one user-visible capability + one line of usage
posture (with an inline command when useful) + direct PR links. This is the
copy-ready "what's new and how to use it" list.>

- <Capability A: one-line outcome. Usage posture: <short command or behavior>.
  (#PR, #PR)>
- <Capability B: one-line outcome. Usage posture: <short command or behavior>.
  (#PR, #PR)>

### Replan Fixes

<Bullets naming the concrete replan bugs this release fixes, each with a
one-line before/after and a direct PR link. Use this section only when the tag
range contains replan changes.>

- <Before -> after. (#PR)>

### Contributors

<@-mention every eligible community contributor from the tag range with their
concrete feature and PRs, exactly like the detailed `## Community Contributors`
section. If there is no community contribution, write: 无社区贡献（本版本全部
提交由维护者完成）/ "No community contribution in this release range." Do not
list or thank the founder in this section.>

## Release Decision

**Who should upgrade:** <Name the affected users or operators and say who can
remain on the current version. Do not write "everyone" without a reason.>

**What this release solves:** <State the concrete failure, missing workflow, or
reliability gap addressed by this release.>

**Breaking changes:** <Start with "No." or "Yes." If yes, give the migration
path. If no, still name any changed default, deprecated path, or experimental
surface that existing users should notice.>

**How to verify:** <State the expected post-upgrade result, then provide the
smallest commands that prove package identity and the affected behavior.>

**Contributors:** <Name the release maintainer and every community contributor
from the tag range, or explicitly state that there were no community
contributions in this release. Link the detailed section when present.>

```bash
loopx --version
loopx doctor
<focused-command-that-proves-the-affected-behavior>
```

## State Kernel & Control Plane

<Detailed: user-visible state, todo, quota, scheduler, gate, peer-routing, or
authority change with direct PR links.>

## Capabilities & Workflows

<Detailed: shipped user outcome, shipped layer, and any last-mile boundary with
direct PR links.>

## Quality & Testing

<Detailed: durable regression coverage, canary, qualification, or release-gate
change with direct PR links.>

## Benchmarks & Integrations

<Detailed: host, provider, benchmark, or external-boundary change with direct PR
links. State explicitly when no benchmark or long-horizon outcome claim is
made.>

## Documentation & Compatibility

<Detailed: documentation, migration, default, deprecation, or compatibility
detail with direct PR links. Repeat the persisted-state migration decision
explicitly.>

## Licensing

<Include this group when the release changes licensing. For `v0.4.8`, state
that the unified open source core moves to Apache-2.0, releases through
`v0.4.7` remain MIT, the historical notice is retained, Apache-2.0 continues
to permit commercial use, and the explicit patent framework supports future
enterprise and ecosystem collaboration without manufacturing retroactive
patent grants from historical MIT contributors. Link `docs/project/licensing.md`.>

<!--
Include this section only when the tag range contains eligible contributors
other than @huangruiteng. Keep founder stewardship out of this community-only
section; it is already named in Release Decision when relevant.
-->

## Community Contributors

<Detailed: link each eligible GitHub handle and PR, and name the concrete
contribution. Call out external or first-time contributors when applicable.
This section mirrors the `At a Glance > Contributors` list in full detail.>

## Optional Capability Activation & Use

No new optional capability activation is introduced in this release.

<!--
If the release adds or materially changes an experimental, default-off, or
opt-in surface, replace the no-change declaration with one entry per surface:

### <Surface Name>

**Activation:** <Exact install, enable, command, or profile opt-in.>

**Validation:** <Minimum runnable readback or verification command.>

**Disable / rollback:** <Exact disable, uninstall, envelope removal, or rollback.>

**Authority boundary:** <Writes, merges, providers, privacy, or host powers not granted.>

**Docs:** https://github.com/huangruiteng/loopx/blob/vX.Y.Z/<canonical-doc>

```bash
<activation-command>
<validation-command>
<disable-or-rollback-command>
```
-->

## Install / Update

New PyPI users and existing PyPI users use the same package-native path:

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx slash-commands --install
loopx doctor
```

All existing installs use explicit update intent; the command preserves the
active pip, pipx, or archive owner:

```bash
loopx update check
loopx update plan
loopx update apply
loopx doctor
```

## 中文摘要

**图文发布指南：**<维护者个人飞书账号名下、已核验内容与图片的配套文档链接。>

<把 `At a Glance` 的标题、升级、Highlights、Replan 修复与贡献者以中文镜像到
本段开头，再进入详细分组。可以更短，但不能弱化。>

### 升级决策

**谁需要升级：**<写明受影响的用户或 operator，以及谁可以暂不升级。>

**解决了什么：**<用结果语言说明本版本解决的故障、缺口或可靠性问题。>

**是否有破坏性变更：**<以“无。”或“有。”开头；若有则给出迁移路径，
若无也要说明默认值、废弃路径或实验能力的变化。>

**如何验证：**<指向上方最小验证命令，并写明升级后的预期结果。>

**贡献者：**<用 GitHub handle 列出 release maintainer 与 tag range 内的
社区贡献者；若没有社区贡献者则明确说明。>

<!-- 保留与英文相同的非空产品分组、PR 归因和边界，可以更短但不能弱化。 -->

### 状态内核与控制面

<中文摘要与 PR 链接。>

### 能力与工作流

<中文摘要与 PR 链接。>

### 质量与测试

<中文摘要与 PR 链接。>

### 基准与集成

<中文摘要与 PR 链接。>

### 文档与兼容性

<中文摘要与 PR 链接。>

### 许可证

<当版本改变许可证时镜像英文 Licensing。`v0.4.8` 必须明确：统一开源 core
切换为 Apache-2.0；`v0.4.7` 及更早版本永久保持 MIT；保留历史 notice；
Apache-2.0 不限制商业使用；显式专利框架服务于未来企业与生态协作，但不会让
历史 MIT 贡献凭空产生追溯性的完整 Apache 专利授权。链接
`docs/project/licensing.md`。>

<!-- 英文存在 Community Contributors 时，保留同一人员、PR 和贡献范围。 -->

### 社区贡献者

<社区贡献者中文归因。>

### 可选能力启用与使用

本版本未新增可选能力启用入口。

<!--
若英文存在 optional capability 条目，以 #### <Surface Name> 逐项镜像，并使用：
**启用：**、**验证：**、**停用 / 回退：**、**权限边界：**、**文档：**，
同时保留可运行的 bash 命令块。
-->

### 发布验证

- Package version and public tag: `X.Y.Z` / `vX.Y.Z`.
- Tag target: `<full-commit-sha>`.
- <Exact-commit checks that passed, including failures or skips without
  overclaiming hosted, live-model, benchmark, or long-horizon evidence.>

Compare: https://github.com/huangruiteng/loopx/compare/vPREVIOUS...vX.Y.Z
