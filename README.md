# 企业级多Agent办公平台

基于 AutoGen 多Agent编排与 MCP 协议，将自然语言指令自动路由至11大企业业务系统并完成操作执行，实现"说一句话，办所有事"的AI原生办公自动化。

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
│  MCP 服务层 (11个领域服务)                            │
│  OA / 邮件 / 日历 / CRM / IM / 文档 / HR / 财务 /    │
│  知识库 / 审批 / 网络搜索                             │
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
| Agent 编排 | `agent/` | 意图分类、Agent路由、多Agent协作、熔断器、会话管理、工具注册 |
| API 服务 | `api/` | FastAPI 路由、中间件（认证/限流/CSRF/追踪）、数据模型 |
| MCP 服务 | `mcp_servers/` | 11个领域MCP服务 + 注册中心 |
| 安全模块 | `security/` | 认证/SSO/加密/PII检测/注入检测/审计/合规/数据驻留/多租户/幻觉检测 |
| 可观测性 | `observability/` | Prometheus指标、OpenTelemetry追踪、Langfuse、结构化日志、业务分析 |
| 部署配置 | `deploy/` | Docker/K8s清单、Nginx、Prometheus/Grafana、灰度发布、高可用、多区域 |
| 网关适配 | `gateway/` | 渠道适配器（多渠道接入） |
| 配置中心 | `config/` | Agent能力配置、Prompt模板 |

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
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "查看我的待审批列表", "user_id": "admin001"}'

# 流式对话（SSE）
curl -N http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "查看我的待审批列表", "user_id": "admin001"}'

# 多轮对话（传入 session_id）
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "再看看他的商机进展", "session_id": "sess-xxx", "user_id": "admin001"}'
```

### 接口总览

| 模块 | 路径前缀 | 说明 |
|------|---------|------|
| 认证 | `/api/v1/auth` | 登录/登出/刷新令牌/SSO回调 |
| 对话 | `/api/v1/chat` | 同步/流式对话 |
| 反馈 | `/api/v1/feedback` | 对话反馈收集 |
| 任务 | `/api/v1/task` | 异步任务管理 |
| 会话管理 | `/api/v1/session` | 创建/查询/归档/删除 |
| 系统管理 | `/api/v1/admin` | 健康检查/灰度/审计/指标 |
| 审批流程 | `/api/v1/approval` | 审批操作与查询 |
| 工作流 | `/api/v1/workflow` | 工作流定义与执行 |
| 插件管理 | `/api/v1/plugin` | 插件注册与配置 |
| 知识库代理 | `/api/v1/knowledge` | 知识库代理（IDA） |
| 多租户 | `/api/v1/tenant` | 租户管理 |
| 合规 | `/api/v1/compliance` | 合规审计 |
| Agent构建 | `/api/v1/agent-builder` | 自定义Agent创建与管理 |
| 技能管理 | `/api/v1/skill` | 技能注册与绑定 |
| 嵌入向量 | `/api/v1/embed` | 文本嵌入服务 |
| 多模态 | `/api/v1/multimodal` | 图像/音频处理 |
| 搜索 | `/api/v1/search` | 语义搜索服务 |
| 分析 | `/api/v1/analytics` | 业务数据分析 |
| Prompt模板 | `/api/v1/prompt-template` | Prompt模板管理 |
| SLA | `/api/v1/sla` | 服务等级协议监控 |
| 区域管理 | `/api/v1/region` | 数据区域管理 |
| 调度器 | `/api/v1/scheduler` | 定时任务调度 |
| Token监控 | `/api/v1/token-monitor` | Token使用监控 |
| 原生工具 | `/api/v1/native-tool` | 原生工具调用 |
| JWKS | `/.well-known/jwks.json` | JWT公钥端点 |
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
# 或业务仪表盘 deploy/monitoring/grafana_business_dashboard.json
```

## 项目结构

```
multi-agent-office-platform/
├── agent/                  # Agent 编排层
│   ├── adapters/           # MCP 适配器
│   ├── agents/             # Agent 实现（Supervisor/Domain/Reviewer/AgentBuilder）
│   ├── core/               # 核心模块
│   │   ├── common/         # 通用工具（国际化/无障碍/多模态/安全清洗）
│   │   ├── infrastructure/ # 基础设施（Redis/事件总线/熔断器/分布式锁/插件系统）
│   │   ├── mcp/            # MCP集成（工具注册/验证/追踪）
│   │   ├── model/          # 模型管理（客户端/路由/Token预算）
│   │   ├── observability/  # 可观测性（审计/反馈/SLA）
│   │   ├── performance/    # 性能优化（缓存/连接池/语义缓存）
│   │   ├── prompt/         # Prompt管理（模板库/注册中心）
│   │   ├── session/        # 会话管理（上下文/长期记忆）
│   │   ├── skill/          # 技能系统（适配器/解析器/能力卡）
│   │   └── workflow/       # 工作流引擎（路由/审批/人工确认/长任务/调度）
│   ├── guardrails/         # 安全护栏
│   ├── teams/              # 多Agent协作与路由
│   └── tools/              # 内置工具集（文本/文档/搜索/RAG/多模态/数据/审计等）
├── api/                    # API 服务层
│   ├── middleware/         # 中间件（认证/限流/CSRF/追踪/熔断）
│   ├── models/             # 请求/响应模型
│   └── routes/             # 路由定义
├── config/                 # 配置文件
│   ├── capabilities/       # Agent能力配置（YAML）
│   └── prompts/            # Prompt 模板（YAML）
├── deploy/                 # 部署配置
│   ├── docker/             # Dockerfile + docker-compose
│   ├── k8s/                # Kubernetes 清单
│   ├── monitoring/         # Prometheus/Grafana/告警规则/仪表盘
│   └── nginx/              # Nginx 反向代理
├── frontend/               # Vue3 前端
│   └── src/                # 源码（views/stores/api/components）
├── gateway/                # 网关适配层
│   └── adapters/           # 渠道适配器
├── mcp_servers/            # MCP 领域服务
│   ├── oa_server/          # OA审批服务
│   ├── email_server/       # 邮件服务
│   ├── calendar_server/    # 日历服务
│   ├── crm_server/         # CRM服务
│   ├── im_server/          # 即时通讯服务
│   ├── doc_server/         # 文档服务
│   ├── hr_server/          # 人事服务
│   ├── finance_server/     # 财务服务
│   ├── knowledge_server/   # 知识库服务
│   ├── approval_server/    # 审批服务
│   ├── web_search_server/  # 网络搜索服务
│   ├── registry.py         # MCP注册中心
│   └── base.py             # MCP服务基类
├── observability/          # 可观测性
│   ├── metrics.py          # Prometheus指标
│   ├── tracing.py          # OpenTelemetry追踪
│   ├── logging_config.py   # 结构化日志
│   └── business_analytics.py # 业务分析
├── scripts/                # 运维脚本
│   ├── setup.sh            # 项目初始化
│   ├── start.sh            # 启动服务
│   ├── deploy.sh           # 部署脚本
│   ├── health_check.sh     # 健康检查
│   └── start_mcp_mock.sh   # MCP模拟服务启动
├── security/               # 安全模块
│   ├── auth.py             # 认证
│   ├── sso.py              # SSO集成
│   ├── encryption.py       # 加密
│   ├── pii_detection.py    # PII检测
│   ├── injection_detection.py # 注入检测
│   ├── hallucination_detection.py # 幻觉检测
│   ├── guardrails.py       # 安全护栏
│   ├── audit.py            # 审计
│   ├── compliance.py       # 合规
│   ├── data_residency.py   # 数据驻留
│   ├── tenant.py           # 多租户
│   ├── permission.py       # 权限管理
│   ├── desensitize.py      # 数据脱敏
│   └── user_store.py       # 用户存储
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

## MCP 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| MCP Registry | 9099 | MCP 注册中心 |
| OA Server | 9001 | OA审批服务 |
| Email Server | 9002 | 邮件服务 |
| Calendar Server | 9003 | 日历服务 |
| CRM Server | 9004 | CRM服务 |
| IM Server | 9006 | 即时通讯服务 |
| Doc Server | 9007 | 文档服务 |
| HR Server | 9008 | 人事服务 |
| Finance Server | 9009 | 财务服务 |
| Knowledge Server | 9010 | 知识库服务 |
| Web Search Server | 9011 | 网络搜索服务 |

## 智能文档助手（IDA）集成

平台支持与智能文档助手（IDA）集成，提供文档智能处理能力：

- **API 代理**: 通过 `/api/v1/knowledge` 代理 IDA 服务
- **MCP 集成**: 通过 SSE 协议连接 IDA MCP 服务
- **跨系统认证**: 支持 RSA 非对称密钥和 JWKS 两种认证模式

配置示例：

```bash
# IDA 服务地址
IDA_BACKEND_URL=http://localhost:5000
IDA_MCP_SSE_URL=http://localhost:9010/sse

# MCP 通信密钥
MCP_API_KEY=your-secure-mcp-api-key

# 认证模式：legacy（映射Token）/ direct（透传Token）
IDA_AUTH_MODE=legacy
```
