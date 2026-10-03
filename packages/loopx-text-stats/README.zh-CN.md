# 文本统计：Developer Book 配套示例

[English](README.md)

这是书中使用的完整零权限 standalone Extension。它是教学包，不是内置 Capability、
已发布的 PyPI 包或默认 Extension catalog 成员。通用 Extension lifecycle 管激活；
这个包只拥有文本输入和计算结果，不拥有 Goal authority。

`characters` 计算 Python Unicode code point；`words` 计算空白分隔片段，不做中文分词；
`lines` 遵循 `str.splitlines()`。Decoder 限制输入为 65,536 字节、文本为 32,000 code point，
拒绝重复键和纯空白，失败只返回固定错误码。JSON Schema 描述 wire shape；decoder 另外检查
编码、字节上限、重复键和 Python 空白语义。

## 运行完整示例

从已核对版本的 LoopX checkout 根目录执行。`copytree` 会拒绝已经存在的目标目录。
下一章继续复用这个环境和工作目录。

```bash
python3 -c "import shutil; shutil.copytree('packages/loopx-text-stats', 'standalone-extension')"
python3 -m venv .local/book-extension-venv
. .local/book-extension-venv/bin/activate
python3 -m pip install -e . -e ./standalone-extension
python3 -m unittest discover -s standalone-extension/tests -v
loopx-text-stats --doctor
loopx-text-stats < standalone-extension/examples/request.json
```

上面把当前 LoopX checkout 和 provider 安装到隔离 Python 环境。若使用已有发布版，
只把 provider 装进那个选定环境，先核对 `loopx --version`，不要无意混入源码安装。
Provider 和测试仅用标准库，不需要 `[test]` extra。

激活时使用独立状态文件，所有命令沿用同一个变量：

```bash
extension_state_dir="$(mktemp -d)"
extension_state="$extension_state_dir/state.json"
loopx extension install --manifest standalone-extension/extension.toml --state-file "$extension_state" --format json
loopx extension install --manifest standalone-extension/extension.toml --state-file "$extension_state" --execute --format json
loopx extension run loopx-text-stats --input-json standalone-extension/examples/request.json --state-file "$extension_state" --execute --format json
loopx extension disable loopx-text-stats --state-file "$extension_state" --execute --format json
```

先审查预览，再执行 install。Disable 后的 run 应被拒绝；启用、升级、恢复 package 与
rollback 见书中的生命周期章节。只运行源代码测试时，无需安装：

```bash
python3 -m unittest discover -s packages/loopx-text-stats/tests -v
```

## 测试证明什么

测试覆盖正确结果、Unicode 度量、重复输入、额外/缺失字段、错误类型和 schema、重复键、
编码和大小限制、doctor 及 CLI 一次一对象输出。超长整数回归在子进程中将 Python 的整数转换
限制固定为 4,300 位：包含 5,000 位整数的请求应返回错误 JSON，不能输出 traceback。

这些测试不替代 managed-runtime lifecycle 或 OS sandbox 资格验证。`permissions = []`
不会沙箱化第三方代码。任意文件读取、网络调用、凭据、Goal/Todo 写入或模型调用都不属于此
provider；安装前仍应审查实际实现。Activation state、环境和构建产物不能进入提交。
