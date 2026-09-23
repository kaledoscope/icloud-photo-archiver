#!/bin/bash
# notify.sh — macOS 原生通知中心集成
# 被其他脚本 source 使用
#
# 只管系统通知。邮件不走这里 —— 主干直接调 modules/utils/email_notify.py。

# 发送系统通知
# 参数: title message [sound]
# sound: "default" (有声音), "" (静默), "Glass", "Ping", 等
send_notification() {
    local title="$1"
    local message="$2"
    local sound="${3:-}"

    if [ -n "$sound" ]; then
        osascript -e "
            display notification \"$message\" \
            with title \"$title\" \
            sound name \"$sound\"
        " 2>/dev/null || true
    else
        osascript -e "
            display notification \"$message\" \
            with title \"$title\"
        " 2>/dev/null || true
    fi
}

# 成功通知（静默）
notify_success() {
    send_notification "📸 $1" "$2" ""
}

# 警告通知（有声音）
notify_warning() {
    send_notification "⚠️ $1" "$2" "Glass"
}

# 错误通知（有声音）
notify_error() {
    send_notification "❌ $1" "$2" "Basso"
}

# 备份流水线专用
notify_backup_complete() {
    notify_success "照片备份" "本周备份流水线全部完成 ✅"
}

notify_backup_failed() {
    local step="$1"
    notify_error "照片备份" "${step} 失败，请检查日志"
}
