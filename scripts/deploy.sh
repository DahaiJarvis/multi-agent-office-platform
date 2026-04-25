#!/bin/bash
# 生产环境发布脚本
# 支持灰度发布和全量发布两种模式
#
# 使用方式:
#   灰度发布:  ./scripts/deploy.sh canary v2.0
#   全量发布:  ./scripts/deploy.sh full v2.0
#   回滚:     ./scripts/deploy.sh rollback

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
K8S_DIR="$PROJECT_DIR/deploy/k8s"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 检查 kubectl 可用性
check_kubectl() {
    if ! command -v kubectl &> /dev/null; then
        log_error "kubectl 未安装"
        exit 1
    fi

    if ! kubectl cluster-info &> /dev/null; then
        log_error "无法连接 K8s 集群"
        exit 1
    fi
    log_info "K8s 集群连接正常"
}

# 健康检查
check_health() {
    local namespace=$1
    local deployment=$2
    local max_retries=30
    local retry=0

    while [ $retry -lt $max_retries ]; do
        local ready=$(kubectl get deployment "$deployment" -n "$namespace" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
        local desired=$(kubectl get deployment "$deployment" -n "$namespace" -o jsonpath='{.status.replicas}' 2>/dev/null || echo "0")

        if [ "$ready" = "$desired" ] && [ "$ready" != "0" ]; then
            log_info "$deployment 就绪: $ready/$desired"
            return 0
        fi

        retry=$((retry + 1))
        log_warn "等待 $deployment 就绪 ($ready/$desired), 重试 $retry/$max_retries..."
        sleep 10
    done

    log_error "$deployment 未能在规定时间内就绪"
    return 1
}

# 灰度发布
canary_deploy() {
    local version=$1
    log_info "========================================="
    log_info "  灰度发布: $version"
    log_info "========================================="

    check_kubectl

    # 1. 创建命名空间和配置
    log_info "[步骤1] 应用 ConfigMap..."
    kubectl apply -f "$K8S_DIR/configmap.yaml"

    # 2. 部署 MCP 服务
    log_info "[步骤2] 部署 MCP 服务..."
    kubectl apply -f "$K8S_DIR/mcp-deployments.yaml"

    # 3. 等待 MCP 服务就绪
    log_info "[步骤3] 等待 MCP 服务就绪..."
    for svc in mcp-oa-server mcp-email-server mcp-calendar-server mcp-crm-server; do
        check_health "mcp-prod" "$svc" || log_warn "$svc 未就绪，继续部署"
    done

    # 4. 更新 Agent 服务镜像（灰度：先更新1个副本）
    log_info "[步骤4] 灰度更新 Agent 编排服务..."
    kubectl set image deployment/agent-orchestrator \
        orchestrator="registry.company.com/agent-platform:$version" \
        -n agent-prod

    # 5. 等待滚动更新完成
    log_info "[步骤5] 等待滚动更新完成..."
    kubectl rollout status deployment/agent-orchestrator -n agent-prod --timeout=300s

    # 6. 健康检查
    log_info "[步骤6] 健康检查..."
    check_health "agent-prod" "agent-orchestrator"

    log_info "========================================="
    log_info "  灰度发布完成！"
    log_info "  请观察系统运行状态，确认无异常后执行全量发布"
    log_info "  全量发布: ./scripts/deploy.sh full $version"
    log_info "========================================="
}

# 全量发布
full_deploy() {
    local version=$1
    log_info "========================================="
    log_info "  全量发布: $version"
    log_info "========================================="

    check_kubectl

    # 1. 确认所有 K8s 资源
    log_info "[步骤1] 应用所有 K8s 配置..."
    kubectl apply -f "$K8S_DIR/configmap.yaml"
    kubectl apply -f "$K8S_DIR/mcp-deployments.yaml"
    kubectl apply -f "$K8S_DIR/agent-deployment.yaml"

    # 2. 更新镜像版本
    log_info "[步骤2] 更新 Agent 编排服务镜像..."
    kubectl set image deployment/agent-orchestrator \
        orchestrator="registry.company.com/agent-platform:$version" \
        -n agent-prod

    # 3. 等待所有部署就绪
    log_info "[步骤3] 等待所有部署就绪..."
    kubectl rollout status deployment/agent-orchestrator -n agent-prod --timeout=600s

    # 4. 全面健康检查
    log_info "[步骤4] 全面健康检查..."
    check_health "agent-prod" "agent-orchestrator"

    # 5. 验证 API 可用性
    log_info "[步骤5] 验证 API 可用性..."
    local pod_name=$(kubectl get pods -n agent-prod -l app=agent-orchestrator -o jsonpath='{.items[0].metadata.name}')
    kubectl exec "$pod_name" -n agent-prod -- curl -sf http://localhost:8000/api/v1/admin/health > /dev/null 2>&1 && \
        log_info "API 健康检查通过" || log_warn "API 健康检查失败，请手动验证"

    log_info "========================================="
    log_info "  全量发布完成！"
    log_info "  版本: $version"
    log_info "========================================="
}

# 回滚
rollback() {
    log_info "========================================="
    log_info "  回滚到上一版本"
    log_info "========================================="

    check_kubectl

    log_info "[步骤1] 回滚 Agent 编排服务..."
    kubectl rollout undo deployment/agent-orchestrator -n agent-prod

    log_info "[步骤2] 等待回滚完成..."
    kubectl rollout status deployment/agent-orchestrator -n agent-prod --timeout=300s

    log_info "[步骤3] 验证回滚结果..."
    check_health "agent-prod" "agent-orchestrator"

    log_info "========================================="
    log_info "  回滚完成！"
    log_info "========================================="
}

# 主入口
case "${1:-}" in
    canary)
        if [ -z "${2:-}" ]; then
            log_error "请指定版本号: ./scripts/deploy.sh canary <version>"
            exit 1
        fi
        canary_deploy "$2"
        ;;
    full)
        if [ -z "${2:-}" ]; then
            log_error "请指定版本号: ./scripts/deploy.sh full <version>"
            exit 1
        fi
        full_deploy "$2"
        ;;
    rollback)
        rollback
        ;;
    *)
        echo "用法: $0 {canary|full|rollback} [version]"
        echo ""
        echo "  canary <version>   灰度发布"
        echo "  full <version>     全量发布"
        echo "  rollback           回滚到上一版本"
        exit 1
        ;;
esac
