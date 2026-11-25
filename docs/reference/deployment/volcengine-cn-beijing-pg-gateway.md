# 火山引擎 (cn-beijing) PostgreSQL 16 与远程协同网关部署参考

本文档规范 LoopX 在火山引擎北京地域（`cn-beijing`）的云端协同底座部署架构、参数化模板、最小权限开通清单及双人/多机分治联通流程。

> [!CAUTION]
> **严格红线：严禁未经用户确认自动购买云资源或改线上配置**
> 任何脚本、Agent 或自动化流绝不允许未经用户交互确认直接调用云厂商计费/下单 API（如自动开通包年包月或按量付费 RDS/ECS）或静默篡改生产环境线上防火墙/安全组/IAM 策略。所有模板与命令均为声明式、受审阅的参数化资产，仅在用户显式授权后由用户或受控发布流程执行。

---

## 1. 架构总览 (Multi-Machine Split Architecture)

为了实现本地高性能交互与云端廉价长任务并行的双人/多机协同，LoopX 采用对齐 `postgresql-authority-service-v0` 与 `monorepo-distribution-split-v0` 的协同底座：

```
+-----------------------------+               +-----------------------------+
|    Local Operator Machine   |               |     Cloud Worker Machine    |
|        operator.local       |               |         worker.cloud        |
|   (Operator Workstation)    |               |  (Volcengine ECS / Ark LLM) |
+--------------+--------------+               +--------------+--------------+
               |                                             |
               | HTTPS / TLS 1.3 (Bearer Credential)         | HTTPS / TLS 1.3 (Bearer Credential)
               | `examples/coordination/                     | `examples/coordination/
               |  remote-authority-client.ts`                |  remote-authority-client.ts`
               v                                             v
+---------------------------------------------------------------------------+
|               Volcengine `cn-beijing` Remote Authority Gateway             |
|                                                                           |
|  +---------------------------+       +---------------------------------+  |
|  |  Caddy TLS Reverse Proxy  | ----> | RemoteAuthorityService (Node24) |  |
|  |  (TLS termination,        | :8443 | `loopx/control_plane/           |  |
|  |   HSTS, IP allowlist)     |       |  coordination/remote_authority` |  |
|  +---------------------------+       +----------------+----------------+  |
|                                                       |                   |
|                                   VPC Internal SQL    |                   |
|                                   (Port 5432, No EIP) v                   |
|                              +---------------------------------+          |
|                              |  Volcengine RDS for PostgreSQL  |          |
|                              |  Version: 16 (loopx_app role)   |          |
|                              |  Schema: loopx_control_plane    |          |
|                              +---------------------------------+          |
|                                                                           |
|  +---------------------------------------------------------------------+  |
|  | Volcano Ark (`ark.cn-beijing.volces.com`) Video / LLM Model Service  |  |
|  | - 同地域低延迟交互（由部署者按环境参数配置端点与模型 ID）                 |  |
|  | - 通过独立受限环境变量注入凭据（不依赖开发机本地配置文件）               |  |
|  +---------------------------------------------------------------------+  |
+---------------------------------------------------------------------------+
```

### 核心设计原则
1. **数据库仅走 VPC 私网**：为 PostgreSQL 实例绑定独立的访问白名单，仅允许网关的私网 IPv4 地址访问 5432 端口。部署时核对实例的全部关联白名单，确保没有额外来源可访问。严禁为数据库绑定公网弹性 IP (EIP)。[火山引擎 PostgreSQL 白名单](https://docs.volcengine.com/docs/rds-for-pg/adding-an-ip-address-allowlist?lang=zh)
2. **最小受限角色装配 (`loopx_app`)**：网关服务使用专用的 `loopx_app` 角色连接数据库，仅具备 `SELECT, INSERT, UPDATE` 必要权限，禁止 `DROP TABLE`, `TRUNCATE`, `ALTER ROLE` 等管理员特权。
3. **端到端 TLS 传输认证**：外部客户端经由 TLS 1.3 连接网关，凭据在网关层做安全哈希对比（`timingSafeEqual`），绝不向数据库传递或持久化明文凭据。
4. **同地域方舟模型与受限凭据注入**：利用同在 `cn-beijing` 的火山方舟大模型/视频 API 端点（`ark.cn-beijing.volces.com`），由部署者在云端通过环境变量或密钥管理服务为 Worker 单独注入受限模型 API Key 与网关 Bearer 凭据，严禁复用或上传开发机本地私有配置文件。

---

## 2. 火山引擎 cn-beijing 资源规格清单

| 资源类别 | 建议规格 / 配置 | 计费类型建议 | 用途 |
|---|---|---|---|
| **VPC** | CIDR: `172.16.0.0/16`, Region: `cn-beijing` | 免费 | 隔离协同云上内网 |
| **子网 (Subnet)** | 可用区: `cn-beijing-a`, CIDR: `172.16.1.0/24` | 免费 | 数据库与网关部署网段 |
| **安全组 (Gateway)** | 入方向: 仅放通 443 (TLS) 给授权 IP；出方向: 允许内网访问 RDS | 免费 | 网关流量防护 |
| **白名单 (RDS)** | 绑定到 RDS 实例，仅列出网关私网 IPv4 的 `/32` 地址 | 随实例配置 | 限制数据库访问来源 |
| **轻量网关 ECS** | 规格: `ecs.g3i.large` (2C4G) 或 `ecs.c3i.large` (2C4G) | 按量计费 / 抢占式 | 运行 Caddy + RemoteAuthorityService |
| **RDS for PG** | 引擎: PostgreSQL 16，规格: `rds.postgres.1c2g` (1C2G) 或通用型 | 按量计费 (按需开启) | 承载协同权威状态与检查点 |
| **弹性公网 IP** | 带宽: 1-5 Mbps 按流量计费，绑定至网关 ECS | 按量计费 | 提供 HTTPS 客户端安全接入 |

---

## 3. 最小权限开通清单 (Volcengine RAM Policy)

远程 Worker 与 CI/CD 自动化部署账号仅授予最小只读查询权限。模板文件位于 `examples/coordination/volcengine/least-privilege-iam-policy.json`：

```json
{
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "rds_postgresql:DescribeDBInstances",
        "rds_postgresql:DescribeDBInstanceDetail",
        "rds_postgresql:DescribeDatabases",
        "rds_postgresql:DescribeAccounts",
        "vpc:DescribeVpcs",
        "vpc:DescribeSubnets",
        "vpc:DescribeSecurityGroups",
        "ecs:DescribeInstances",
        "ecs:DescribeInstanceStatus"
      ],
      "Resource": ["*"]
    }
  ]
}
```

> [!NOTE]
> 任何创建资源（`CreateDBInstance`、`RunInstances`）或修改安全组（`AuthorizeSecurityGroupIngress`）的操作必须由主账号管理员在控制台审批后执行，不向任何日常自动化代理开放。

---

## 4. 参数化部署模板

配套部署模板位于 `examples/coordination/volcengine/` 目录：
1. **Terraform 编排模板**：`main.tf`
   - 锁定 `volcengine/volcengine` provider 已发布约束（`>= 0.0.186, < 0.1.0`，支持 `PostgreSQL_16`）。
   - 定义 VPC、子网、安全组、`volcengine_rds_postgresql_instance`（使用 `node_spec`、`primary_zone_id`、`secondary_zone_id` 与 `charge_info` 块）及 `rds_endpoints` / `rds_endpoint` 输出。
   - 通过 `gateway_private_ips` 提供网关实际使用的私网 IPv4 地址。模板为每个地址生成 `/32` 白名单条目并绑定到 PostgreSQL 实例。更换网关地址时同步更新白名单。
   - 所有变量均由部署者显式传入或使用安全默认值（如北京地域 `cn-beijing`、默认仅放通 VPC 内网至 443 端口），并设置 `prevent_destroy = true` 避免误删。
2. **TLS 网关配置**：`gateway-caddy-tls.Caddyfile`
   - Caddy（以独立系统用户 `caddy` 运行）使用 `/etc/ssl/loopx/cert.pem` 与 `/etc/ssl/loopx/key.pem` 完成对外 `:443` TLS 终止，并反向代理至本地 Node 权威服务（`https://127.0.0.1:8443`）。
   - 包含请求大小限制（16KB）、连接超时约束与 HSTS 安全头，并与 Node 内部回环私钥及数据库/策略配置严格隔离。
3. **网关守护进程服务**：`remote-authority-systemd.service`
   - 以 `DynamicUser=yes` 临时非 root 服务身份运行 `examples/coordination/remote-authority-service.ts`，并启用 `ProtectSystem=strict`, `PrivateTmp=true`, `NoNewPrivileges=true` 等 systemd 安全加固。
   - **凭据与数据库隔离**：仅由 systemd（root）通过 `EnvironmentFile=/etc/loopx/authority.env` 独立注入 `LOOPX_AUTHORITY_DATABASE_URL`（使用受限 `loopx_app` 角色），不与私钥混入同一传递面。
   - **最小只读凭据移交 (`LoadCredential=` / `%d/`)**：因 `DynamicUser=yes` 进程不是 `root`，直接传入 `0600 root:root` 原始文件路径会触发 `EACCES` 退出导致网关无法监听。unit 通过 `LoadCredential=` 将 `/etc/loopx/remote-policy.json`、`/etc/ssl/loopx/internal-cert.pem` 与 `/etc/ssl/loopx/internal-key.pem` 以仅当前服务临时用户只读（`0400`）方式挂载至 `%d/remote-policy.json`、`%d/authority-cert.pem`、`%d/authority-key.pem` 传给 `ExecStart`。

### 4.1 属主、最小权限与 Caddy 隔离契约

部署前必须在网关 ECS 上按以下最小权限收敛目录与密钥（切勿为规避 `EACCES` 将任何私钥放宽为 `0644` 或把 Node 凭据组权限授给 `caddy`）：

| 路径 | 属主 (`chown`) | 权限 (`chmod`) | 注入 / 读取方式 | 访问边界与隔离说明 |
|---|---|---|---|---|
| `/opt/loopx/` | `root:root` | 目录 `0755` / 文件 `0644` | Node `ExecStart` 只读运行 | 含 `examples/coordination/` 与 `node_modules`；`ProtectSystem=strict` 下只读 |
| `/etc/loopx/` | `root:root` | `0700` | 仅 systemd PID 1 遍历 | 禁止非 root 用户（含 `caddy` 与临时服务用户）列出或直接访问 |
| `/etc/loopx/authority.env` | `root:root` | `0600` | `EnvironmentFile=/etc/loopx/authority.env` | 仅存 `LOOPX_AUTHORITY_DATABASE_URL`（`loopx_app` 串）；不进入 `%d/`，`caddy` 不可读 |
| `/etc/loopx/remote-policy.json` | `root:root` | `0600` | `LoadCredential=remote-policy.json:...` -> `%d/remote-policy.json` (`0400`) | 仅 Node 临时身份只读；`caddy` 不可读；原子更新文件后需 `systemctl restart remote-authority-systemd.service` 刷新 |
| `/etc/ssl/loopx/` | `root:caddy` | `0750` | 仅 `root` 与 `caddy` 组遍历 | 阻断其他本地普通用户进入证书目录 |
| `/etc/ssl/loopx/internal-cert.pem` | `root:root` | `0644` | `LoadCredential=authority-cert.pem:...` -> `%d/authority-cert.pem` (`0400`) | Node `127.0.0.1:8443` 回环公钥证书（非私钥；向部署检查端提供公钥 `--ca` 验证） |
| `/etc/ssl/loopx/internal-key.pem` | `root:root` | `0600` | `LoadCredential=authority-key.pem:...` -> `%d/authority-key.pem` (`0400`) | **仅服务内部回环私钥**；非 root 直接读取原路径必报 `EACCES`；`caddy` 无权读取 |
| `/etc/ssl/loopx/cert.pem` | `root:root` | `0644` | Caddy 只读 | 对外 `:443` TLS 公钥证书（非私钥），与客户端 `--ca` 校验对齐 |
| `/etc/ssl/loopx/key.pem` | `root:caddy` | `0640` | Caddy 直接只读（保持 Caddy 私钥读取范围） | **仅 Caddy 对外私钥**；Node 临时用户无读取权限，两层密钥隔离 |
| `/var/log/caddy/` | `caddy:caddy` | `0750` | Caddy 访问日志写入 | 独立日志目录，不包含数据库或 Bearer 凭据 |

---

## 5. 多机分治与双人协同联调流程

### 步骤 1：本地零成本预演 (Colima / Docker)
在向火山引擎上线前，必须先在本机运行 Colima 端到端彩排套件验证：
```bash
# 模拟模式（无任何容器开销，内存数据库 + 本地 HTTPS/TLS 服务端快速预检 8 个核心切片）
./examples/coordination/colima-pg16-e2e-rehearsal.sh --simulate

# 容器模式（启动隔离 postgres:16-alpine 容器 + 受限角色 + HTTPS/TLS 联调，缺少 Docker 直接失败退出）
./examples/coordination/colima-pg16-e2e-rehearsal.sh --rehearsal
```

### 步骤 2：目标库初始化、受限账号装配与创世种子灌入
进入全新的火山引擎 RDS PostgreSQL 16 实例后，必须严格拆分**管理员（一次性初始化与角色赋权）**与**受限运行账号（`loopx_app`，seed 写入与网关运行）**两类凭据与职责，并复用 Stage 2B CLI（`loopx postgres setup` 与 `local-to-pg-seed --execute`）完成灌入与游标回读：

1. **管理员一次性初始化与角色装配（仅使用管理员连接）**：
   - **管理员职责**：安装 `loopx_control_plane` schema、行级安全（RLS）策略与目标库唯一 `store_identity`；确保受限账号 `loopx_app` 具备登录与 `CONNECT` 权限且无管理员特权（`NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT`）。管理员连接仅用于一次性初始化、升级或轮换，严禁写入网关 `EnvironmentFile` 或用于日常 seed / 网关运行。
   ```bash
   # 管理员连接串仅在初始化阶段使用
   export LOOPX_POSTGRES_URL="postgresql://<admin-user>:<admin-password>@<rds-private-host>:5432/<db-name>"

   # 2.1a 先执行只读 dry-run 预览（不连接或修改数据库）
   loopx --format json postgres setup --runtime-role loopx_app

   # 2.1b 显式执行 schema、RLS、store_identity 安装与 loopx_app 最小表权装配
   loopx --format json postgres setup --runtime-role loopx_app --execute
   ```
   记录输出 JSON 中的 `store_identity`（格式 `postgresql:<32 位小写十六进制>`）。`setup --execute` 会在同一事务中为 `loopx_app` 授予最小表级权限（`USAGE ON SCHEMA loopx_control_plane`、`authority_store_metadata` 只读 `SELECT`、`authority_heads` 的 `SELECT, INSERT, UPDATE`、`authority_checkpoints/commits/events/receipts` 的 `SELECT, INSERT`，禁止 `DELETE`、`TRUNCATE` 与 DDL）。若 RDS 尚未预先创建可登录的 `loopx_app` 角色（`setup` 默认以 `NOLOGIN` 建角），由管理员补齐登录口令与库级连接权限：
   ```sql
   ALTER ROLE loopx_app LOGIN PASSWORD '<restricted-password>' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
   GRANT CONNECT ON DATABASE <db-name> TO loopx_app;
   ```

2. **受限账号执行创世种子脱敏预览与灌入（使用 `loopx_app`）**：
   - **受限账号职责**：`loopx_app` 仅向尚无权威状态的空目标 Goal 写入游标 `1` 的创世种子，不安装 schema、不修改角色权限、也不迁移既有历史日志。完成初始化后立即将 `LOOPX_POSTGRES_URL` 切换为受限账号连接串，并通过 `--store-identity` 绑定目标库身份：
   ```bash
   export LOOPX_REGISTRY="/path/to/registry.json"
   export LOOPX_RUNTIME_ROOT="/path/to/operator-runtime-root"
   export LOOPX_GOAL_ID="<goal-id>"
   export LOOPX_TENANT_ID="<tenant-id>"
   export LOOPX_SEED_OUTPUT="/path/to/private/genesis-seed.json"

   # 2.2a 只读脱敏预览（自动剔除本地绝对路径、.local/ 引用、飞书 ID 与已过期租约；不连接目标 PostgreSQL）
   loopx --registry "$LOOPX_REGISTRY" --runtime-root "$LOOPX_RUNTIME_ROOT" \
     --format json postgres local-to-pg-seed \
     --goal-id "$LOOPX_GOAL_ID" \
     --tenant-id "$LOOPX_TENANT_ID" \
     --output "$LOOPX_SEED_OUTPUT"

   # 2.2b 切换为受限运行账号并绑定 store_identity 执行灌入（写入游标 1）
   export LOOPX_STORE_IDENTITY="<store-identity-from-setup>"
   export LOOPX_POSTGRES_URL="postgresql://loopx_app:<restricted-password>@<rds-private-host>:5432/<db-name>"

   loopx --registry "$LOOPX_REGISTRY" --runtime-root "$LOOPX_RUNTIME_ROOT" \
     --format json postgres local-to-pg-seed \
     --goal-id "$LOOPX_GOAL_ID" \
     --tenant-id "$LOOPX_TENANT_ID" \
     --store-identity "$LOOPX_STORE_IDENTITY" \
     --output "$LOOPX_SEED_OUTPUT" \
     --execute
   ```
   确认返回 JSON 中 `status` 为 `"seeded"`、`cursor` 为 `"1"`、`provider_revision` 形如 `<store_identity>:1`（若使用完全相同的参数与 `--output` 文件重试，将幂等读回原回执；目标 Goal 若已存在不同权威状态则拒绝覆盖）。

3. **目标 Goal 与游标回读校验**：
   执行步骤 3 客户端认领前，先使用受限账号 `LOOPX_POSTGRES_URL` 直接从目标库回读 Goal 与游标，确认 `status === "loaded"`、`cursor === "1"` 且 `provider_revision` 匹配：
   ```bash
   node --no-warnings --experimental-sqlite --experimental-strip-types \
     --input-type=module -e '
     import {Pool} from "pg";
     import {PostgreSqlAuthorityStore} from "./loopx/control_plane/coordination/postgresql_authority_store.ts";
     const pool = new Pool({connectionString: process.env.LOOPX_POSTGRES_URL});
     try {
       const store = new PostgreSqlAuthorityStore({
         connect: async () => {
           const client = await pool.connect();
           return {
             query: (sql, args) => client.query(sql, args ? [...args] : undefined),
             release: (err) => client.release(err),
           };
         },
       }, {
         tenant_id: process.env.LOOPX_TENANT_ID,
         goal_id: process.env.LOOPX_GOAL_ID,
         expected_store_identity: process.env.LOOPX_STORE_IDENTITY,
       });
       const loaded = await store.loadAuthority();
       if (loaded.status !== "loaded") {
         throw new Error(`Expected loaded target goal, got ${loaded.status}`);
       }
       console.log(JSON.stringify({
         status: loaded.status,
         goal_id: loaded.head.goal_id,
         cursor: loaded.cursor,
         provider_revision: loaded.provider_revision,
         todo_count: Array.isArray(loaded.head.todos) ? loaded.head.todos.length : 0,
       }));
     } finally {
       await pool.end();
     }
   '
   ```
   在网关 ECS 上按 4.1 节属主与最小权限就位后，启动托管单元并验证**以非 root 临时服务身份监听**以及端到端 `snapshot` 回读：
   ```bash
   # 1. 收敛属主与最小读取权限（先创建凭据文件再启用服务）
   sudo install -d -o root -g root -m 0700 /etc/loopx
   sudo install -d -o root -g caddy -m 0750 /etc/ssl/loopx
   sudo chown root:root /etc/loopx/authority.env /etc/loopx/remote-policy.json \
     /etc/ssl/loopx/internal-cert.pem /etc/ssl/loopx/internal-key.pem /etc/ssl/loopx/cert.pem
   sudo chmod 0600 /etc/loopx/authority.env /etc/loopx/remote-policy.json /etc/ssl/loopx/internal-key.pem
   sudo chmod 0644 /etc/ssl/loopx/internal-cert.pem /etc/ssl/loopx/cert.pem
   sudo chown root:caddy /etc/ssl/loopx/key.pem
   sudo chmod 0640 /etc/ssl/loopx/key.pem

   # 2. 启动 systemd 服务（LoadCredential 将策略与回环证书/私钥只读移交 %d/）
   sudo systemctl daemon-reload
   sudo systemctl enable --now remote-authority-systemd.service
   sudo systemctl restart caddy

   # 3. 确认服务进程以非 root 临时用户运行且成功监听 127.0.0.1:8443
   systemctl show -p ActiveState,UID,GID,ExecMainPID remote-authority-systemd.service
   ps -o uid=,user=,gid=,group=,pid=,args= -p "$(systemctl show -p ExecMainPID --value remote-authority-systemd.service)"
   sudo ss -tlnp | grep -E ':443|:8443'

   # 4. 导出公钥 CA 副本并以普通客户端身份完成一次合法 snapshot 回读（先直连回环端口，再经 Caddy 对外入口复验）
   sudo install -m 0644 /etc/ssl/loopx/internal-cert.pem /etc/loopx-public-internal-ca.pem
   LOOPX_AUTHORITY_CREDENTIAL="<operator-bearer-secret>" \
   node --no-warnings --experimental-strip-types \
     examples/coordination/remote-authority-client.ts \
     --endpoint "https://127.0.0.1:8443" \
     --ca /etc/loopx-public-internal-ca.pem <<'EOF'
   {
     "schema_version": "loopx_remote_authority_request_v0",
     "tenant_id": "<tenant-id>",
     "goal_id": "<goal-id>",
     "actor_id": "<registered-actor>",
     "store_identity": "<store-identity-from-setup>",
     "operation": "snapshot"
   }
   EOF
   ```
   确认输出 JSON 中 `"status": "snapshot"` 且 `goal_id`、`store_identity`、`provider_revision` 与待办列表 `todos` 一致，再经 Caddy 对外地址（`--endpoint "https://<gateway-domain>" --ca "<path-to-gateway-ca.pem>"`）发送同一 `snapshot` 查询确认源 Goal 为 `hard_lease` 模式与规范 `todo_<token>` 任务，之后进入步骤 3。

### 步骤 3：多机客户端并发联调
每个客户端仅持有所属 `principal` 的受限网关 Bearer 凭据与目标 `store_identity` / `tenant_id` / `goal_id` 参数，不接触数据库管理员凭据或彼此私钥：
- **操作员节点 (`operator.local`)**：
  通过环境变量或密钥代理注入网关凭据，经 TLS CA 校验将命令 JSON 通过标准输入提交给网关：
  ```bash
  LOOPX_AUTHORITY_CREDENTIAL="<operator-bearer-secret>" \
  node --no-warnings --experimental-sqlite --experimental-strip-types \
    examples/coordination/remote-authority-client.ts \
    --endpoint "https://<gateway-domain>" \
    --ca "<path-to-gateway-ca.pem>" < command.json
  ```
- **云端 Worker (`worker.cloud`)**：
  由部署者在云端环境独立注入以下受限凭据与参数（可通过 ECS 实例环境文件、systemd `EnvironmentFile` 或云厂商密钥托管服务挂载，权限收紧为 `0600`，不依赖任何开发机本地文件）：
  - `LOOPX_AUTHORITY_CREDENTIAL`：仅授权 `worker.cloud` 对应 `principal` 与目标 scope 的网关 Bearer 凭据。
  - 模型调用参数与密钥（如 `ARK_API_KEY`、`ARK_ENDPOINT=https://ark.cn-beijing.volces.com/api/v3` 与目标 `MODEL_ID`）：独立签发的最小额度模型凭据，仅用于 Worker 执行分配任务。
  - `LOOPX_GATEWAY_ENDPOINT` 与 `LOOPX_GATEWAY_CA`：部署者配置的网关域名及可信 CA 路径。
  Worker 通过 `examples/coordination/remote-authority-client.ts` 获取租约并执行分配的任务，随后向网关回传 `progress` 与带验证证据的 `complete` 指令；若遇到网络超时或未知响应，须保留原请求 JSON 与 `request_id`，改用 `--recover` 只读查询回执，严禁自动重试或替换 `request_id`。
- **租约冲突与超期接管**：
  若云端 Worker 节点因网络断连或故障未续租，租约到达 TTL 后，其他已授权节点可按服务端时钟合法接管继续推进，并在完成后由任一授权节点经 `snapshot` / `read` 回读一致终态。
