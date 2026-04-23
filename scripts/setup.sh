#!/bin/bash
# 环境初始化脚本

set -e

echo "========================================="
echo "  企业级多Agent办公平台 - 环境初始化"
echo "========================================="

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# 检查 Python 版本
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 Python3，请先安装 Python 3.11+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[信息] Python 版本: $PYTHON_VERSION"

# 创建虚拟环境
if [ ! -d ".venv" ]; then
    echo "[步骤1] 创建 Python 虚拟环境..."
    python3 -m venv .venv
    echo "[完成] 虚拟环境已创建"
else
    echo "[跳过] 虚拟环境已存在"
fi

# 激活虚拟环境
source .venv/bin/activate

# 安装依赖
echo "[步骤2] 安装 Python 依赖..."
pip install --upgrade pip
pip install -r requirements.txt
pip install -e ".[dev]"
echo "[完成] 依赖安装完成"

# 创建 .env 文件
if [ ! -f ".env" ]; then
    echo "[步骤3] 创建 .env 配置文件..."
    cp .env.example .env
    echo "[完成] .env 已创建，请根据实际情况修改配置"
else
    echo "[跳过] .env 已存在"
fi

echo ""
echo "========================================="
echo "  初始化完成！"
echo "========================================="
echo ""
echo "后续步骤："
echo "  1. 编辑 .env 文件，填入实际的 API Key 和数据库配置"
echo "  2. 启动 Redis:  docker run -d -p 6379:6379 redis:7-alpine"
echo "  3. 启动 PostgreSQL:  docker run -d -p 5432:5432 -e POSTGRES_DB=agent_platform -e POSTGRES_PASSWORD=postgres postgres:16-alpine"
echo "  4. 启动应用:  ./scripts/start.sh"
echo ""
