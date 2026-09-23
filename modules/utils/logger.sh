#!/bin/bash
# logger.sh — 共享的日志记录函数
# 被其他脚本 source 使用

# 日志级别
LOG_LEVEL="${LOG_LEVEL:-INFO}"

# 时间戳格式
log() {
    local level="${1:-INFO}"
    shift
    local message="$*"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')

    # 根据日志级别过滤
    case "$LOG_LEVEL" in
        DEBUG) ;;
        INFO)
            if [ "$level" = "DEBUG" ]; then return; fi
            ;;
        WARN)
            if [ "$level" = "DEBUG" ] || [ "$level" = "INFO" ]; then return; fi
            ;;
        ERROR)
            if [ "$level" != "ERROR" ]; then return; fi
            ;;
    esac

    local line="[${timestamp}] [${level}] ${message}"
    echo "$line"

    # 如果 LOG_FILE 变量已定义，同步写入文件
    if [ -n "${LOG_FILE:-}" ]; then
        echo "$line" >> "$LOG_FILE"
    fi
}

# 便捷函数
log_info()  { log "INFO" "$@"; }
log_warn()  { log "WARN" "$@"; }
log_error() { log "ERROR" "$@"; }
log_debug() { log "DEBUG" "$@"; }

# 带 emoji 的分段标记
log_section_start() {
    log_info "===== $1 ====="
}

log_section_end() {
    local status="${1:-OK}"
    if [ "$status" = "OK" ]; then
        log_info "✅ $2 完成"
    else
        log_error "❌ $2 失败"
    fi
}
