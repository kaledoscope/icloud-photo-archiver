#!/bin/bash
# backup_to_nas.sh — 唤醒 NAS → 挂载 → 备份 → 卸载 → 关机
#
# 冷备份全流程。模块独立，可单独运行/调试/替换。
#
# 启用开关: config/config.env 里的 NAS_ENABLED（默认 false）。
# 未启用时本脚本直接退出 0 —— 不用「IP 是不是还是占位符」来判断，
# 那种判断只要改一下占位符就会静默失效。
#
# 用法:
#   bash backup_to_nas.sh                  # 完整流程
#   bash backup_to_nas.sh --dry-run        # 预览：不唤醒、不挂载、不写清单
#   bash backup_to_nas.sh --wol-only       # 仅测试 WOL 唤醒
#   bash backup_to_nas.sh --no-shutdown    # 挂载后不关机（调试用）

set -euo pipefail

# 本模块负责的备份目标名。清单标记字段 = backed_up_<TARGET>，
# 远端路径 = config 里的 REMOTE_<TARGET大写>_DIR
TARGET="nas"

# ===== 配置 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 本脚本在 modules/backup/<目标>/ 下，往上三层才是项目根
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# 装载配置（config.env + secrets.env）
source "$PROJECT_DIR/config/load.sh"

BACKUP_ROOT="${BACKUP_ROOT:?未配置 BACKUP_ROOT。请检查 config/config.env}"
PYTHON_BIN="${PYTHON_BIN:?未配置 PYTHON_BIN。请检查 config/config.env}"

NAS_ENABLED="${NAS_ENABLED:-false}"
NAS_IP="${NAS_IP:-}"
NAS_MAC="${NAS_MAC:-}"
NAS_USER="${NAS_USER:-}"
NAS_SHARE="${NAS_SHARE:-}"
NAS_MOUNT_POINT="${NAS_MOUNT_POINT:?未配置 NAS_MOUNT_POINT。请检查 config/config.env}"
NAS_REMOTE_DIR="${REMOTE_NAS_DIR:-${NAS_MOUNT_POINT}/PhotoBackup}"
SSH_KEY="${SSH_KEY:-}"

# rsync 调优参数：config 里没写也照常跑，不让 set -u 把整条流水线打断
RSYNC_TIMEOUT="${RSYNC_TIMEOUT:-300}"
RSYNC_CONTIMEOUT="${RSYNC_CONTIMEOUT:-60}"

MAX_WAIT_SEC="${NAS_MAX_WAIT_SEC:-300}"          # SSH 就绪最大等待
WAIT_INTERVAL="${NAS_WAIT_INTERVAL:-10}"         # 轮询间隔
MAX_WOL_RETRIES="${NAS_MAX_WOL_RETRIES:-3}"      # WOL 重试次数
WOL_RETRY_DELAY="${NAS_WOL_RETRY_DELAY:-300}"    # 两次 WOL 之间的等待
PING_WAIT_SEC="${NAS_PING_WAIT_SEC:-60}"         # 单次 WOL 后等 ping 多久

LOG_FILE="${BACKUP_ROOT}/.logs/backup_nas.log"

# 以包的形式调用 Python 模块（modules/ 是包，PYTHONPATH 指向项目根）
run_py() { PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -m "$@"; }

log() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local line="[${timestamp}] $*"
    echo "$line"
    echo "$line" >> "$LOG_FILE"
}

# SSH 到 NAS 的统一参数
nas_ssh() {
    local timeout="${1:-10}"
    shift
    ssh -i "$SSH_KEY" \
        -o ConnectTimeout="$timeout" \
        -o BatchMode=yes \
        -o StrictHostKeyChecking=accept-new \
        "$NAS_USER@$NAS_IP" "$@"
}

# ===== 步骤 0: 配置完整性检查 =====
require_nas_config() {
    local missing=()
    [ -n "$NAS_IP" ]   || missing+=("NAS_IP")
    [ -n "$NAS_MAC" ]  || missing+=("NAS_MAC")
    [ -n "$NAS_USER" ] || missing+=("NAS_USER")
    [ -n "$NAS_SHARE" ] || missing+=("NAS_SHARE")
    [ -n "$SSH_KEY" ]  || missing+=("SSH_KEY")

    if [ ${#missing[@]} -gt 0 ]; then
        log "❌ NAS 配置不完整，缺少: ${missing[*]}"
        log "   → 请填写 config/config.env（NAS_IP/MAC/USER/SHARE）和 config/secrets.env（SSH_KEY）"
        return 1
    fi
    if [ ! -f "$SSH_KEY" ]; then
        log "❌ SSH 密钥不存在: $SSH_KEY"
        return 1
    fi
    return 0
}

# ===== 步骤 1: Wake-on-LAN =====
wake_nas() {
    log "发送 WOL 唤醒包 → $NAS_MAC"

    local i
    for i in $(seq 1 "$MAX_WOL_RETRIES"); do
        log "  第 $i/$MAX_WOL_RETRIES 次 WOL..."
        wakeonlan "$NAS_MAC" 2>&1 | while IFS= read -r line; do log "    $line"; done

        local waited=0
        while [ $waited -lt "$PING_WAIT_SEC" ]; do
            sleep "$WAIT_INTERVAL"
            waited=$((waited + WAIT_INTERVAL))
            if ping -c 1 -W 3 "$NAS_IP" > /dev/null 2>&1; then
                log "  NAS 已响应 ping (等了 ${waited}s)"
                return 0
            fi
            log "  等待 ping... (${waited}s)"
        done

        if [ "$i" -lt "$MAX_WOL_RETRIES" ]; then
            log "  第 $i 次 WOL 未响应，等待 ${WOL_RETRY_DELAY}s 后重试..."
            sleep "$WOL_RETRY_DELAY"
        fi
    done

    log "❌ WOL 失败: $MAX_WOL_RETRIES 次重试后 NAS 仍未响应"
    return 1
}

# ===== 步骤 2: 等待 SSH 就绪 =====
wait_ssh_ready() {
    log "等待 SSH 就绪 (最多 ${MAX_WAIT_SEC}s)..."

    local waited=0
    while [ $waited -lt "$MAX_WAIT_SEC" ]; do
        if nas_ssh 5 "echo OK" 2>/dev/null; then
            log "✅ NAS SSH 就绪 (等待了 ${waited}s)"
            return 0
        fi
        sleep "$WAIT_INTERVAL"
        waited=$((waited + WAIT_INTERVAL))
        if [ $((waited % 30)) -eq 0 ]; then
            log "  仍在等待 SSH... (${waited}s)"
        fi
    done

    log "❌ NAS SSH 超时: 等待 ${MAX_WAIT_SEC}s 未就绪"
    return 1
}

# ===== 步骤 3: 挂载 NAS 共享 =====
mount_nas() {
    log "挂载 NAS 共享: $NAS_SHARE"
    mkdir -p "$NAS_MOUNT_POINT"

    if mount | grep -q "$NAS_MOUNT_POINT"; then
        log "  检测到旧挂载，先卸载..."
        diskutil unmount "$NAS_MOUNT_POINT" 2>/dev/null || {
            log "  正常卸载失败，尝试强制卸载..."
            diskutil unmount force "$NAS_MOUNT_POINT" 2>/dev/null || true
        }
        sleep 2
    fi

    mount -t smbfs "//${NAS_USER}@${NAS_IP}/${NAS_SHARE}" "$NAS_MOUNT_POINT" 2>&1 \
        | while IFS= read -r line; do log "  mount: $line"; done

    if mount | grep -q "$NAS_MOUNT_POINT"; then
        log "✅ NAS 挂载成功 → $NAS_MOUNT_POINT"
        mkdir -p "$NAS_REMOTE_DIR"
        return 0
    fi

    log "❌ NAS 挂载失败"
    return 1
}

# ===== 步骤 4: rsync 差异同步 =====
sync_to_nas() {
    log "开始 rsync → NAS..."

    # 默认按 文件名 + 文件大小 + 修改时间 判断差异（不读内容）
    # 照片是一次写入永不修改的数据，--checksum 没必要且浪费 I/O
    rsync -avhP --partial \
        --exclude '.state/' --exclude '.logs/' --exclude '.DS_Store' \
        --exclude '._*' --exclude '.gitkeep' \
        --timeout="$RSYNC_TIMEOUT" --contimeout="$RSYNC_CONTIMEOUT" \
        --stats \
        "$BACKUP_ROOT/" \
        "$NAS_REMOTE_DIR/" 2>&1 | while IFS= read -r line; do
            if echo "$line" | grep -qE '(xfr#|speedup|Number of files|Total|sent|Literal|Matched)'; then
                log "  $line"
            fi
        done

    local exit_code=${PIPESTATUS[0]}
    if [ $exit_code -eq 0 ]; then
        log "✅ rsync 完成"
        return 0
    fi
    log "❌ rsync 失败 (exit: $exit_code)"
    return 1
}

# ===== 步骤 5: 卸载 NAS 共享 =====
unmount_nas() {
    log "卸载 NAS 共享..."
    sleep 5  # 等 rsync 缓冲区刷盘

    diskutil unmount "$NAS_MOUNT_POINT" 2>/dev/null || {
        log "⚠️  正常卸载失败，强制卸载..."
        diskutil unmount force "$NAS_MOUNT_POINT" 2>/dev/null || {
            log "⚠️  强制卸载也失败了"
            return 1
        }
    }

    if mount | grep -q "$NAS_MOUNT_POINT"; then
        log "⚠️  卸载可能未成功"
        return 1
    fi
    log "✅ NAS 已卸载"
    return 0
}

# ===== 步骤 6: 远程关机 =====
shutdown_nas() {
    log "发送 NAS 关机指令..."

    if nas_ssh 10 "sudo synopoweroff" 2>/dev/null; then
        log "✅ NAS 关机指令已发送 (synopoweroff)"
        return 0
    fi
    if nas_ssh 10 "sudo poweroff" 2>/dev/null; then
        log "✅ NAS 关机指令已发送 (poweroff)"
        return 0
    fi

    log "  SSH 关机失败，尝试 DSM API..."
    if curl -s -m 10 \
        "http://${NAS_IP}:5000/webapi/entry.cgi?api=SYNO.Core.Shutdown&version=1&method=shutdown" \
        > /dev/null 2>&1; then
        log "✅ NAS 关机指令已发送 (DSM API)"
        return 0
    fi

    log "⚠️  NAS 关机指令发送失败，请手动关机"
    return 1
}

# ===== 主流程 =====
main() {
    local wol_only=false no_shutdown=false dry_run=false
    for arg in "$@"; do
        case "$arg" in
            --wol-only)    wol_only=true ;;
            --no-shutdown) no_shutdown=true ;;
            --dry-run)     dry_run=true ;;
        esac
    done

    mkdir -p "$(dirname "$LOG_FILE")"
    log "===== ${TARGET} 冷备份开始 ====="

    if [ "$NAS_ENABLED" != "true" ]; then
        log "⏭  NAS 未启用（config/config.env 里 NAS_ENABLED=false），跳过"
        return 0
    fi

    [ "$dry_run" = true ] && log "[DRY RUN 模式] 不唤醒、不挂载、不写清单"

    if ! require_nas_config; then
        log "===== ${TARGET} 冷备份失败 (配置不完整) ====="
        return 1
    fi

    # 检查 NAS 是否已在线
    if ping -c 1 -W 3 "$NAS_IP" > /dev/null 2>&1; then
        log "NAS 已在线，跳过 WOL"
    elif [ "$dry_run" = true ]; then
        log "[DRY RUN] 将发送 WOL 到 $NAS_MAC"
    else
        log "NAS 离线，开始 WOL 唤醒..."
        if ! wake_nas; then
            log "===== ${TARGET} 冷备份失败 (WOL) ====="
            return 1
        fi
    fi

    if [ "$wol_only" = true ]; then
        log "===== WOL 测试完成 ====="
        return 0
    fi

    if [ "$dry_run" = false ]; then
        wait_ssh_ready || { log "===== ${TARGET} 冷备份失败 (SSH) ====="; return 1; }
        mount_nas     || { log "===== ${TARGET} 冷备份失败 (挂载) ====="; return 1; }
    else
        log "[DRY RUN] 将挂载 //$NAS_USER@$NAS_IP/$NAS_SHARE → $NAS_MOUNT_POINT"
    fi

    # 同步
    local sync_result=0
    if [ "$dry_run" = false ]; then
        sync_to_nas || sync_result=$?
    else
        log "[DRY RUN] 将 rsync $BACKUP_ROOT/ → $NAS_REMOTE_DIR/"
    fi

    # 只有同步成功才打标记 —— 失败却标成已备份，等于给清理逻辑发了假通行证
    if [ $sync_result -eq 0 ]; then
        if [ "$dry_run" = false ]; then
            log "更新清单中的 ${TARGET} 备份标记..."
            run_py modules.utils.manifest mark --target "$TARGET" \
                2>&1 | while IFS= read -r line; do log "  $line"; done
        else
            log "[DRY RUN] 将标记清单中所有文件为 ${TARGET} 已备份"
        fi
    fi

    if [ "$dry_run" = false ]; then
        unmount_nas
    else
        log "[DRY RUN] 将卸载 $NAS_MOUNT_POINT"
    fi

    if [ "$no_shutdown" = true ]; then
        log "跳过 NAS 关机（--no-shutdown）"
    elif [ "$dry_run" = false ]; then
        shutdown_nas
    else
        log "[DRY RUN] 将发送关机指令到 $NAS_IP"
    fi

    if [ $sync_result -eq 0 ]; then
        log "===== ${TARGET} 冷备份成功 ====="
        return 0
    fi
    log "===== ${TARGET} 冷备份完成（但 rsync 有错误）====="
    return 1
}

main "$@"
