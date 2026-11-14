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
|       ruiteng.macbook       |               |         lusen.cloud         |
|  (macOS Apple Silicon dev)  |               |  (Volcengine ECS / Ark LLM) |
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
|  | - 同地域低延迟内网交互                                                 |  |
|  | - 复用本机已就绪凭据 (`operations-ark-video.json`) 驱动廉价 Worker      |  |
|  +---------------------------------------------------------------------+  |
+---------------------------------------------------------------------------+
```

### 核心设计原则
1. **网络零公网暴露数据库**：PostgreSQL 实例仅绑定 VPC 内网子网，安全组仅对网关 ECS 开放 5432 端口；严禁绑定公网弹性 IP (EIP)。
2. **最小受限角色装配 (`loopx_app`)**：网关服务使用专用的 `loopx_app` 角色连接数据库，仅具备 `SELECT, INSERT, UPDATE` 必要权限，禁止 `DROP TABLE`, `TRUNCATE`, `ALTER ROLE` 等管理员特权。
3. **端到端 TLS 传输认证**：外部客户端经由 TLS 1.3 连接网关，凭据在网关层做安全哈希对比（`timingSafeEqual`），绝不向数据库传递或持久化明文凭据。
4. **同地域方舟模型协同**：利用同在 `cn-beijing` 的火山方舟大模型/视频 API 端点（`ark.cn-beijing.volces.com`），保证 Worker 节点在同一云内网络低延迟调用模型。

---

## 2. 火山引擎 cn-beijing 资源规格清单

| 资源类别 | 建议规格 / 配置 | 计费类型建议 | 用途 |
|---|---|---|---|
| **VPC** | CIDR: `172.16.0.0/16`, Region: `cn-beijing` | 免费 | 隔离协同云上内网 |
| **子网 (Subnet)** | 可用区: `cn-beijing-a`, CIDR: `172.16.1.0/24` | 免费 | 数据库与网关部署网段 |
| **安全组 (Gateway)** | 入方向: 仅放通 443 (TLS) 给授权 IP；出方向: 允许内网访问 RDS | 免费 | 网关流量防护 |
| **安全组 (RDS)** | 入方向: 仅放通 5432 来自网关安全组；严禁任何 `0.0.0.0/0` | 免费 | 数据库访问收敛 |
| **轻量网关 ECS** | 规格: `ecs.g3i.large` (2C4G) 或 `ecs.c3i.large` (2C4G) | 按量计费 / 抢占式 | 运行 Caddy + RemoteAuthorityService |
| **RDS for PG** | 引擎: PostgreSQL 16，规格: `rds.pg.s1.small` (1C2G) 或通用型 | 按量计费 (按需开启) | 承载协同权威状态与检查点 |
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
   - 定义 VPC、子网、安全组、RDS PostgreSQL 16 实例与网关 ECS 的参数化配置。
   - 所有变量均提供安全默认值（如北京地域 `cn-beijing`），并设置 `prevent_destroy = true` 避免误删。
2. **TLS 网关配置**：`gateway-caddy-tls.Caddyfile`
   - Caddy 自动化 TLS 反向代理至本地 Node 权威服务（`https://127.0.0.1:8443`）。
   - 包含请求大小限制（16KB）、连接超时约束与 HSTS 安全头。
3. **网关守护进程服务**：`remote-authority-systemd.service`
   - 以独立低特权系统用户运行 `examples/coordination/remote-authority-service.ts`。
   - 启用 `DynamicUser=yes`, `ProtectSystem=strict`, `PrivateTmp=true` 等 systemd 安全加固。

---

## 5. 多机分治与双人协同联调流程

### 步骤 1：本地零成本预演 (Colima / Docker)
在向火山引擎上线前，必须先在本机运行 Colima 端到端彩排套件验证：
```bash
# 模拟模式（无任何容器开销，10ms 内全量完成 8 个核心切片校验）
./examples/coordination/colima-pg16-e2e-rehearsal.sh --simulate

# 容器模式（启动隔离 postgres:16-alpine 容器做真实 SQL 联调）
./examples/coordination/colima-pg16-e2e-rehearsal.sh --rehearsal
```

### 步骤 2：创世种子脱敏与灌入
对需要共享给云端的 Goal（如 `loopx-quality-review`），使用 Stage 2B 工具导出脱敏创世种子并灌入云端 RDS：
```bash
# 导出并校验隐私边界（自动剔除本地绝对路径、飞书私有 ID 与未过期租约）
node --no-warnings --experimental-strip-types \
  -e '
  import {exportLocalToPgGenesisSeed} from "./loopx/control_plane/coordination/postgresql_authority_seed.ts";
  const result = await exportLocalToPgGenesisSeed({
    runtimeRoot: process.env.HOME + "/.loopx",
    goalId: "loopx-quality-review",
    tenantId: "tenant-prod",
    outputPath: "/tmp/genesis-seed-canary.json"
  });
  console.log("Genesis seed export result:", result.status, result.seed_digest);
'
```

### 步骤 3：多机客户端并发联调
- 本机操作员 (`ruiteng.macbook`)：
  ```bash
  LOOPX_AUTHORITY_CREDENTIAL="<ruiteng-bearer-secret>" \
  node --no-warnings --experimental-strip-types \
    examples/coordination/remote-authority-client.ts \
    --endpoint "https://gateway.loopx.bj.volces.com" \
    --ca /etc/ssl/certs/loopx-ca.pem
  ```
- 云端 Worker (`lusen.cloud`)：
  配合 `operations-ark-video.json` 中的火山方舟 API Key，在获取租约后执行测试/评审，随后向网关回传 `progress` 与带凭证证据的 `complete` 指令。
- 租约冲突与超期接管：
  若云端 Worker 节点因网络断连或故障未续租，租约到达 TTL 后，本地操作员可无阻塞接管继续推进，彻底消除任务悬挂风险。
