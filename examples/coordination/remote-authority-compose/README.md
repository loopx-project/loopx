# LoopX 远程协同自托管

在装有 Docker Engine 和 Compose 的机器上解压构建好的包，进入目录运行。首次启动需要访问镜像仓库和 npm。选一个空闲的回环端口，默认使用 8443。

```sh
./start.sh
./client.sh a read
./client.sh b read
./client.sh reader read
```

两个可信写客户端拥有不同凭据，可以交接同一个示例任务。额外的 `reader` 使用独立凭据，只能读取获准的示例任务。首次启动创建 PostgreSQL 权威库、私有 CA 和客户端。重复启动沿用卷里的任务、存储身份和撤权策略。数据库在内部网络中运行。

用 `./client.sh a descriptor` 取得请求的租户、Goal、Actor 和存储实例。把请求放在标准输入上，用 `./client.sh a request` 执行。发送超时后，保留原请求及 `request_id`，通过 `./client.sh a recover` 查询回执。

远端客户端可用 SSH 将网关的回环端口转发到本机，再用同一版本的 `remote-authority-client.ts` 和 `--ca` 连接。管理员可导出 `./client.sh a ca`，并私下交付对应客户端卷中的 `credential` 和 `request.json`。客户端通过 `LOOPX_AUTHORITY_CREDENTIAL` 接收凭据。 [网关请求与重试说明（英文）](https://github.com/loopx-project/loopx/blob/main/docs/reference/remote-authority-task-service-v0.md)

两个 TLS 网关都只发布回环端口：可信写客户端使用 `LOOPX_PORT`（默认 8443），受限读者使用 `LOOPX_READ_PORT`（默认 8444）。`reader` 的私有策略绑定当前 Goal 实例、具体 Todo 及其原始创建/绑定回执；未列入的资源默认拒绝。snapshot 仅返回获准状态，不含 note、租约密钥或全局游标。读授权不授予历史操作回执访问。

可信写入口保留 v0 的范围和操作授权，a/b 可读取同范围任务；当前 v1 资源 ACL 只支持 read/snapshot，不能提供资源级写隔离。向观察者仅交付 `reader` 的凭据和只读端口。两入口共用原服务和受限数据库角色，不能将读者隔离表述为写客户端也被资源 ACL 限制。客户端到网关使用 TLS，数据库连接走隔离 backend 网络。

## 构建和验收

从经审阅的模板 checkout 运行。选择用于构建网关的完整 Git 提交。

```sh
python3 examples/coordination/remote-authority-compose/build_bundle.py \
  --source-revision "$(git rev-parse HEAD)" --output /tmp/loopx-authority.tar.gz
```

同一源码提交和模板内容生成相同的归档。归档内含 `LICENSE`、`NOTICE`、源码摘要及固定镜像清单。Docker 构建前会校验源码摘要。生成物、运行凭据、数据库和备份应存放在各自的发布或运行位置。

源码版本须包含 scoped v1 读取实现（#8861）。在专用测试 Docker 环境中解压验证，使用唯一的 Compose 项目名和两个空闲回环端口。在 amd64 主机上验证本机架构和实际运行架构。冷启动与缓存启动分别记录镜像拉取、构建、就绪与客户端首次读回时间。确认数据库端口未发布，客户端无法访问 backend 网络，网关不向客户端提供数据库凭据。

从仓库运行下列演练。它会创建临时项目，验证写客户端的认领、到期接管、新增任务与完成操作的原回执恢复及重启重放，以及只读客户端的资源过滤、拒写、撤权重启和策略缺失/损坏拒绝，最后清理自己的容器和卷。

```sh
node examples/coordination/remote-authority-compose/rehearse.mjs /path/to/unpacked-bundle
```

Compose 为数据库、网关及辅助容器设置 CPU、内存、进程数和日志轮转。共享主机还需核对 Docker 数据盘空间与已有负载。选择独立项目名、端口、网络和卷。两个同主机容器可以验证协议，跨物理主机资格需从另一台主机连接验证。

## 备份、恢复和轮换

```sh
./state.sh backup /absolute/path/to/new-backup
./start.sh
COMPOSE_PROJECT_NAME=loopx-restored ./state.sh restore /absolute/path/to/new-backup
COMPOSE_PROJECT_NAME=loopx-restored LOOPX_PORT=8445 LOOPX_READ_PORT=8446 ./start.sh
```

备份会停止当前 Compose 项目，保存同一时点的数据库和凭据卷。请将含私钥及数据库凭据的备份放在加密受限的位置。恢复前校验摘要和包版本，并要求新项目的目标卷尚未创建。启动后读回任务和原操作回执，确认旧项目已停止接收请求再切换入口。

- 撤权时原子替换对应网关卷中的 `policy.json`。只读策略位于 `reader-gateway` 卷，写策略位于 `gateway` 卷；撤销资源条目或凭据后下一次请求即拒绝。不能把 v0 策略放到已启动的 v1 监听器来降级权限。轮换密钥须生成新的高熵凭据，同步更新私有策略和客户端凭据。备份也含历史凭据。
- 自动生成的服务叶证书有效期为 397 天。到期前用 `pg-admin` 中的 CA 续签，保留原 SAN。切换前在客户端用原 CA 校验新证书，确认各客户端时钟已到新证书的 `notBefore` 时间。将配对的 `cert.pem` 和 `key.pem` 更新到 `gateway` 与 `reader-gateway` 两个卷中。沿用同一 Compose 项目配置运行 `docker compose restart gateway reader-gateway`。用 `docker compose ps gateway reader-gateway` 确认两个服务均已就绪（`healthy`），再校验两个 TLS 入口，再用 `./client.sh a read`、`./client.sh b read` 和 `./client.sh reader read` 读回任务。更换 CA 时提前分发新信任。
- 更新 PostgreSQL 口令或版本前先备份，在隔离项目演练迁移和回退。同步修改数据库角色与对应凭据文件。PostgreSQL 跨大版本需使用其正式迁移流程。

[English](README.en.md)
