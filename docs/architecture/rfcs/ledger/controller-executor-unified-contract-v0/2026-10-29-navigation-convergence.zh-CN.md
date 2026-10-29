# C1 导航收敛检查点

English mirror: [same checkpoint](2026-10-29-navigation-convergence.md).

C1 随后采用已合入 #7627 的文件优先交付及文件差分，集成目标为
`385eb853c1d37f5a285f2e8e0b875bd0ea04d3b3`；#7643 的来源保真修复保留。
原 `--turn-envelope` 拼写不再改变建议导航或诊断详情选择，两种调用消费相同 owner。

C1 在数值引用检查点 `16626b9d08b4017376093cff7c669241d68f99bb` 后集成目标主干
`bcb1a8710844bda6b84c2a00cf5d86ba78e126d9`。本项是 #7430 在途集成，不是新默认
或实际安装采用。

实际冲突是同一读取决定存在两个 owner：C1 未发布的强制成果 scope 与已合入
#7560 的建议性导航。集成采用主干导航、完整 authority 快照及 native 规划前发布
当前恢复的顺序，删除 scope wrapper、读取指令改写器、普通读取的专用成果 reader
和融合 native 操作。普通详情复用通用 `context-read --select`；staged replan
保留其原 capture 绑定的验收引用及共享索引。不增加 authority store、reader、
scheduler 或审批门。

签名完整契约在原恢复 capture 中保持不变，不改写为部分签名 decision。既有未签名
材料投影仍保留累计要求，通过同一精确 capture selector 按需读取支撑历史。真实
File/SQLite 集成核验完整签名来源、原最后约束、八项声明来源、选择读取与 authority
状态不变。可选规划中断从当前 capture 恢复，不重新准入或重放结算。

已移除的融合操作在简单 native 投影中使用一次 owner 调用；当前路径明确观察到
既有 planner 与 codec 两次调用。旧调用数改进及 scope 专用体积/耗时结果保留为
历史，不资格化本次集成。不宣称延迟、模型 token、验收成果或完整成本收益。
#7627 的来源实现已经合入并由本集成复用，实际安装/模型采用与成果仍须单独验收。

验证及余项绑定 PR 精确 head。保留首次集成 collection 错误、两项旧布局失败及
已退役 reader 测试失败 cohort，再验证当前通用/staged 路径；不降低原 quota 预算，
不把测试数变化当作收益。数值 source/hash 身份、writer/caller 替换、受支持历史
reader 退出及原 quota 单会话成果/完整成本对照仍开放，可选双会话独立。
