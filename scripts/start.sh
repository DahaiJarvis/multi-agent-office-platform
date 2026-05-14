#!/bin/bash
# 项目启动脚本
# 功能: 检查端口占用 -> 释放应用端口 -> 检查依赖可用性 -> 启动后端+前端
# 兼容: macOS / Linux

set -euo pipefail

# ==================== 颜色与日志 ====================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${BLUE}[STEP]${NC}  $1"; }

# ==================== 项目路径 ====================

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# ==================== 端口配置 ====================

# 应用端口: 后端 API 服务端口，被占用时需要释放
APP_PORTS=(
    "8000:Agent API"
)

# 前端端口: 不强制释放占用进程（可能为 IDE 等系统进程），不可用时自动换端口
FRONTEND_PORT_DEFAULT=3000

# 依赖端口: 基础设施服务端口，仅检查可用性，不主动终止
# (Redis/PostgreSQL 等由系统服务管理，自动重启，杀掉无意义)
DEP_PORTS=(
    "6379:Redis"
    "5432:PostgreSQL"
)

# IDA 智能文档助手端口（独立服务，仅检查可用性，不主动终止）
IDA_PORTS=(
    "5000:IDA Backend"
)

# MCP 服务端口: MCP 子服务绑定的端口，被占用时需要释放
MCP_PORTS=(
    "9001:OA MCP"
    "9002:Email MCP"
    "9003:Calendar MCP"
    "9004:CRM MCP"
    "9005:Approval MCP"
    "9006:IM MCP"
    "9007:Doc MCP"
    "9008:HR MCP"
    "9009:Finance MCP"
    "9010:Knowledge MCP"
    "9011:Web Search MCP"
    "9099:MCP Registry"
)

# ==================== 子进程 PID 追踪 ====================

API_PID=""
FRONTEND_PID=""
ACTUAL_FRONTEND_PORT=""
MCP_PIDS=()

# ==================== 工具函数 ====================

# 检测操作系统类型
detect_os() {
    local uname_out
    uname_out="$(uname -s)"
    case "${uname_out}" in
        Linux*)     echo "linux";;
        Darwin*)    echo "macos";;
        CYGWIN*)    echo "cygwin";;
        MINGW*)     echo "mingw";;
        *)          echo "unknown";;
    esac
}

# 检查端口是否空闲
# 参数: $1 = 端口号
# 返回: 0=空闲, 1=被占用
is_port_free() {
    local port="$1"
    local pid
    pid="$(find_pid_by_port "${port}")"
    [ -z "$pid" ]
}

# 从指定端口开始，查找第一个空闲端口
# 参数: $1 = 起始端口号, $2 = 最大尝试次数(默认20)
# 输出: 可用端口号（未找到则输出空）
find_free_port() {
    local start_port="$1"
    local max_tries="${2:-20}"
    local current="${start_port}"
    local end_port=$((start_port + max_tries))

    while [ "${current}" -lt "${end_port}" ]; do
        if is_port_free "${current}"; then
            echo "${current}"
            return 0
        fi
        current=$((current + 1))
    done

    return 1
}

# 查找占用指定端口的进程 PID
# 参数: $1 = 端口号
# 输出: PID（无进程则输出空）
find_pid_by_port() {
    local port="$1"
    local os
    os="$(detect_os)"
    local pid=""

    case "${os}" in
        linux)
            pid=$(ss -tlnp 2>/dev/null | grep ":${port} " | grep -oP 'pid=\K[0-9]+' | head -1)
            if [ -z "$pid" ]; then
                pid=$(lsof -t -i:"${port}" 2>/dev/null | head -1)
            fi
            ;;
        macos)
            pid=$(lsof -t -i:"${port}" 2>/dev/null | head -1)
            ;;
        *)
            pid=$(lsof -t -i:"${port}" 2>/dev/null | head -1)
            ;;
    esac

    echo "${pid}"
}

# 获取进程的命令行信息
# 参数: $1 = PID
get_process_info() {
    local pid="$1"
    if [ -z "$pid" ]; then
        echo ""
        return
    fi

    local os
    os="$(detect_os)"
    case "${os}" in
        linux)
            ps -p "${pid}" -o args= 2>/dev/null | head -c 120 || echo ""
            ;;
        macos)
            ps -p "${pid}" -o command= 2>/dev/null | head -c 120 || echo ""
            ;;
        *)
            ps -p "${pid}" -o args= 2>/dev/null | head -c 120 || echo ""
            ;;
    esac
}

# 终止指定 PID 的进程
# 参数: $1 = PID, $2 = 端口描述
kill_process() {
    local pid="$1"
    local desc="$2"

    if [ -z "$pid" ]; then
        return 1
    fi

    local proc_info
    proc_info="$(get_process_info "${pid}")"

    log_warn "端口 ${desc} 被进程占用: PID=${pid}"
    if [ -n "${proc_info}" ]; then
        log_warn "  进程详情: ${proc_info}"
    fi

    # 先尝试优雅终止 (SIGTERM)
    log_info "发送 SIGTERM 信号终止进程 ${pid}..."
    kill "${pid}" 2>/dev/null || true

    # 等待进程退出，最多 5 秒
    local wait_count=0
    while [ ${wait_count} -lt 10 ]; do
        if ! kill -0 "${pid}" 2>/dev/null; then
            log_info "进程 ${pid} 已正常退出"
            return 0
        fi
        sleep 0.5
        wait_count=$((wait_count + 1))
    done

    # 优雅终止失败，强制杀死 (SIGKILL)
    log_warn "进程 ${pid} 未响应 SIGTERM，发送 SIGKILL 强制终止..."
    kill -9 "${pid}" 2>/dev/null || true
    sleep 0.5

    if ! kill -0 "${pid}" 2>/dev/null; then
        log_info "进程 ${pid} 已被强制终止"
        return 0
    else
        log_error "无法终止进程 ${pid}，请手动处理"
        return 1
    fi
}

# ==================== 端口检查 ====================

# 检查并释放应用端口（被占用时终止占用进程）
# 参数: 端口列表 "port:desc" ...
# 返回: 0=全部就绪, 1=存在无法释放的端口
check_and_free_ports() {
    local port_list=("$@")
    local has_error=0

    for entry in "${port_list[@]}"; do
        local port="${entry%%:*}"
        local desc="${entry#*:}"

        local pid
        pid="$(find_pid_by_port "${port}")"

        if [ -z "$pid" ]; then
            log_info "端口 ${port} (${desc}) - 空闲"
        else
            log_warn "端口 ${port} (${desc}) - 被占用 (PID: ${pid})"
            if kill_process "${pid}" "${port} (${desc})"; then
                log_info "端口 ${port} 已释放"
            else
                log_error "端口 ${port} 释放失败"
                has_error=1
            fi
        fi
    done

    return ${has_error}
}

# 检查依赖服务端口可用性（仅检查，不终止进程）
# 参数: 端口列表 "port:desc" ...
# 返回: 0=全部可用, 1=存在不可用的依赖
check_dependency_ports() {
    local port_list=("$@")
    local has_error=0

    for entry in "${port_list[@]}"; do
        local port="${entry%%:*}"
        local desc="${entry#*:}"

        local pid
        pid="$(find_pid_by_port "${port}")"

        if [ -n "$pid" ]; then
            log_info "端口 ${port} (${desc}) - 已就绪 (PID: ${pid})"
        else
            log_warn "端口 ${port} (${desc}) - 未运行"
            has_error=1
        fi
    done

    return ${has_error}
}

# ==================== 依赖检查 ====================

check_dependencies() {
    log_step "检查运行环境..."

    # Python
    if ! command -v python3 &>/dev/null; then
        log_error "未找到 python3，请先安装 Python 3.11+"
        exit 1
    fi
    local py_ver
    py_ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    log_info "Python: ${py_ver}"

    # 虚拟环境
    if [ -d ".venv" ]; then
        source .venv/bin/activate
        log_info "虚拟环境: 已激活"
    else
        log_warn "未找到 .venv 虚拟环境，使用系统 Python"
    fi

    # uvicorn
    if ! python3 -c "import uvicorn" 2>/dev/null; then
        log_error "uvicorn 未安装，请先运行: ./scripts/setup.sh"
        exit 1
    fi
    log_info "uvicorn: 已安装"
}

# 检查前端运行环境
check_frontend_dependencies() {
    log_step "检查前端运行环境..."

    if [ ! -d "${PROJECT_DIR}/frontend" ]; then
        log_warn "未找到 frontend 目录，跳过前端启动"
        return 1
    fi

    # Node.js
    if ! command -v node &>/dev/null; then
        log_warn "未找到 node，跳过前端启动（安装: https://nodejs.org）"
        return 1
    fi
    local node_ver
    node_ver=$(node --version 2>/dev/null)
    log_info "Node.js: ${node_ver}"

    # npm
    if ! command -v npm &>/dev/null; then
        log_warn "未找到 npm，跳过前端启动"
        return 1
    fi
    local npm_ver
    npm_ver=$(npm --version 2>/dev/null)
    log_info "npm: ${npm_ver}"

    # 检查 node_modules 是否存在，不存在则自动安装
    if [ ! -d "${PROJECT_DIR}/frontend/node_modules" ]; then
        log_step "安装前端依赖..."
        cd "${PROJECT_DIR}/frontend"
        npm install
        cd "${PROJECT_DIR}"
        log_info "前端依赖安装完成"
    else
        log_info "前端依赖: 已安装"
    fi

    return 0
}

# ==================== 环境变量 ====================

load_env() {
    log_step "加载环境变量..."

    if [ -f ".env" ]; then
        set -a
        while IFS='=' read -r key value; do
            # 跳过注释和空行
            [[ "$key" =~ ^#.*$ ]] && continue
            [[ -z "$key" ]] && continue
            # 去除值两端的引号
            value="${value%\"}"
            value="${value#\"}"
            value="${value%\'}"
            value="${value#\'}"
            export "${key}=${value}"
        done < <(grep -v '^\s*#' .env | grep -v '^\s*$')
        set +a
        log_info ".env 文件已加载"
    else
        log_warn ".env 文件不存在，使用默认配置"
    fi
}

# ==================== 前端构建 ====================

build_frontend() {
    log_step "构建前端生产包..."

    cd "${PROJECT_DIR}/frontend"
    npm run build
    cd "${PROJECT_DIR}"

    log_info "前端生产包构建完成: frontend/dist/"
}

# ==================== 启动服务 ====================

start_api() {
    local host="$1"
    local port="$2"
    local workers="$3"
    local env="$4"

    log_step "启动 Agent API 服务..."

    if [ "${env}" = "development" ]; then
        log_info "开发模式: 启用热重载"
        uvicorn api.main:app --host "${host}" --port "${port}" --reload &
        API_PID=$!
    else
        log_info "生产模式: ${workers} 工作进程"
        uvicorn api.main:app --host "${host}" --port "${port}" --workers "${workers}" &
        API_PID=$!
    fi

    log_info "Agent API 已启动 (PID: ${API_PID})"
}

start_frontend_dev() {
    local frontend_port="$1"

    log_step "启动前端开发服务器..."

    cd "${PROJECT_DIR}/frontend"
    npx vite --host 0.0.0.0 --port "${frontend_port}" &
    FRONTEND_PID=$!
    cd "${PROJECT_DIR}"

    log_info "前端开发服务器已启动 (PID: ${FRONTEND_PID})"
}

# MCP 服务启动配置: 模块路径 -> 端口
MCP_SERVICE_MAP=(
    "mcp_servers.knowledge_server.server:9010"
    "mcp_servers.web_search_server.server:9011"
)

start_mcp_services() {
    local skip_mcp="${SKIP_MCP_SERVICES:-}"
    if [ "${skip_mcp}" = "true" ]; then
        log_info "已跳过 MCP 服务启动 (SKIP_MCP_SERVICES=true)"
        return 0
    fi

    log_step "启动 MCP 服务..."

    for entry in "${MCP_SERVICE_MAP[@]}"; do
        local module="${entry%%:*}"
        local port="${entry##*:}"
        local desc="${module##*.}"
        desc="${desc%.server}"

        local pid
        pid="$(find_pid_by_port "${port}")"
        if [ -n "$pid" ]; then
            log_info "MCP 服务 ${desc} 已在运行 (PID: ${pid}, 端口: ${port})"
            continue
        fi

        python3 -m "${module}" &
        local mcp_pid=$!
        MCP_PIDS+=("${mcp_pid}")
        log_info "MCP 服务 ${desc} 已启动 (PID: ${mcp_pid}, 端口: ${port})"
    done

    # 等待 MCP 服务就绪
    local ready_count=0
    local max_wait=10
    local waited=0
    while [ ${waited} -lt ${max_wait} ]; do
        ready_count=0
        for entry in "${MCP_SERVICE_MAP[@]}"; do
            local port="${entry##*:}"
            local pid
            pid="$(find_pid_by_port "${port}")"
            if [ -n "$pid" ]; then
                ready_count=$((ready_count + 1))
            fi
        done

        if [ ${ready_count} -eq ${#MCP_SERVICE_MAP[@]} ]; then
            break
        fi

        sleep 1
        waited=$((waited + 1))
    done

    log_info "MCP 服务就绪: ${ready_count}/${#MCP_SERVICE_MAP[@]}"
}

# ==================== 信号处理 ====================

cleanup() {
    echo ""
    log_info "收到终止信号，正在关闭服务..."

    # 终止前端进程
    if [ -n "${FRONTEND_PID}" ] && kill -0 "${FRONTEND_PID}" 2>/dev/null; then
        log_info "终止前端服务 (PID: ${FRONTEND_PID})..."
        kill "${FRONTEND_PID}" 2>/dev/null || true
        wait "${FRONTEND_PID}" 2>/dev/null || true
        FRONTEND_PID=""
    fi

    # 终止后端进程
    if [ -n "${API_PID}" ] && kill -0 "${API_PID}" 2>/dev/null; then
        log_info "终止 API 服务 (PID: ${API_PID})..."
        kill "${API_PID}" 2>/dev/null || true
        wait "${API_PID}" 2>/dev/null || true
        API_PID=""
    fi

    # 终止 MCP 服务进程
    for mcp_pid in "${MCP_PIDS[@]}"; do
        if [ -n "${mcp_pid}" ] && kill -0 "${mcp_pid}" 2>/dev/null; then
            log_info "终止 MCP 服务 (PID: ${mcp_pid})..."
            kill "${mcp_pid}" 2>/dev/null || true
        fi
    done
    MCP_PIDS=()

    log_info "所有服务已停止"
    exit 0
}

trap cleanup SIGINT SIGTERM

# ==================== 主流程 ====================

main() {
    echo ""
    echo "========================================="
    echo "  企业级多Agent办公平台 - 启动脚本"
    echo "========================================="
    echo "  操作系统: $(detect_os)"
    echo "  项目目录: ${PROJECT_DIR}"
    echo "========================================="
    echo ""

    # 1. 检查后端依赖
    check_dependencies

    # 2. 加载环境变量
    load_env

    # 3. 读取配置
    local host="${API_HOST:-0.0.0.0}"
    local port="${API_PORT:-8000}"
    local workers="${API_WORKERS:-1}"
    local env="${ENVIRONMENT:-development}"
    local frontend_port="${FRONTEND_PORT:-${FRONTEND_PORT_DEFAULT}}"

    # 是否跳过前端启动
    local skip_frontend="${SKIP_FRONTEND:-}"

    # 动态更新应用端口列表（仅含后端 API 端口）
    APP_PORTS[0]="${port}:Agent API"

    # 4. 检查依赖服务端口（仅检查可用性，不终止进程）
    log_step "检查依赖服务..."
    if ! check_dependency_ports "${DEP_PORTS[@]}"; then
        log_warn "部分依赖服务未运行，应用可能无法正常工作"
        log_warn "启动建议:"
        log_warn "  Redis:      docker run -d -p 6379:6379 redis:7-alpine"
        log_warn "  PostgreSQL: docker run -d -p 5432:5432 -e POSTGRES_DB=agent_platform -e POSTGRES_PASSWORD=postgres postgres:16-alpine"
        echo ""

        # 询问是否继续
        read -rp "是否仍要启动应用? [y/N] " confirm
        if [[ ! "${confirm}" =~ ^[Yy]$ ]]; then
            log_info "已取消启动"
            exit 0
        fi
    fi

    # 4.5 检查 IDA 智能文档助手端口
    log_step "检查 IDA 智能文档助手..."
    if ! check_dependency_ports "${IDA_PORTS[@]}"; then
        log_warn "IDA 后端服务未运行，知识库相关功能将不可用"
        log_warn "请先启动 IDA 后端服务，或配置 IDA_BACKEND_URL 环境变量"
        log_warn "IDA 启动后知识库功能将自动恢复"
    fi

    # 5. 检查并释放应用端口
    log_step "检查应用端口..."
    if ! check_and_free_ports "${APP_PORTS[@]}"; then
        log_error "应用端口释放失败，无法启动服务"
        exit 1
    fi

    # 6. 检查并释放 MCP 端口（可选）
    local skip_mcp="${SKIP_MCP_PORTS:-}"
    if [ "${skip_mcp}" != "true" ]; then
        log_step "检查 MCP 服务端口..."
        if ! check_and_free_ports "${MCP_PORTS[@]}"; then
            log_warn "部分 MCP 端口释放失败，相关服务可能无法启动"
        fi
    else
        log_info "已跳过 MCP 端口检查 (SKIP_MCP_PORTS=true)"
    fi

    # 7. 应用端口就绪确认（二次检查，仅检查 API 端口）
    log_step "确认应用端口就绪..."
    local retry_count=0
    local max_retries=5

    while [ ${retry_count} -lt ${max_retries} ]; do
        local all_free=true
        for entry in "${APP_PORTS[@]}"; do
            local p="${entry%%:*}"
            local pid
            pid="$(find_pid_by_port "${p}")"
            if [ -n "$pid" ]; then
                all_free=false
                log_warn "端口 ${p} 仍被占用 (PID: ${pid})，等待释放..."
            fi
        done

        if [ "${all_free}" = true ]; then
            break
        fi

        retry_count=$((retry_count + 1))
        if [ ${retry_count} -lt ${max_retries} ]; then
            sleep 1
        else
            log_error "应用端口未能在 ${max_retries} 次重试后释放"
            exit 1
        fi
    done
    log_info "应用端口已就绪"

    # 8. 检查前端环境
    local frontend_available=false
    if [ "${skip_frontend}" != "true" ]; then
        if check_frontend_dependencies; then
            frontend_available=true
        fi
    else
        log_info "已跳过前端启动 (SKIP_FRONTEND=true)"
    fi

    # 9. 前端端口可用性检查（不强制释放，不可用时自动换端口）
    if [ "${frontend_available}" = true ] && [ "${env}" = "development" ]; then
        log_step "检查前端端口..."
        if is_port_free "${frontend_port}"; then
            ACTUAL_FRONTEND_PORT="${frontend_port}"
            log_info "前端端口 ${frontend_port} - 空闲"
        else
            log_warn "前端端口 ${frontend_port} 被占用，尝试查找可用端口..."
            local found_port
            found_port="$(find_free_port $((frontend_port + 1)))"
            if [ -n "${found_port}" ]; then
                ACTUAL_FRONTEND_PORT="${found_port}"
                log_info "前端端口自动切换: ${frontend_port} -> ${found_port}"
            else
                log_warn "未找到可用前端端口 (尝试范围: $((frontend_port + 1))-$((frontend_port + 20)))，跳过前端启动"
                frontend_available=false
            fi
        fi
    fi

    # 10. 生产模式: 先构建前端
    if [ "${env}" != "development" ] && [ "${frontend_available}" = true ]; then
        build_frontend
    fi

    # 11. 启动 MCP 服务（Knowledge、Web Search 等）
    start_mcp_services

    # 12. 启动后端 API 服务
    echo ""
    echo "========================================="
    echo "  启动配置"
    echo "========================================="
    echo "  环境:     ${env}"
    echo "  API 地址: ${host}:${port}"
    echo "  进程数:   ${workers}"
    if [ "${frontend_available}" = true ] && [ "${env}" = "development" ]; then
        echo "  前端端口: ${ACTUAL_FRONTEND_PORT}"
    fi
    echo "========================================="
    echo ""

    start_api "${host}" "${port}" "${workers}" "${env}"

    # 等待 API 启动就绪
    local api_ready=false
    local api_retry=0
    while [ ${api_retry} -lt 15 ]; do
        if curl -s "http://localhost:${port}/api/v1/admin/health" >/dev/null 2>&1; then
            api_ready=true
            break
        fi
        sleep 1
        api_retry=$((api_retry + 1))
    done

    if [ "${api_ready}" = true ]; then
        log_info "Agent API 已就绪"
    else
        log_warn "Agent API 未在 15 秒内响应，可能仍在启动中"
    fi

    # 13. 启动前端服务
    if [ "${frontend_available}" = true ]; then
        if [ "${env}" = "development" ]; then
            start_frontend_dev "${ACTUAL_FRONTEND_PORT}"
        else
            log_info "生产模式: 前端静态文件由 API 服务提供"
            log_info "前端已构建至 frontend/dist/，请配置 Nginx 或 API 静态文件服务"
        fi
    fi

    # 14. 输出访问地址
    echo ""
    echo "========================================="
    echo "  服务已启动"
    echo "========================================="
    if [ "${frontend_available}" = true ] && [ "${env}" = "development" ]; then
        echo "  前端: http://localhost:${ACTUAL_FRONTEND_PORT}"
    fi
    echo "  API:  http://localhost:${port}"
    echo "  文档: http://localhost:${port}/docs"
    echo "========================================="
    echo ""
    log_info "按 Ctrl+C 停止所有服务"
    echo ""

    # 等待所有后台进程
    wait
}

# ==================== 入口 ====================

main "$@"
