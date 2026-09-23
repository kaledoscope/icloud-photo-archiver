#!/bin/bash
# clouddrive_watchdog.sh — CloudDrive2 挂载健康检查 + 自动恢复
#
# 检测 /Volumes/CloudDrive/PhotoBackup 是否可访问。
# 如果不可访问（115 session 过期导致 FUSE "Device not configured"），
# 自动 kill + restart clouddrive 进程，等待挂载恢复。
#
# 用法:
#   bash clouddrive_watchdog.sh              # 日志到 pipeline.log
#   bash clouddrive_watchdog.sh --quiet      # 静默模式，只输出到 stderr 当有问题
#
# 退出码:
#   0  挂载健康 或 已成功恢复
#   1  超时未恢复（流水线继续跑，Step 0 会检测到并妥善处理）

set -euo pipefail

# ===== 配置 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 装载配置（config.env + secrets.env）
source "$PROJECT_DIR/config/load.sh"

# 缺配置直接报错，不用硬编码路径兜底
BACKUP_ROOT="${BACKUP_ROOT:?未配置 BACKUP_ROOT。请检查 config/config.env}"
CLOUDDRIVE_BIN="${CLOUDDRIVE_BIN:?未配置 CLOUDDRIVE_BIN。请检查 config/config.env}"
CLOUDDRIVE_MOUNT="${CLOUDDRIVE_MOUNT:?未配置 CLOUDDRIVE_MOUNT。请检查 config/config.env}"

# 探针用【挂载点根目录】，不用归档子目录：
#   本脚本回答的是"FUSE 挂载还活着吗"，不是"归档目录建了没"。
#   用子目录当探针时，只要归档目录尚未创建（首次运行 / 换了 REMOTE_115_DIR），
#   就会被误判成挂载已死 → 无谓地重启 CloudDrive2。
#   后端断连时（FUSE "Device not configured"），根目录和子目录一样会报错，
#   所以探针换成根目录不会漏掉真正的故障。
MOUNT_CHECK="$CLOUDDRIVE_MOUNT"

MAX_WAIT=120
POLL_INTERVAL=10

# 日志配置
LOG_DIR="${BACKUP_ROOT}/.logs"
PIPELINE_LOG="${LOG_DIR}/pipeline.log"

quiet=false
for arg in "$@"; do
    case "$arg" in
        --quiet) quiet=true ;;
    esac
done

log() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local line="[${timestamp}] [watchdog] $*"
    if [ "$quiet" = false ]; then
        echo "$line"
    fi
    mkdir -p "$LOG_DIR"
    echo "$line" >> "$PIPELINE_LOG"
}

# ---- Step 1: 快速检查挂载 ----
if ls "$MOUNT_CHECK" >/dev/null 2>&1; then
    # 挂载健康，无需操作
    exit 0
fi

log "⚠️  CloudDrive2 挂载不可用，尝试自动恢复..."

# ---- Step 2: 杀掉 clouddrive 进程 ----
# 精确匹配 /Applications/CloudDrive/clouddrive，避免误杀 iCloud/CloudDocs 系统进程
clouddrive_pids=$(pgrep -f "$CLOUDDRIVE_BIN" 2>/dev/null || true)

if [ -z "$clouddrive_pids" ]; then
    log "未找到运行中的 clouddrive 进程，直接启动..."
else
    log "终止 clouddrive 进程: $clouddrive_pids"
    echo "$clouddrive_pids" | xargs kill 2>/dev/null || true
    sleep 3

    # 确保死透
    survivors=$(pgrep -f "$CLOUDDRIVE_BIN" 2>/dev/null || true)
    if [ -n "$survivors" ]; then
        log "进程未响应 SIGTERM，强制终止: $survivors"
        echo "$survivors" | xargs kill -9 2>/dev/null || true
        sleep 2
    fi

    # 确认 FUSE 挂载已卸载（clouddrive 死后 macOS 会自动卸载）
    if mount | grep -q "CloudFS on $CLOUDDRIVE_MOUNT"; then
        log "手动卸载残留 FUSE 挂载..."
        umount "$CLOUDDRIVE_MOUNT" 2>/dev/null || diskutil unmount "$CLOUDDRIVE_MOUNT" 2>/dev/null || true
        sleep 2
    fi
fi

# ---- Step 3: 启动 clouddrive ----
log "启动 CloudDrive2..."
nohup "$CLOUDDRIVE_BIN" > /dev/null 2>&1 &
disown

# ---- Step 4: 等待挂载恢复 ----
elapsed=0
while [ $elapsed -lt $MAX_WAIT ]; do
    sleep "$POLL_INTERVAL"
    elapsed=$((elapsed + POLL_INTERVAL))

    if ls "$MOUNT_CHECK" >/dev/null 2>&1; then
        log "✅ CloudDrive2 挂载已恢复 (${elapsed}s)"
        exit 0
    fi

    log "等待挂载恢复... (${elapsed}s / ${MAX_WAIT}s)"
done

log "❌ 超时: ${MAX_WAIT}s 内未恢复，流水线将继续（Step 0 会报告挂载不可用）"
exit 1
