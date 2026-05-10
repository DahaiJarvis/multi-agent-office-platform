# 企业级多Agent办公平台

基于 AutoGen 多Agent编排与 MCP 协议，将自然语言指令自动路由至9大企业业务系统并完成操作执行，实现"说一句话，办所有事"的AI原生办公自动化。

## 技术栈

- **AI 编排**: AutoGen + 通义千问（多Agent编排与意图路由）
- **工具协议**: MCP Protocol（Agent与工具层解耦）
- **后端服务**: FastAPI + Pydantic（后端API与数据校验）
- **可观测性**: OpenTelemetry + Langfuse（AI调用链追踪与质量评估）
- **韧性保障**: Circuit Breaker + 降级策略（LLM调用链路容错）
- **安全认证**: OIDC + PyJWT（企业SSO集成与跨系统认证）
- **前端交互**: Vue3 + Pinia（前端交互与状态管理）

## 项目架构

```
┌─────────────────────────────────────────────────────┐
│  前端 (Vue3 + Vite + TypeScript)                     │
├─────────────────────────────────────────────────────┤
│  API 网关 (Nginx → FastAPI + Uvicorn)               │
├─────────────────────────────────────────────────────┤
│  Agent 编排层 (AutoGen + MCP)                        │
│  意图分类 → Agent路由 → 工具调用 → 结果聚合           │
├─────────────────────────────────────────────────────┤
│  MCP 服务层 (9个领域服务)                             │
│  OA / 邮件 / 日历 / CRM / IM / 文档 / HR / 财务 / 知识库 │
├─────────────────────────────────────────────────────┤
│  基础设施 (PostgreSQL 16 / Redis 7 / K8s)            │
├─────────────────────────────────────────────────────┤
│  可观测性 (OpenTelemetry + Prometheus + Langfuse)     │
├─────────────────────────────────────────────────────┤
│  安全 (OIDC/SSO + AES-256 + PII检测 + 数据驻留)      │
├─────────────────────────────────────────────────────┤
│  韧性 (Circuit Breaker + 降级 + 多区域灾备)           │
└─────────────────────────────────────────────────────┘
```

## 核心模块

| 模块 | 路径 | 说明 |
|------|------|------|
| Agent 编排 | `agent/` | 意图分类、Agent路由、多Agent协作、熔断器、会话管理 |
| API 服务 | `api/` | FastAPI 路由、中间件（认证/限流/CSRF/追踪）、数据模型 |
| MCP 服务 | `mcp_servers/` | 9个领域MCP服务 + 注册中心 |
| 安全模块 | `security/` | 认证/SSO/加密/PII检测/注入检测/审计/合规/数据驻留/多租户 |
| 可观测性 | `observability/` | Prometheus指标、OpenTelemetry追踪、Langfuse、结构化日志 |
| 部署配置 | `deploy/` | Docker/K8s清单、Nginx、Prometheus/Grafana、灰度发布、高可用 |
| 前端 | `frontend/` | Vue3 SPA、对话界面、管理后台 |

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 20+
- Redis 7+
- PostgreSQL 16+
- Docker（可选，用于运行基础设施）

### 1. 初始化项目

```bash
./scripts/setup.sh
```

该脚本会自动创建虚拟环境、安装依赖、生成 `.env` 配置文件。

### 2. 启动基础设施

```bash
# Redis
docker run -d -p 6379:6379 redis:7-alpine

# PostgreSQL
docker run -d -p 5432:5432 \
  -e POSTGRES_DB=agent_platform \
  -e POSTGRES_PASSWORD=postgres \
  postgres:16-alpine
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，至少填入以下配置：

```bash
# 必填: 通义千问 API Key
DASHSCOPE_API_KEY=sk-your-dashscope-api-key

# 数据库密码（与 Docker 启动参数一致）
POSTGRES_PASSWORD=postgres
```

### 4. 启动应用

```bash
# 标准启动（后端 + 前端）
./scripts/start.sh

# 跳过 MCP 端口检查
SKIP_MCP_PORTS=true ./scripts/start.sh

# 跳过前端启动
SKIP_FRONTEND=true ./scripts/start.sh

# 自定义 API 端口
API_PORT=9000 ./scripts/start.sh
```

启动成功后：

| 服务 | 地址 |
|------|------|
| 前端界面 | http://localhost:3000 |
| API 服务 | http://localhost:8000 |
| Swagger 文档 | http://localhost:8000/docs |

### Docker Compose 一键启动

```bash
cd deploy/docker
docker compose up -d
```

## 内置测试账号

| 用户ID | 密码 | 角色 | 部门 |
|--------|------|------|------|
| admin001 | admin123 | admin | 技术部 |
| mgr001 | mgr123 | manager | 产品部 |
| hr001 | hr123 | hr_specialist | 人力资源部 |
| fin001 | fin123 | finance | 财务部 |
| emp001 | emp123 | employee | 运营部 |

> 测试账号在首次启动时自动写入数据库，密码使用 bcrypt 哈希存储。

## API 接口

所有接口前缀为 `/api/v1`，完整文档见 Swagger UI。

### 认证

```bash
# 登录
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"user_id": "admin001", "password": "admin123"}'
```

返回：

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user_id": "admin001",
  "roles": ["admin"]
}
```

### Agent 对话

```bash
TOKEN="你的access_token"

# 同步对话
curl -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "查看我的待审批列表", "user_id": "admin001"}'

# 流式对话（SSE）
curl -N http://localhost:8000/api/v1/agent/chat/stream \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "查看我的待审批列表", "user_id": "admin001"}'

# 多轮对话（传入 session_id）
curl -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "再看看他的商机进展", "session_id": "sess-xxx", "user_id": "admin001"}'
```

### 接口总览

| 模块 | 路径前缀 | 说明 |
|------|---------|------|
| 认证 | `/api/v1/auth` | 登录/登出/刷新令牌 |
| Agent 对话 | `/api/v1/agent` | 同步/流式对话、反馈 |
| 会话管理 | `/api/v1/session` | 创建/查询/归档/删除 |
| 系统管理 | `/api/v1/admin` | 健康检查/灰度/审计/指标 |
| 审批流程 | `/api/v1/approval` | 审批操作与查询 |
| 工作流 | `/api/v1/workflow` | 工作流定义与执行 |
| 插件管理 | `/api/v1/plugin` | 插件注册与配置 |
| 知识库 | `/api/v1/knowledge` | 知识库代理（IDA） |
| 多租户 | `/api/v1/tenant` | 租户管理 |
| 合规 | `/api/v1/compliance` | 合规审计 |
| Prometheus | `/metrics` | 监控指标 |

## 部署

### Kubernetes

```bash
# 应用 K8s 清单
kubectl apply -f deploy/k8s/configmap.yaml
kubectl apply -f deploy/k8s/agent-deployment.yaml
kubectl apply -f deploy/k8s/mcp-deployments.yaml
```

### 监控

```bash
# Prometheus + Alertmanager
kubectl apply -f deploy/monitoring/prometheus.yml
kubectl apply -f deploy/monitoring/alert_rules.yml

# 导入 Grafana 仪表盘
# Grafana → Dashboards → Import → 粘贴 deploy/monitoring/grafana_dashboard.json
```

## 项目结构

```
multi-agent-office-platform/
├── agent/                  # Agent 编排层
│   ├── adapters/           # MCP 适配器
│   ├── agents/             # Agent 实现（Supervisor/Domain/Reviewer）
│   ├── core/               # 核心模块
│   │   └── performance/    # 性能优化（缓存/连接池/语义缓存）
│   ├── guardrails/         # 安全护栏
│   └── teams/              # 多Agent协作与路由
├── api/                    # API 服务层
│   ├── middleware/          # 中间件（认证/限流/CSRF/追踪）
│   ├── models/             # 请求/响应模型
│   └── routes/             # 路由定义
├── config/                 # 配置文件
│   └── prompts/            # Prompt 模板（YAML）
├── deploy/                 # 部署配置
│   ├── docker/             # Dockerfile + docker-compose
│   ├── k8s/                # Kubernetes 清单
│   ├── monitoring/         # Prometheus/Grafana/告警规则
│   └── nginx/              # Nginx 反向代理
├── frontend/               # Vue3 前端
│   └── src/                # 源码（views/stores/api/components）
├── mcp_servers/            # MCP 领域服务
├── observability/          # 可观测性（指标/追踪/日志）
├── scripts/                # 运维脚本（启动/部署/健康检查）
├── security/               # 安全模块
└── tests/                  # 测试
    ├── unit/               # 单元测试
    ├── integration/        # 集成测试
    ├── e2e/                # 端到端测试
    ├── performance/        # 性能测试（Locust）
    └── mocks/              # LLM Mock 客户端
```

## 开发

### 代码质量

```bash
# 格式化
ruff format .

# Lint 检查
ruff check .

# 类型检查
mypy --ignore-missing-imports api/ agent/ security/

# 运行测试
pytest tests/ -v --cov=api --cov=agent --cov=security
```

### 安全扫描

```bash
# 代码安全扫描
bandit -r api/ agent/ security/ -ll

# 依赖漏洞检查
safety check -r requirements.txt

# 密钥泄露检测
detect-secrets scan --all-files
```

