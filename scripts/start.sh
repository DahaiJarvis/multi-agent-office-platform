#!/bin/bash
# 应用启动脚本

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# 激活虚拟环境
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# 加载环境变量
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi

# 从环境变量获取配置
HOST=${API_HOST:-0.0.0.0}
PORT=${API_PORT:-8000}
WORKERS=${API_WORKERS:-1}
ENV=${ENVIRONMENT:-development}

echo "========================================="
echo "  企业级多Agent办公平台 - 启动服务"
echo "========================================="
echo "  环境: $ENV"
echo "  地址: $HOST:$PORT"
echo "  进程: $WORKERS"
echo "========================================="

if [ "$ENV" = "development" ]; then
    echo "[开发模式] 启动热重载服务..."
    uvicorn api.main:app --host "$HOST" --port "$PORT" --reload
else
    echo "[生产模式] 启动服务..."
    uvicorn api.main:app --host "$HOST" --port "$PORT" --workers "$WORKERS"
fi
