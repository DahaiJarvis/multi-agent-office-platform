#!/bin/bash
# ============================================================
# 外部 Skills 下载脚本
#
# 从 GitHub 仓库下载 Claude (Anthropic) 官方 Skills
# 到 skills/external/anthropic/ 目录
#
# 用法:
#   ./download_skills.sh              # 下载全部推荐的 Anthropic Skills
#   ./download_skills.sh docx xlsx    # 只下载指定的 Skills
#   ./download_skills.sh --list       # 列出可下载的 Skills
#   ./download_skills.sh --guide      # 显示手动下载指南
#
# 依赖: git
# ============================================================

set -euo pipefail

# 项目 Skills 目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="${SCRIPT_DIR}"
EXTERNAL_DIR="${SKILLS_DIR}/external"
ANTHROPIC_DIR="${EXTERNAL_DIR}/anthropic"
OPENCLAW_DIR="${EXTERNAL_DIR}/openclaw"

# Anthropic Skills 仓库地址
ANTHROPIC_REPO="https://github.com/anthropics/skills.git"

# 推荐下载的 Anthropic Skills 列表
RECOMMENDED_SKILLS=(
    docx
    xlsx
    pptx
    pdf
    internal-comms
    brand-guidelines
    doc-coauthoring
    mcp-builder
    skill-creator
    webapp-testing
    claude-api
)

# 临时克隆目录
TEMP_CLONE_DIR=""

# 清理函数
cleanup() {
    if [[ -n "${TEMP_CLONE_DIR}" && -d "${TEMP_CLONE_DIR}" ]]; then
        echo "[清理] 删除临时目录: ${TEMP_CLONE_DIR}"
        rm -rf "${TEMP_CLONE_DIR}"
    fi
}
trap cleanup EXIT

# 创建目标目录
mkdir -p "${ANTHROPIC_DIR}"
mkdir -p "${OPENCLAW_DIR}"

# 显示手动下载指南
show_guide() {
    cat << 'GUIDE'
============================================================
  Anthropic Skills 手动下载指南
============================================================

由于网络原因，自动下载可能失败。请按以下步骤手动下载：

方法一：浏览器下载（推荐）
------------------------------------------------------------
1. 在浏览器中打开: https://github.com/anthropics/skills
2. 点击绿色 "Code" 按钮 -> "Download ZIP"
3. 解压下载的 ZIP 文件
4. 将解压后 skills/ 目录下的子文件夹复制到项目的
   skills/external/anthropic/ 目录中

   需要复制的子文件夹:
GUIDE
    for skill in "${RECOMMENDED_SKILLS[@]}"; do
        echo "     - ${skill}"
    done
    cat << 'GUIDE'

方法二：终端 git clone（需要能访问 GitHub）
------------------------------------------------------------
1. 克隆仓库到临时目录:
   git clone --depth 1 https://github.com/anthropics/skills.git /tmp/anthropic-skills

2. 复制需要的 skills 到项目目录:
GUIDE
    echo "   TARGET_DIR=\"\$(pwd)/skills/external/anthropic\""
    for skill in "${RECOMMENDED_SKILLS[@]}"; do
        echo "   cp -r /tmp/anthropic-skills/skills/${skill} \"\${TARGET_DIR}/\""
    done
    cat << 'GUIDE'

3. 清理临时目录:
   rm -rf /tmp/anthropic-skills

方法三：使用代理下载
------------------------------------------------------------
如果有 HTTP 代理，设置后再运行本脚本:
   export https_proxy=http://your-proxy:port
   ./download_skills.sh --all

方法四：手动创建目录结构（离线方式）
------------------------------------------------------------
如果无法访问 GitHub，可以手动创建 skill 目录:

   skills/external/anthropic/
     docx/
       SKILL.md          # 必须包含 name 和 description 字段
       scripts/           # 可选：Python 脚本
     xlsx/
       SKILL.md
       scripts/
     ...

   SKILL.md 格式示例:
   ---
   name: docx
   description: Word 文档生成和编辑
   version: 1.0.0
   ---
   指令内容...

============================================================
  OpenClaw Skills 下载（可选）
============================================================

OpenClaw Skills 仓库: https://github.com/openclaw/skills
下载方式与 Anthropic Skills 相同，目标目录为:
  skills/external/openclaw/

注意: OpenClaw Skills 质量参差不齐，建议逐个审查后再使用。

============================================================
GUIDE
}

# 列出可下载的 Skills
list_skills() {
    echo "可下载的 Anthropic 官方 Skills:"
    echo "-----------------------------------"
    for skill in "${RECOMMENDED_SKILLS[@]}"; do
        local status=""
        if [[ -d "${ANTHROPIC_DIR}/${skill}" ]]; then
            status="[已安装]"
        else
            status="[未安装]"
        fi
        echo "  ${skill} ${status}"
    done
    echo "-----------------------------------"
    echo "用法: $0 [skill1 skill2 ...]"
    echo "      $0 --all     下载全部推荐 Skills"
    echo "      $0 --list    列出可下载的 Skills"
    echo "      $0 --guide   显示手动下载指南"
}

# 检查 git 是否可用
check_git() {
    if ! command -v git &> /dev/null; then
        echo "[错误] git 未安装，请先安装 git"
        exit 1
    fi
}

# 下载指定的 Skills
download_skills() {
    local skills_to_download=("$@")

    if [[ ${#skills_to_download[@]} -eq 0 ]]; then
        skills_to_download=("${RECOMMENDED_SKILLS[@]}")
    fi

    echo "[信息] 准备下载 ${#skills_to_download[@]} 个 Anthropic Skills: ${skills_to_download[*]}"

    # 检查 git
    check_git

    # 创建临时目录
    TEMP_CLONE_DIR=$(mktemp -d)
    echo "[信息] 临时克隆目录: ${TEMP_CLONE_DIR}"

    # Sparse clone Anthropic Skills 仓库
    echo "[信息] 正在克隆 Anthropic Skills 仓库 (sparse checkout)..."
    cd "${TEMP_CLONE_DIR}"

    git init -q
    git remote add origin "${ANTHROPIC_REPO}"

    # 构建 sparse-checkout 路径列表
    local sparse_paths=()
    for skill in "${skills_to_download[@]}"; do
        sparse_paths+=("skills/${skill}")
    done

    git sparse-checkout init --cone
    git sparse-checkout set "${sparse_paths[@]}"

    # 只拉取最新提交
    if ! git fetch -q --depth=1 origin main 2>/dev/null && ! git fetch -q --depth=1 origin master 2>/dev/null; then
        echo ""
        echo "[错误] 无法从 GitHub 拉取代码，可能原因:"
        echo "  1. 网络无法访问 github.com"
        echo "  2. 需要设置代理: export https_proxy=http://your-proxy:port"
        echo ""
        echo "请运行以下命令查看手动下载指南:"
        echo "  ./download_skills.sh --guide"
        exit 1
    fi

    local branch
    branch=$(git branch -r | head -1 | sed 's/origin\///' | tr -d ' ')
    git checkout -q "${branch}" 2>/dev/null || git checkout -q HEAD 2>/dev/null

    echo "[信息] 克隆完成"

    # 复制 Skills 到目标目录
    local installed=0
    local failed=0

    for skill in "${skills_to_download[@]}"; do
        local src_dir="${TEMP_CLONE_DIR}/skills/${skill}"
        local dest_dir="${ANTHROPIC_DIR}/${skill}"

        if [[ ! -d "${src_dir}" ]]; then
            echo "[警告] Skill '${skill}' 在仓库中不存在，跳过"
            ((failed++)) || true
            continue
        fi

        # 如果目标已存在，先删除
        if [[ -d "${dest_dir}" ]]; then
            echo "[信息] Skill '${skill}' 已存在，更新..."
            rm -rf "${dest_dir}"
        fi

        # 复制
        cp -r "${src_dir}" "${dest_dir}"
        echo "[完成] 已安装: ${skill} -> ${dest_dir}"
        ((installed++)) || true
    done

    echo ""
    echo "========================================="
    echo "  Anthropic Skills 下载完成"
    echo "  安装: ${installed}  失败: ${failed}"
    echo "  安装目录: ${ANTHROPIC_DIR}"
    echo "========================================="
}

# 主逻辑
main() {
    if [[ ${#} -eq 0 ]]; then
        download_skills
        return
    fi

    case "${1}" in
        --list|-l)
            list_skills
            ;;
        --all|-a)
            download_skills "${RECOMMENDED_SKILLS[@]}"
            ;;
        --guide|-g)
            show_guide
            ;;
        --help|-h)
            echo "用法: $0 [选项] [skill1 skill2 ...]"
            echo ""
            echo "选项:"
            echo "  --list, -l    列出可下载的 Skills"
            echo "  --all, -a     下载全部推荐 Skills"
            echo "  --guide, -g   显示手动下载指南"
            echo "  --help, -h    显示帮助信息"
            echo ""
            echo "示例:"
            echo "  $0                    # 下载全部推荐 Skills"
            echo "  $0 docx xlsx pdf      # 只下载指定的 Skills"
            ;;
        *)
            download_skills "$@"
            ;;
    esac
}

main "$@"
