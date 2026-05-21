#!/bin/bash
# MCP 服务 Mock 模式一键启动脚本
# 在本地开发时使用，启动所有 MCP 服务器（Mock 模式）
# 使用方式: bash scripts/start_mcp_mock.sh [start|stop|status]

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${PROJECT_DIR}/logs"
PID_DIR="${PROJECT_DIR}/.mcp_pids"

# MCP 服务列表: 名称 模块路径 端口
MCP_SERVICES=(
    "oa mcp_servers.oa_server.server 9001"
    "email mcp_servers.email_server.server 9002"
    "calendar mcp_servers.calendar_server.server 9003"
    "crm mcp_servers.crm_server.server 9004"
    "approval mcp_servers.approval_server.server 9005"
    "im mcp_servers.im_server.server 9006"
    "doc mcp_servers.doc_server.server 9007"
    "hr mcp_servers.hr_server.server 9008"
    "finance mcp_servers.finance_server.server 9009"
    "knowledge mcp_servers.knowledge_server.server 9010"
    "web_search mcp_servers.web_search_server.server 9011"
)

mkdir -p "${LOG_DIR}" "${PID_DIR}"

start_service() {
    local name=$1
    local module=$2
    local port=$3
    local pid_file="${PID_DIR}/${name}.pid"
    local log_file="${LOG_DIR}/mcp_${name}.log"

    if [ -f "${pid_file}" ]; then
        local old_pid
        old_pid=$(cat "${pid_file}")
        if kill -0 "${old_pid}" 2>/dev/null; then
            echo "[跳过] ${name} 已在运行 (PID: ${old_pid}, Port: ${port})"
            return 0
        else
            rm -f "${pid_file}"
        fi
    fi

    echo "[启动] ${name} -> 端口 ${port}"
    MCP_MOCK_MODE=true python -m ${module} > "${log_file}" 2>&1 &
    local pid=$!
    echo "${pid}" > "${pid_file}"

    # 等待服务启动
    sleep 1
    if kill -0 "${pid}" 2>/dev/null; then
        echo "[成功] ${name} 已启动 (PID: ${pid}, Port: ${port})"
    else
        echo "[失败] ${name} 启动失败，请查看日志: ${log_file}"
        rm -f "${pid_file}"
    fi
}

stop_service() {
    local name=$1
    local pid_file="${PID_DIR}/${name}.pid"

    if [ -f "${pid_file}" ]; then
        local pid
        pid=$(cat "${pid_file}")
        if kill -0 "${pid}" 2>/dev/null; then
            echo "[停止] ${name} (PID: ${pid})"
            kill "${pid}" 2>/dev/null || true
            sleep 0.5
            # 强制终止
            kill -9 "${pid}" 2>/dev/null || true
        fi
        rm -f "${pid_file}"
    fi
}

start_all() {
    echo "=========================================="
    echo " 启动所有 MCP 服务 (Mock 模式)"
    echo " MCP_MOCK_MODE=true"
    echo "=========================================="
    echo ""
    export MCP_MOCK_MODE=true

    for service_info in "${MCP_SERVICES[@]}"; do
        read -r name module port <<< "${service_info}"
        start_service "${name}" "${module}" "${port}"
    done

    echo ""
    echo "=========================================="
    echo " 所有 MCP 服务已启动 (Mock 模式)"
    echo " 日志目录: ${LOG_DIR}/"
    echo " 停止服务: bash scripts/start_mcp_mock.sh stop"
    echo "=========================================="
}

stop_all() {
    echo "=========================================="
    echo " 停止所有 MCP 服务"
    echo "=========================================="
    echo ""

    for service_info in "${MCP_SERVICES[@]}"; do
        read -r name module port <<< "${service_info}"
        stop_service "${name}"
    done

    echo ""
    echo "[完成] 所有 MCP 服务已停止"
}

show_status() {
    echo "=========================================="
    echo " MCP 服务状态"
    echo "=========================================="
    echo ""

    for service_info in "${MCP_SERVICES[@]}"; do
        read -r name module port <<< "${service_info}"
        local pid_file="${PID_DIR}/${name}.pid"

        if [ -f "${pid_file}" ]; then
            local pid
            pid=$(cat "${pid_file}")
            if kill -0 "${pid}" 2>/dev/null; then
                echo "[运行中] ${name} (PID: ${pid}, Port: ${port})"
            else
                echo "[已停止] ${name} (Port: ${port}) - 进程已退出"
            fi
        else
            echo "[未启动] ${name} (Port: ${port})"
        fi
    done

    echo ""
}

case "${1}" in
    start)
        start_all
        ;;
    stop)
        stop_all
        ;;
    restart)
        stop_all
        echo ""
        sleep 1
        start_all
        ;;
    status)
        show_status
        ;;
    *)
        echo "用法: bash $0 {start|stop|restart|status}"
        echo ""
        echo "  start   - 启动所有 MCP 服务 (Mock 模式)"
        echo "  stop    - 停止所有 MCP 服务"
        echo "  restart - 重启所有 MCP 服务"
        echo "  status  - 查看服务运行状态"
        exit 1
        ;;
esac
