# public-safe-outbound

对外提交（外部仓库 / PR / 发布）前的 fail-closed 脱敏扫描 capability。
对一组候选文本或路径检测：

- 凭据：Ark/ARK api key、`sk-`、`Bearer`、AK/SK、private key 块、`api_key=` 赋值；
- 公司内部信息：内部域名、内部代码托管、内部 MCP 网关、内部文档链接——
  **由部署环境注入**（`PUBLIC_SAFE_CONFIG` 指向一个本地 JSON 文件，见下）；
- 私有绝对路径：home 目录与 root 挂载等本地绝对路径；

任一命中即失败（exit 1），不修改文件，输出 compact、打码的 JSON 结果。

## 公司门禁 = 本地中心配置（不上传）

本 capability 的源码不硬编码任何公司信息。公司内部标记（域名、内网链接、
私有路径前缀、凭据模式）维护在**本地 loopx 中心区域**（例如
`~/.loopx/public-safety/internal-markers.json`），运行时经
`PUBLIC_SAFE_CONFIG` 注入：

```json
{
  "internal_domains": ["example.internal"],
  "internal_url_patterns": ["internal.doc"],
  "private_path_prefixes": ["/home/"],
  "credential_patterns": ["internal-token-"]
}
```

这份本地文件**绝不上传**到任何公开仓库；capability 在无配置时仍执行通用
规则（凭据、通用私有路径），公司特有规则由部署方提供。

## 用法

```bash
# 扫描一个路径下所有文本文件
python -m loopx.capabilities.public_safe_outbound.scan_cli \
  --scan-root <repo> --format json

# 扫描 staged diff
git diff --cached | python -m loopx.capabilities.public_safe_outbound.scan_cli \
  --diff --format json
```

带公司门禁的完整用法：

```bash
PUBLIC_SAFE_CONFIG=~/.loopx/public-safety/internal-markers.json \
  python -m loopx.capabilities.public_safe_outbound.scan_cli \
  --scan-root <repo> --format json
```

## 默认开启

作为 builtin capability 注册（origin=`builtin`），随 LoopX 运行时默认
installed/enabled，可经 `loopx capability list` / `show` 查看。

## 边界

- 保守设计：宁可误报也不漏报；命中样例只输出打码片段。
- 不做任何写操作、不读取 secrets 文件内容（仅规则扫描）。
- 规则为公开可维护列表，可按项目追加（`scanner.RULES`）。
