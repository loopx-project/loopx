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
1. **网络零公网暴露数据库**：PostgreSQL 实例仅绑定 VPC 内网子网，安全组仅对网关 ECS 开放 5432 端口；严禁绑定公网弹性 IP (EIP)。
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
| **安全组 (RDS)** | 入方向: 仅放通 5432 来自网关安全组；严禁任何 `0.0.0.0/0` | 免费 | 数据库访问收敛 |
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
   - 所有变量均由部署者显式传入或使用安全默认值（如北京地域 `cn-beijing`、默认仅放通 VPC 内网至 443 端口），并设置 `prevent_destroy = true` 避免误删。
2. **TLS 网关配置**：`gateway-caddy-tls.Caddyfile`
   - Caddy 自动化 TLS 反向代理至本地 Node 权威服务（`https://127.0.0.1:8443`）。
   - 包含请求大小限制（16KB）、连接超时约束与 HSTS 安全头。
3. **网关守护进程服务**：`remote-authority-systemd.service`
   - 以独立低特权系统用户运行 `examples/coordination/remote-authority-service.ts`。
   - 通过仅 root 可读的 `EnvironmentFile=/etc/loopx/authority.env` 注入 `LOOPX_AUTHORITY_DATABASE_URL`（使用受限 `loopx_app` 角色），并启用 `DynamicUser=yes`, `ProtectSystem=strict`, `PrivateTmp=true` 等 systemd 安全加固。

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

### 步骤 2：创世种子脱敏与灌入
部署者按目标环境设置参数变量后，使用 Stage 2B 工具导出脱敏创世种子，并通过只读权限隔离后的初始化连接灌入目标 PostgreSQL 实例：
```bash
# 由部署者提供参数与输出路径（自动剔除本地绝对路径、飞书私有 ID 与未过期租约）
export LOOPX_RUNTIME_ROOT="/path/to/operator-runtime-root"
export LOOPX_GOAL_ID="<goal-id>"
export LOOPX_TENANT_ID="<tenant-id>"
export LOOPX_SEED_OUTPUT="/tmp/genesis-seed.json"

node --no-warnings --experimental-sqlite --experimental-strip-types \
  -e '
  import {exportLocalToPgGenesisSeed} from "./loopx/control_plane/coordination/postgresql_authority_seed.ts";
  const result = await exportLocalToPgGenesisSeed({
    runtimeRoot: process.env.LOOPX_RUNTIME_ROOT!,
    goalId: process.env.LOOPX_GOAL_ID!,
    tenantId: process.env.LOOPX_TENANT_ID!,
    outputPath: process.env.LOOPX_SEED_OUTPUT!,
  });
  console.log("Genesis seed export result:", result.status, result.seed_digest);
'
```

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
