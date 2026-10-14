# public-safe-outbound

对外提交（外部仓库 / PR / 发布）前的 fail-closed 脱敏扫描 capability。
对一组候选文本或路径检测：

- 凭据：Ark/ARK api key、`sk-`、`Bearer`、AK/SK、private key 块、`api_key=` 赋值；
- 公司内部信息：内部域名、内部代码托管、内部 MCP 网关、内部文档链接——
  **由部署环境注入**（`PUBLIC_SAFE_CONFIG` 指向一个本地 JSON 文件，见下）；
- 私有绝对路径：home 目录与 root 挂载等本地绝对路径；

任一命中即失败（exit 1），不修改文件，输出 compact 的 JSON 结果。回执不含绝对路径：
命中位置以扫描根为基准的相对路径标注；命中片段 `sample_masked` 是围绕匹配位置的
有界窗口，匹配本身被打码，不是文件开头。

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
# 扫描一个路径下的文本文件（见下方选择规则）
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

- 保守设计：宁可误报也不漏报；命中样例只输出围绕匹配位置的有界窗口，匹配本身以
  `***` 打码，不输出文件开头。
- **失败关闭同样适用于"没扫到"**：`PUBLIC_SAFE_CONFIG` 未设置是文档化的
  无配置模式（只跑通用规则）；已设置却读不到或解析失败时，扫描以 `ok=false`、
  `unscanned_reasons=["config_unreadable"]` 结束，不会静默降级为通用规则。
  被请求的文件读不到或超过 `limit_bytes` 时同样记为
  `content_unreadable` / `content_too_large`，`ok` 保持 false；目录等
  不指向文件的路径跳过。未扫描原因与命中位置一样只含相对标签或原因码，不含绝对路径。
- 不做任何写操作、不读取 secrets 文件内容（仅规则扫描）。
- **文件选择是排除式，不是后缀白名单**：默认扫描根目录下所有常规文件，因此
  `.env`、`Dockerfile`、`Makefile`、`id_rsa`、`credentials` 这类无后缀或点文件
  都会被检查（白名单式曾把它们静默跳过，而 `node_modules`/`.venv` 因含 `.js`/`.py`
  反而被走遍）。被排除的是版本控制、依赖、缓存、构建与虚拟环境目录
  （版本控制元数据 `.git`/`.hg`/`.svn` 见 `scan_cli.VCS_METADATA_NAMES`，作为
  目录或文件都排除；`node_modules`、`.venv`、`dist`、`build`、`__pycache__`、
  各类 `*_cache` 等工具生成目录见 `scan_cli.TOOL_MANAGED_DIRECTORY_NAMES`，只排除
  目录，同名文件仍扫描）以及图片、音视频、归档、原生库、字体、数据库等二进制后缀
  （见 `scan_cli.BINARY_SUFFIXES`；`.svg` 属文本，仍会扫描）。
  单文件读取由 `limit_bytes`（默认 1 MB）封顶。
- 规则为公开可维护列表，可按项目追加（`scanner.RULES`）。
