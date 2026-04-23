#!/bin/bash
# 健康检查脚本

set -e

HOST=${API_HOST:-localhost}
PORT=${API_PORT:-8000}
HEALTH_URL="http://${HOST}:${PORT}/api/v1/admin/health"

echo "检查服务健康状态: $HEALTH_URL"

RESPONSE=$(curl -s -w "\n%{http_code}" "$HEALTH_URL" 2>/dev/null || echo -e "\n000")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
    echo "[健康] 服务正常运行"
    echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "$BODY"
    exit 0
else
    echo "[异常] 服务不可用 (HTTP $HTTP_CODE)"
    exit 1
fi
