# 安装 LoopX Workbench

本指南安装 `loopx-project/loopx` 仓库里的 `apps/workbench`，不安装上游 Calli。先确认主机有 Git、Node.js 22+ 和 npm。系统依赖需要另行安装时，先向用户说明具体命令及影响。

## 预览、安装与版本

默认安装当前 `main`；要复现某个版本，将下面的 `main` 替换为完整提交 SHA 或 tag。脚本和源码使用同一个 ref。先用 `--dry-run` 检查安装目录与版本。

```bash
ref=main
curl -fsSL "https://raw.githubusercontent.com/loopx-project/loopx/$ref/apps/workbench/install.sh" -o /tmp/loopx-workbench-install.sh
LOOCHAT_REF="$ref" bash /tmp/loopx-workbench-install.sh --dry-run
LOOCHAT_REF="$ref" bash /tmp/loopx-workbench-install.sh
```

`--dry-run` 只输出步骤，不下载源码、不写配置、不启动服务。`--no-service` 安装并打印前台启动命令；macOS 或没有 systemd 用户会话的 Linux 也走此路径。安装器输出完整提交 SHA、Workbench 目录、访问地址和 token；将 token 交给发起安装的用户，不粘贴到公开日志或 PR。

默认布局：

- Git 克隆：`~/.local/share/loochat`，可由 `LOOCHAT_HOME` 覆盖。
- 应用和 npm workspaces：`~/.local/share/loochat/apps/workbench`。
- 配置：`~/.config/loochat/.env`；设置 `XDG_CONFIG_HOME` 时跟随该目录。
- 数据：`~/.local/state/loochat/data`；设置 `XDG_STATE_HOME` 时跟随该目录。

使用 shallow、partial clone 和 sparse checkout，只检出 `apps/workbench` 及 Git cone 模式保留的父目录文件。Git 服务不支持 partial clone 时会退回下载该浅历史所需的对象；不会检出整个仓库。`LOOCHAT_REPO` 可以覆盖 Git 来源，例如 `file:///path/to/loopx`。更新时继续传入同样的来源与目录。

## 连接 LoopX Core

安装器只构建 Workbench，不包含或自动启动 Python Core。不要在这个稀疏检出中运行 `python3 -m loopx.cli`。普通 agent 会话不依赖 Core；创建、查看和继续 Goal 需要 Core 与它所用注册表对应的 CLI。

先按 [LoopX 仓库安装说明](https://github.com/loopx-project/loopx#readme) 单独安装 Core。如果从源码运行，使用另一个完整 LoopX checkout，并在那里安装依赖。保持 Core 和 CLI 使用同一环境、注册表以及兼容版本。在 Workbench 的 `.env` 中按实际环境配置，例如：

```dotenv
LOOCHAT_LOOPX_CORE_URL=http://127.0.0.1:8792
LOOCHAT_LOOPX_CMD=/path/to/core/.venv/bin/python -m loopx.cli
LOOCHAT_LOOPX_CWD=/path/to/full/loopx
LOOCHAT_LOOPX_REGISTRY=/path/to/registry.global.json
```

已安装 `loopx` CLI 且使用默认注册表时，可以省略后三项。Core 的启动与停止由它自己的部署流程管理；安装 Workbench 不会重启已有 Core。首次安装也可通过环境变量 `LOOCHAT_LOOPX_CORE_URL` 设置生成配置中的 Core 地址，已有 `.env` 不会被覆盖。

安装器用 `/healthz` 检查 Core 连通性，并在不可达时说明 Goal 功能不可用。启动 Workbench 后，验证：

1. 按安装器打印的命令启动，或查看 `systemctl --user status loochat`。
2. 访问所打印地址，输入 token，确认 Workbench `/api/health` 正常。
3. 通过带 Workbench token 的 `/api/loopx/healthz` 检查 Core 代理；无法连接时应返回 `502`、`loopx_core_unavailable` 和目标地址。
4. 修正 `.env` 或启动 Core 后，按下节方式重启 Workbench 并重试。连通性通过不代表已验证 Goal 创建和会话恢复，需按用户授权另外验收。

## 更新、重启与卸载

更新时重跑同一个安装入口；分支 ref 获取最新提交，完整 SHA 则保持固定版本。安装器打印实际检出的 SHA。存在源码改动或安装后新增的本地提交时，先保存到另一个工作目录，再更新。上游 Calli 的已有克隆不能就地当作本仓库更新：请选择新的 `LOOCHAT_HOME`；原有配置与数据仍保留。

Linux 服务的 `WorkingDirectory` 指向 `apps/workbench`。`scripts/install-service.sh` 和 `scripts/restart.sh` 都从自身目录定位应用，因此在安装后的应用目录内运行：

```bash
cd ~/.local/share/loochat/apps/workbench
scripts/restart.sh               # 构建、等线程空闲、重启并检查健康
scripts/install-service.sh --remove
```

`--no-service` 安装不会管理手动启动的进程。更新前先正常停止自己启动的 Workbench，更新后重跑安装器打印的前台命令。

```bash
LOOCHAT_REF="$ref" bash /tmp/loopx-workbench-install.sh --uninstall --dry-run
LOOCHAT_REF="$ref" bash /tmp/loopx-workbench-install.sh --uninstall
```

卸载只停止属于这个克隆的 systemd 服务并删除克隆；配置、token 和线程数据保留。手动运行的进程需先正常退出；指向其他目录的同名服务不会被停止。Core 是独立安装，卸载 Workbench 不会删除或停止 Core。
