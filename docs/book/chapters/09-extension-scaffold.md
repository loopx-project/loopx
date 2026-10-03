# 创建 standalone Extension

本章用一份完整的零权限教学包学习 request、response、验证和打包。[配套源码](https://github.com/loopx-project/loopx/tree/main/packages/loopx-text-stats)位于 `packages/loopx-text-stats/`，包含 manifest、Python package、两份 JSON Schema、示例请求和标准库测试。

它不注册内置 Capability，不进入默认 catalog，也不读写 Goal。独立 package 拥有文本统计的输入与结果；安装、激活和受控调用继续由现有 Extension lifecycle 管理。

## 先把完整示例运行起来

下面从已核对的 LoopX checkout 根目录执行，把整个教学包复制到新目录。`copytree` 拒绝已有目标，避免覆盖先前练习。下一章继续沿用这个工作目录和环境。

```bash
python3 -c "import shutil; shutil.copytree('packages/loopx-text-stats', 'standalone-extension')"
python3 -m venv .local/book-extension-venv
. .local/book-extension-venv/bin/activate
python3 -m pip install -e . -e ./standalone-extension
python3 -m unittest discover -s standalone-extension/tests -v
loopx-text-stats --doctor
loopx-text-stats < standalone-extension/examples/request.json
```

这条路径把当前 LoopX checkout 与 provider 装进同一隔离环境。若你选择使用已安装的 release，先核对版本，再只安装 provider；不要无意将源码与发布物混用。这里没有 `[test]` extra，测试使用标准库 unittest。

这些命令完成 package 安装与直接调用，尚未写入 LoopX activation state。下一章再用独立 state file 激活一次。

## 官方 scaffold 与完成态示例有什么区别

学习从空白领域实现开始时，可另外生成起点：

```bash
loopx extension init loopx-text-stats \
  --destination text-stats-starter --execute --format json
```

目标目录必须不存在。官方 scaffold 展示 message 回显；本书完成态把它改成 text 统计，并同步修改 decoder、schemas、示例与测试。生成源码不会自动 build、安装或激活。

两条路线分别适合“先看可运行结果”和“练习修改合同”。不要把只换了函数名的 scaffold 当成已经完成领域适配的 provider。

## Manifest 只声明需要的生命周期

```toml
schema_version = "loopx_extension_manifest_v0"
id = "loopx-text-stats"
version = "0.1.0"
requires_loopx_api = ">=1,<2"
permissions = []

[runtime]
protocol = "loopx_text_stats_extension_v0"
entrypoint = "loopx-text-stats"
doctor_args = ["--doctor"]
required_permissions = []
timeout_seconds = 30
```

`id` 标识 Extension，`version` 参与 revision，`requires_loopx_api` 声明兼容窗口。Console entrypoint 需在运行 LoopX 的环境可解析。`timeout_seconds` 由 managed runtime 使用，当前合同允许 1—120 秒。

这个示例不声明 `provides` / `implements`，也没有权限需求，因此使用 standalone runner。需要受保护 effect 时，先找相应 Capability/domain contract；权限声明本身不产生授权，也不提供 OS sandbox。

## 用明确的度量定义结果

`characters` 是 Python Unicode code point 数，`words` 是空白分隔片段数，`lines` 遵循 `str.splitlines()`。所以“你好世界”的 words 是 1，组合字符可能包含多个 code point。这是示例的语义，不是通用自然语言分词。

示例输入为：

```json
{
  "schema_version": "loopx_text_stats_request_v0",
  "text": "LoopX keeps work explicit.\nTests verify the result."
}
```

成功 response 的 `result` 是：

```json
{"characters": 51, "non_whitespace_characters": 44, "words": 8, "lines": 2}
```

外层同时保留 `ok`、response schema、request schema 与 extension identity。完整输出结构见 `schemas/response.schema.json`；额外的 LoopX managed receipt 不是领域计算结果的一部分。

## Decoder 为什么还要自己校验

请求要求 object、精确 schema、非空白 text，并拒绝额外字段。JSON Schema 用于描述 wire shape；decoder 实际执行字段约束，并检查编码、重复键、输入字节上限和 Python 空白语义。

本例的输入上限是 65,536 字节，text 上限是 32,000 code point。这是教学包自己的限制，不代表 LoopX 全局默认值。

长整数暴露了另一条边界：Python 的 `json.loads` 可能因整数转换限制抛出 `ValueError`，即使数据还没超过字节上限。只捕获 `JSONDecodeError` 会让这个输入越过统一错误结果。

实现先保留 `InvalidRequest` 的专门错误，再把其他解析 `ValueError` 与递归错误规范化为 `invalid_json`。重复字段仍返回 `duplicate_field`。不放宽解释器限制，也不把底层异常或请求原文写入 receipt。

## 哪些测试有实际价值

| 场景 | 期望 | 证明范围 |
| --- | --- | --- |
| 示例文本、Unicode、重复输入 | 固定度量与确定性输出 | 本 provider 的领域函数 |
| 缺失/未知字段、错误类型/schema、重复键 | 固定错误码，无原文回显 | 输入合同与公开错误边界 |
| 字节与文本长度边界 | 上限内接受，越界拒绝 | 示例选择的本地限制 |
| 5,000 位整数 | 错误 JSON，stderr 无 traceback | Python 3.11+ 整数解析回归 |
| doctor、CLI 输出 | doctor 不处理业务请求，run 输出一个对象 | 真实 provider 子进程入口 |

源码与测试放在同一个配套目录。测试不调用模型、外部服务或 live Goal，也不证明 managed runtime 的进程回收、权限隔离或升级路径。下一章将这些生命周期步骤单独验证。

## 怎样扩展这个例子

先定义新输入与可验证结果，再同时改 decoder、schemas、示例与测试。若增加外部状态或权限需求，应重新评估 placement，不能直接给 standalone manifest 加 permission 后继续沿用零权限调用假设。

[英文 README](https://github.com/loopx-project/loopx/blob/main/packages/loopx-text-stats/README.md)与[中文 README](https://github.com/loopx-project/loopx/blob/main/packages/loopx-text-stats/README.zh-CN.md)提供同一套完整运行步骤。没有额外的配套仓库，也不需要从聊天中拼回缺失文件。
