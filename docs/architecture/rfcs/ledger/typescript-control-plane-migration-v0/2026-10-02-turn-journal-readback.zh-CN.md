# settlement 定位的 Turn journal 回读归到一个 owner

已有 Turn journal owner 通过一个原生 File 查询，同时提供委派恢复定位和 completion
capability 证据。这推进路线图 S2/S3/S10 与状态组合 RFC 的类型化恢复边界，不代表完整
ownership → writeback → settlement 链路或 provider acceptance 已获资格。
[English mirror](2026-10-02-turn-journal-readback.md)

## 交付边界

此前 Python 分别为恢复和 capability 回读选择 journal。恢复路径跳过不可读文件，
校验的 lineage 也弱于 capability 读取。Turn 回复丢失后遇到不可读历史，可能被当成
“没有原 Turn”，使委派调用方选择新建 Turn。

`turn_journal_query.ts` 负责扫描、结构化 settlement 匹配、唯一性与证据选择，复用
已有 settlement decoder 和 journal inspector。一致的进行中 journal 仍可定位恢复；
只有终态且 replay 合法的历史提供 capability。无效或无法解释的历史不能证明不存在
或唯一；可识别的其他 Turn 和非 journal 辅助文件仍被忽略。两个 Python 公开函数只
负责传输与结果适配，不再拥有选择规则。

伴随重构将写入端既有的 status/phase 约束收进 `journalPhaseViolation`，由检查与
写入共同使用。非法组合现在也会阻止 `turn inspect-journal` 的合法回放判断、恢复
选择和 capability 证据读取。不新增状态枚举或持久化 schema；新查询方法和冲突／
检查诊断由 journal owner 局部拥有。非法的非字符串 capability 声明不再转成字符串证据。

## 证据与限制

独立反例覆盖不可读历史、identity/envelope 冲突、阶段乱序、status/phase 矛盾、
其他 settlement 字段、分隔符碰撞、两种文件顺序下的重复 identity、非法 UTF-8、
非普通文件，以及已有 Todo-only journal binding 对 scoped identity 的拒绝。
真实 File writer 的每级合法 checkpoint 都经过回读；查询不改写文件或目录内容。

真实委派／CLI／Turn 旅程使用隔离的测试 host：提交 Turn、丢失回复、损坏 journal、
重试原 operation、恢复测试数据原始字节、完成原结果返回。host 全程只执行一次；
受阻 operation 提供 `recovery_required` 和错误。此场景分别运行 File、SQLite
coordination authority，Turn journal 均为 File。completion CLI 保留有／无 capability
证据时的后继选择。这些测试不证明模型质量、PostgreSQL 或掉电持久性。

迁移测量以 `31480ca039d36fcb07d6e6d8447082bf803d4a96` 为基线，两侧使用相同的
64 文件合成 capability 查询，每侧 16 次热请求。唯一 Turn id 场景仍为一次 runtime
请求：base/head p50 为 15.72/18.50 ms，p95 为 34.67/36.06 ms。不同 Todo 复用 Turn id
场景从 64 次请求降到一次：p50 为 846.91/17.79 ms，p95 为 1015.74/34.14 ms。
这是组件查询耗时，不是完整 CLI、生产 SLO 或持续规模运行资格。

迁移核算：产品代码 +232/−244 行（净减少 12 行），其中移除 219 行 Python、新增
31 行 Python 传输／兼容代码；验证及配置 +358/−11 行，文档另计。TS 状态规则抽取
属于移动，不计作 Python 删除收益。PR 记录最终测试、安装包、语义检查与 premerge 结果。临时性能和基线对照脚本留在本地；持久不变量和真实入口测试保留。委派与
completion 调用方进入进程内 TypeScript 且 Python API 兼容窗口结束后，删除查询 facade。

两条旧 characterization 预期按已有写入不变量修正：验证前停止、失败却未记录阶段，
均保留历史但禁止回放。固定基线错误地将二者标为 replay 合法；另有六个状态阶段
反例独立检查写入／回读一致性。

## 兼容与回退

没有配置、前端控件、receipt、journal 格式、authority provider 或数据迁移变更。
已有委派错误／回读和 CLI 检查负责呈现受阻历史。默认恢复查询现在拒绝歧义或不完整
历史，不再据此选择新 Turn；capability 读取在这些失败时不提供证据。恢复经核验的
原历史，再继续原 operation；不能删 receipt 或换 id 强行推进。

扫描读取各文件的原子版本，不提供目录级快照或 provider 不存在的权威证明。执行
single-flight、当前权限、lease fencing 和提交时校验仍由已有 owner 负责；历史证据
不授予当前权限。回退代码不需要重写数据，但会恢复已经演示的较弱检查。
