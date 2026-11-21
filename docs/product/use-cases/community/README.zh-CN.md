# 在你常用的 AI 助手里试做一个任务

[English](README.md) · [社区常见问题](faq.zh-CN.md)

挑一个有失败测试的项目，让你平时用的助手先复现问题，修好后把改动和测试结果给你看。

1. **安装 LoopX，打开项目。** 按[安装说明](../../../guides/installing-loopx.md)装好
   LoopX 和[当前助手的入口](../../../guides/getting-started.md#command-skill-registration)。
   打开项目，已经接入 LoopX 的项目直接沿用现有连接。
2. **在项目对话里交代任务。** Codex App 用 `$loopx` 或技能菜单选中已安装的
   `loopx` 技能，把下面的任务一起发出去。Claude Code 则在同一条消息开头用
   安装后的 `/loopx`：

    > 检查这个项目中失败的测试。先复现失败，修复确认的问题，再跑测试验证。
    > 告诉我改了什么，保留改动供我审阅。

3. **检查改动和实际跑过的测试。** 确认原来的失败已解决。需要继续或调整时，
   在同一个对话里交代下一步，让助手接着已有工作做。

助手通过 LoopX 入口接入项目并记录工作。找不到入口时，核对技能是否装给了
当前助手，再重新打开它。看到一份设置预览，还不能确认任务已开始；
宿主无法启用续跑时，请助手告诉你具体缺哪一步，再决定如何继续。

上述入口来自 [LoopX 2.3.0](https://github.com/loopx-project/loopx/releases/tag/v2.3.0)。
其他助手可查[各宿主的入口和续跑方式](../../../guides/newcomer-command-path.md#choose-the-loop-driver)。
手边没有失败测试，可以用[修 Bug 练习项目](../../../guides/first-bugfix-loop.zh-CN.md)。

[可选：用 SQLite 命令行练习查看工作状态](manual-cli.zh-CN.md)。
