# 标准启动
./scripts/start.sh

# 跳过 MCP 端口检查
SKIP_MCP_PORTS=true ./scripts/start.sh

# 自定义 API 端口
API_PORT=9000 ./scripts/start.sh


## 项目访问与使用指南

### 一、环境准备

**1. 安装依赖**
```bash
./scripts/setup.sh
```

**2. 启动基础设施**（Redis + PostgreSQL）
```bash
docker run -d -p 6379:6379 redis:7-alpine
docker run -d -p 5432:5432 \
  -e POSTGRES_DB=agent_platform \
  -e POSTGRES_PASSWORD=postgres \
  postgres:16-alpine
```

**3. 配置 `.env`**
```bash
cp .env.example .env
# 编辑 .env，至少填入 DASHSCOPE_API_KEY（通义千问 API Key）
```

**4. 启动应用**
```bash
SKIP_MCP_PORTS=true ./scripts/start.sh
```

服务启动后访问 **http://localhost:8000**

---

### 二、API 接口总览

所有接口前缀为 `/api/v1`，Swagger 文档：**http://localhost:8000/docs**

#### 1. 认证 `/api/v1/auth`

| 方法 | 路径 | 说明 | 是否需要Token |
|------|------|------|:---:|
| POST | `/auth/login` | 用户登录 | 否 |
| POST | `/auth/logout` | 用户登出 | 是 |
| POST | `/auth/refresh` | 刷新令牌 | 否 |

**登录示例：**
```bash
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

**内置测试账号：**

| 用户ID | 密码 | 角色 |
|--------|------|------|
| admin001 | admin123 | admin |
| mgr001 | mgr123 | manager |
| hr001 | hr123 | hr_specialist |
| fin001 | fin123 | finance |
| emp001 | emp123 | employee |

#### 2. Agent 对话 `/api/v1/agent`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/agent/chat` | 同步对话（等完整响应） |
| POST | `/agent/chat/stream` | 流式对话（SSE 逐Token推送） |
| POST | `/agent/feedback` | 提交反馈（点赞/点踩） |
| GET | `/agent/feedback/stats` | 反馈统计 |
| GET | `/agent/feedback/stats/{agent}` | 指定Agent反馈统计 |

**同步对话示例：**
```bash
TOKEN="你的access_token"

curl -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "message": "帮我查一下张三的客户信息",
    "user_id": "admin001"
  }'
```

返回：
```json
{
  "session_id": "sess-xxx",
  "message": "已为您查询到张三的客户信息...",
  "agent_name": "CRM Agent",
  "intent": "crm_query",
  "collaboration_mode": "DIRECT"
}
```

**流式对话示例：**
```bash
curl -N http://localhost:8000/api/v1/agent/chat/stream \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "message": "帮我查一下张三的客户信息",
    "user_id": "admin001"
  }'
```

SSE 逐事件推送：
```
data: {"event": "session_id", "data": "sess-xxx"}
data: {"event": "intent", "data": "{\"intent\":\"crm_query\",\"confidence\":0.92,\"agent\":\"CRM Agent\",\"mode\":\"DIRECT\"}"}
data: {"event": "chunk", "data": "已"}
data: {"event": "chunk", "data": "为"}
data: {"event": "chunk", "data": "您"}
...
data: {"event": "status", "data": "completed"}
```

**多轮对话**（传入 session_id 继续）：
```bash
curl -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "message": "再看看他的商机进展",
    "session_id": "sess-xxx",
    "user_id": "admin001"
  }'
```

#### 3. 会话管理 `/api/v1/session`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/session/create` | 创建会话 |
| GET | `/session/{id}` | 获取会话信息 |
| GET | `/session/{id}/history` | 获取消息历史 |
| POST | `/session/{id}/archive` | 归档到L3长期存储 |
| GET | `/session/user/{uid}/history` | 查询用户归档会话 |
| DELETE | `/session/{id}` | 删除会话 |

#### 4. 系统管理 `/api/v1/admin`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/health` | 基础健康检查 |
| GET | `/admin/health/detail` | 深度健康检查 |
| GET | `/admin/mcp/status` | MCP服务状态 |
| GET | `/admin/failover/status` | 故障转移状态 |
| GET | `/admin/canary/flags` | 灰度功能开关 |
| POST | `/admin/canary/rollout` | 更新灰度比例 |
| POST | `/admin/canary/whitelist` | 更新白名单 |
| POST | `/admin/canary/toggle` | 启用/禁用功能 |
| GET | `/admin/metrics/summary` | 运营指标汇总 |
| GET | `/admin/token/usage/{uid}` | 用户Token用量 |
| GET | `/admin/token/budget/{uid}` | Token预算检查 |
| GET | `/admin/audit/logs` | 查询审计日志 |
| POST | `/admin/audit/flush` | 刷新审计缓冲区 |

**健康检查（无需Token）：**
```bash
curl http://localhost:8000/api/v1/admin/health
```

#### 5. Prometheus 指标

```bash
curl http://localhost:8000/metrics
```

---

### 三、典型使用流程

```
1. 登录获取 Token
   POST /auth/login  →  access_token

2. 发起对话（新会话）
   POST /agent/chat  { message, user_id }  →  session_id + 回复

3. 多轮对话（继续会话）
   POST /agent/chat  { message, session_id, user_id }  →  回复

4. 查看历史
   GET /session/{id}/history

5. 归档会话
   POST /session/{id}/archive

6. 提交反馈
   POST /agent/feedback  { session_id, message_index, feedback_type }
```

### 四、交互式文档

启动后访问 FastAPI 自动生成的 Swagger UI：

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

在 Swagger UI 中可直接调试所有接口，点击右上角 `Authorize` 输入 `Bearer <access_token>` 即可认证。