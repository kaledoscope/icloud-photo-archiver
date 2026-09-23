#!/bin/bash
# backup_to_115.sh — 将本地中转目录差异同步至 115 网盘 (via CloudDrive2)
#
# 模块化设计：115 备份与 NAS 备份完全独立，可单独运行/调试/替换。
#
# 用法:
#   bash backup_to_115.sh                  # 使用 config/ 下的配置
#   bash backup_to_115.sh --dry-run        # 预览：不写远端、不改清单
#   BACKUP_ROOT=/tmp/t bash backup_to_115.sh --dry-run   # 临时换中转目录

set -euo pipefail

# 本模块负责的备份目标名。清单标记字段 = backed_up_<TARGET>，
# 远端路径 = config 里的 REMOTE_<TARGET大写>_DIR
TARGET="115"

# 全局开关：sync_files 等子函数要用，必须是全局变量。
# 别改成 main() 里的 local —— 开了 set -u 之后子函数读到未定义变量会直接中止。
DRY_RUN=false

# ===== 配置 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 本脚本在 modules/backup/<目标>/ 下，往上三层才是项目根
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# 装载配置（config.env + secrets.env）
source "$PROJECT_DIR/config/load.sh"

# 缺配置直接报错，不用硬编码路径兜底
BACKUP_ROOT="${BACKUP_ROOT:?未配置 BACKUP_ROOT。请检查 config/config.env}"
PYTHON_BIN="${PYTHON_BIN:?未配置 PYTHON_BIN。请检查 config/config.env}"
REMOTE_DIR="${REMOTE_115_DIR:?未配置 REMOTE_115_DIR。请检查 config/config.env}"
CLOUDDRIVE_MOUNT="${CLOUDDRIVE_MOUNT:?未配置 CLOUDDRIVE_MOUNT。请检查 config/config.env}"

# rsync 调优参数：config 里没写也照常跑，不让 set -u 把整条流水线打断
RSYNC_TIMEOUT="${RSYNC_TIMEOUT:-300}"
RSYNC_CONTIMEOUT="${RSYNC_CONTIMEOUT:-60}"
MAX_RETRIES="${MAX_RETRIES:-3}"
RETRY_DELAY="${RETRY_DELAY:-300}"

LOG_FILE="${BACKUP_ROOT}/.logs/backup_115.log"

# 以包的形式调用 Python 模块（modules/ 是包，PYTHONPATH 指向项目根）
run_py() { PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -m "$@"; }

# 加载共享工具函数
UTILS_DIR="$PROJECT_DIR/modules/utils"
if [ -f "$UTILS_DIR/logger.sh" ]; then
    source "$UTILS_DIR/logger.sh"
fi

log() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local line="[${timestamp}] $*"
    echo "$line"
    echo "$line" >> "$LOG_FILE"
}

# ===== 步骤 1: 检查挂载点 =====
check_mount() {
    log "检查 CloudDrive2 挂载点: $CLOUDDRIVE_MOUNT"

    if [ ! -d "$CLOUDDRIVE_MOUNT" ]; then
        log "❌ 挂载点目录不存在: $CLOUDDRIVE_MOUNT"
        return 1
    fi

    if [ -z "$(ls -A "$CLOUDDRIVE_MOUNT" 2>/dev/null)" ]; then
        log "❌ 挂载点为空，CloudDrive2 可能未正确挂载"
        return 1
    fi

    if [ ! -w "$CLOUDDRIVE_MOUNT" ]; then
        log "❌ 挂载点不可写: $CLOUDDRIVE_MOUNT"
        return 1
    fi

    log "✅ CloudDrive2 挂载正常"
    return 0
}

# ===== 步骤 2: 确保远端目录存在 =====
prepare_remote_dir() {
    log "准备远端目录: $REMOTE_DIR"
    mkdir -p "$REMOTE_DIR"

    if [ ! -d "$REMOTE_DIR" ]; then
        log "❌ 无法创建远端目录: $REMOTE_DIR"
        return 1
    fi
    return 0
}

# ===== 步骤 3: 预检查磁盘空间与待同步文件 =====
# 返回: 0=可以同步  1=错误  2=没有文件需要同步
check_disk_space() {
    log "检查源端和目的端磁盘空间..."

    local source_usage
    source_usage=$(df -h "$BACKUP_ROOT" 2>/dev/null | tail -1 | awk '{print $5}' | tr -d '%')
    if [ -n "$source_usage" ] && [ "$source_usage" -gt 95 ]; then
        log "⚠️  源端磁盘使用率: ${source_usage}%，接近满载"
    fi

    local file_count
    file_count=$(find "$BACKUP_ROOT" -type f \
        -not -path '*/.state/*' -not -path '*/.logs/*' \
        -not -name '.DS_Store' 2>/dev/null | wc -l | tr -d ' ')
    log "待同步文件数: $file_count"

    if [ "$file_count" -eq 0 ]; then
        log "⚠️  源端无待同步文件，跳过 rsync"
        return 2
    fi
    return 0
}

# ===== 步骤 4: rsync 差异同步 =====
sync_files() {
    log "开始 rsync → 115 网盘..."
    [ "$DRY_RUN" = true ] && log "  [DRY RUN] 只预览，不实际传输"

    local attempt=1
    local exit_code=1
    local rsync_args=(-avhP --partial
        --exclude '.state/' --exclude '.logs/' --exclude '.DS_Store'
        --exclude '._*' --exclude '.gitkeep'
        --timeout="$RSYNC_TIMEOUT" --contimeout="$RSYNC_CONTIMEOUT"
        --stats)
    [ "$DRY_RUN" = true ] && rsync_args+=(--dry-run)

    while [ $attempt -le "$MAX_RETRIES" ]; do
        log "  第 $attempt/$MAX_RETRIES 次尝试..."

        # 默认按 文件名 + 文件大小 + 修改时间 判断差异（不读内容）
        # 照片是一次写入永不修改的数据，没必要用 --checksum 读远端文件算哈希
        # 远端是 CloudDrive2 FUSE 挂载，--checksum 会导致每个文件被完整下载
        rsync "${rsync_args[@]}" \
            "$BACKUP_ROOT/" \
            "$REMOTE_DIR/" 2>&1 | while IFS= read -r line; do
                if echo "$line" | grep -qE '(xfr#|speedup|Number of files|Total|sent|Literal|Matched)'; then
                    log "  $line"
                fi
            done

        exit_code=${PIPESTATUS[0]}

        if [ $exit_code -eq 0 ]; then
            log "✅ rsync 完成 (第 $attempt 次尝试)"
            break
        fi

        log "⚠️  rsync 失败 (exit: $exit_code)，第 $attempt 次尝试"
        if [ $attempt -lt "$MAX_RETRIES" ]; then
            log "  等待 ${RETRY_DELAY}s 后重试..."
            sleep "$RETRY_DELAY"
        fi
        attempt=$((attempt + 1))
    done

    return $exit_code
}

# ===== 步骤 5: 更新清单备份标记 =====
update_manifest() {
    log "更新清单中的 ${TARGET} 备份标记..."
    run_py modules.utils.manifest mark --target "$TARGET" \
        2>&1 | while IFS= read -r line; do log "  $line"; done
}

# ===== 主流程 =====
main() {
    for arg in "$@"; do
        case "$arg" in
            --dry-run) DRY_RUN=true ;;
        esac
    done

    mkdir -p "$(dirname "$LOG_FILE")"

    log "===== ${TARGET} 备份开始 ====="
    [ "$DRY_RUN" = true ] && log "[DRY RUN 模式] 不写远端，不改清单"

    if ! check_mount; then
        log "===== ${TARGET} 备份失败 (挂载检查) ====="
        return 1
    fi

    if [ "$DRY_RUN" = false ]; then
        if ! prepare_remote_dir; then
            log "===== ${TARGET} 备份失败 (目录准备) ====="
            return 1
        fi
    else
        log "[DRY RUN] 将确保远端目录存在: $REMOTE_DIR"
    fi

    # 注意 `|| space_result=$?` 的写法：直接 `check_disk_space` 再取 $?
    # 会被 set -e 提前干掉（返回码 2 也是非零）
    local space_result=0
    check_disk_space || space_result=$?
    if [ $space_result -eq 1 ]; then
        log "===== ${TARGET} 备份失败 (空间检查) ====="
        return 1
    elif [ $space_result -eq 2 ]; then
        log "===== ${TARGET} 备份完成 (无文件需同步) ====="
        return 0
    fi

    if sync_files; then
        if [ "$DRY_RUN" = false ]; then
            update_manifest
        else
            log "[DRY RUN] 将标记清单中所有文件为 ${TARGET} 已备份"
        fi
        log "===== ${TARGET} 备份成功 ====="
        return 0
    else
        log "===== ${TARGET} 备份失败 (rsync 错误) ====="
        return 1
    fi
}

main "$@"
