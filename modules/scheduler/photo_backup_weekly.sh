#!/bin/bash
# photo_backup_weekly.sh — 周一+周四执行的备份流水线
#
# 被 launchd 在每周一和周四凌晨 3:00 调度执行。
# launchd Weekday: 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat, 7=Sun.
# 也可以手动运行测试。
#
# 本文件只做【编排】：按顺序调用各模块，收集输出，决定后续步骤走不走。
# 所有判断逻辑都在 modules/ 下的 Python 模块里 —— 这里不该出现内联 Python。
#
# 流水线（按正确时序，第一性原理设计）:
#   Pre-flight  CloudDrive2 挂载健康 + Photos 库访问权限
#   0. 验证老文件已在远端归档（FATAL，失败则阻塞后续清理）
#   1. 清理本地过期中转文件（clean_staging.py → 废纸篓）
#   2. iCloud/Photos 可清理报告（icloud_report.py → 列出可手动删除的年月）
#   3. 等待 iCloud 同步 → 导出新增照片（photo_export.py）
#   4. rsync 备份到 115 网盘（backup_to_115.sh）
#   5. NAS 冷备（backup_to_nas.sh，仅每月第一周周一且 NAS_ENABLED=true）
#   6. 通知（macOS 通知 + 邮件报告）
#
# 核心安全原则:
#   - Step 0 只验证"即将被 Step 1 清理的老文件"，确保远端已有归档
#   - 保留期内的文件不验证（即使远端缺失，本次 rsync 也会传上去）
#   - Step 0 失败 → PIPELINE_OK=false → 跳过清理/导出/rsync → 发告警邮件
#   - 先清理再导出，避免刚删除的照片又被重新导出
#
# 用法:
#   bash photo_backup_weekly.sh          # 完整流水线
#   bash photo_backup_weekly.sh --test   # 测试模式（不实际备份）

set -euo pipefail

# ===== 配置 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
MODULES_DIR="$PROJECT_DIR/modules"
CONFIG_DIR="$PROJECT_DIR/config"

# 装载配置（config.env + secrets.env）
# ⚠️ 必须在派生任何路径之前 —— 否则 LOG_DIR 会基于旧值算出来，
#    改了 config 却不生效（历史坑）
source "$CONFIG_DIR/load.sh"

# 缺配置直接报错退出，不用硬编码路径兜底 —— 兜底会让"配置没生效"变成静默故障
BACKUP_ROOT="${BACKUP_ROOT:?未配置 BACKUP_ROOT。请检查 config/config.env}"
PYTHON_BIN="${PYTHON_BIN:?未配置 PYTHON_BIN。请检查 config/config.env}"
LOG_DIR="${BACKUP_ROOT}/.logs"
PIPELINE_LOG="${LOG_DIR}/pipeline.log"

# 临时目录必须是【全局】变量：清理它的 EXIT trap 在 main 返回之后才触发，
# 那时候 main 里的 local 已经出栈，set -u 下会报 unbound variable，
# 而失败的 EXIT trap 会把整个脚本的退出码改成 1 —— 每次成功运行都变成"失败"。
TMP_DIR=""

# Python 模块统一以包的形式调用（modules/ 是包，PYTHONPATH 指向项目根）
run_py() { PYTHONPATH="$PROJECT_DIR" "$PYTHON_BIN" -m "$@"; }

# 从模块输出里取机器可读结果字段: result_field <输出文件> <字段名>
result_field() {
    local line
    line=$(grep -m1 '\[RESULT\]' "$1" 2>/dev/null || true)
    [ -n "$line" ] || return 0
    printf '%s\n' "$line" | tr ' ' '\n' | grep "^$2=" | head -1 | cut -d= -f2 || true
}

# 加载共享工具
if [ -f "$MODULES_DIR/utils/logger.sh" ]; then
    source "$MODULES_DIR/utils/logger.sh"
fi
if [ -f "$MODULES_DIR/utils/notify.sh" ]; then
    source "$MODULES_DIR/utils/notify.sh"
fi

log() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local line="[${timestamp}] $*"
    echo "$line"
    mkdir -p "$LOG_DIR"
    echo "$line" >> "$PIPELINE_LOG"
}

# 把模块输出逐行转进日志，并同时留一份到报告里
relay() {
    local prefix="$1" file="$2"
    while IFS= read -r line; do
        log "  $line"
    done < "$file"
}

# ===== 主流水线 =====
main() {
    local test_mode=false
    for arg in "$@"; do
        case "$arg" in
            --test) test_mode=true ;;
        esac
    done

    log "========================================="
    log "照片备份流水线启动"
    log "========================================="
    [ "$test_mode" = true ] && log "[测试模式]"

    local PIPELINE_OK=true
    local START_TIME
    START_TIME=$(date +%s)
    local RUN_TS
    RUN_TS=$(date '+%Y-%m-%d %H:%M:%S')

    # 报告文件（邮件附件：详细 TXT）
    local REPORT_DIR="${LOG_DIR}/reports"
    mkdir -p "$REPORT_DIR"
    local REPORT_FILE="${REPORT_DIR}/report-$(date '+%Y%m%d-%H%M%S').txt"
    report_detail() { echo "$*" >> "$REPORT_FILE"; }

    # 摘要行（邮件正文：每步一句话）
    local SUMMARY_LINES=""

    report_detail "═══════════════════════════════════════════════"
    report_detail "  O-Data-Center-System · 备份详细报告"
    report_detail "═══════════════════════════════════════════════"
    report_detail ""
    report_detail "执行时间: $RUN_TS"
    report_detail ""

    TMP_DIR=$(mktemp -d)
    trap 'rm -rf "${TMP_DIR:-}"' EXIT

    # ═══════════════════════════════════════════════
    # Pre-flight: 环境检查
    # ═══════════════════════════════════════════════
    log ""
    log "━━━ Pre-flight: CloudDrive2 健康检查 ━━━"
    bash "$MODULES_DIR/utils/clouddrive_watchdog.sh" || true

    log ""
    log "━━━ Pre-flight: Photos 库访问权限 ━━━"
    local tcc_ok=true
    run_py modules.preflight.check_photos_access > "$TMP_DIR/preflight.txt" 2>&1 || tcc_ok=false
    relay "" "$TMP_DIR/preflight.txt"
    log ""

    # ═══════════════════════════════════════════════
    # Step 0: 归档验证
    # 只验证即将被 Step 1 清理的老文件；缺一个就 FATAL，阻塞后续所有删除动作
    # ═══════════════════════════════════════════════
    log "━━━ Step 0/6: 验证老文件已在远端归档 ━━━"
    local verify_ok=0
    run_py modules.export.verify_backup > "$TMP_DIR/step0.txt" 2>&1 || verify_ok=$?
    relay "[verify]" "$TMP_DIR/step0.txt"

    local verify_pass verify_fail
    verify_pass=$(result_field "$TMP_DIR/step0.txt" passed)
    verify_fail=$(result_field "$TMP_DIR/step0.txt" failed)
    report_detail "$(cat "$TMP_DIR/step0.txt")"

    if [ "$verify_ok" -eq 0 ]; then
        log "✅ Step 0 完成: 老文件已在远端安全归档"
        SUMMARY_LINES+=$'\n'"  Step 0  归档验证      ✅ ${verify_pass:-0} 个老文件已归档"
    else
        log "❌ Step 0 失败: 老文件在远端缺失，流水线阻塞！"
        log "   为防止误删未归档数据，跳过清理和导出步骤"
        SUMMARY_LINES+=$'\n'"  Step 0  归档验证      ❌ ${verify_fail:-?} 项未归档 — 阻塞！"
        PIPELINE_OK=false
    fi

    # ═══════════════════════════════════════════════
    # Step 1: 清理本地过期中转文件
    # 放在导出和 rsync 之前，减少中转目录体积，rsync 不必扫描历史垃圾
    # ═══════════════════════════════════════════════
    if [ "$test_mode" = false ] && [ "$PIPELINE_OK" = true ]; then
        log ""
        log "━━━ Step 1/6: 清理本地过期中转文件 ━━━"
        run_py modules.cleanup.clean_staging > "$TMP_DIR/step1.txt" 2>&1 || true
        relay "" "$TMP_DIR/step1.txt"

        local cleaned kept
        cleaned=$(result_field "$TMP_DIR/step1.txt" cleaned)
        kept=$(result_field "$TMP_DIR/step1.txt" kept)

        report_detail ""
        report_detail "━━━ Step 1: 本地中转清理 ━━━"
        report_detail "$(cat "$TMP_DIR/step1.txt")"

        if [ "${cleaned:-0}" -gt 0 ]; then
            log "✅ Step 1 完成: ${cleaned} 个目录 → 废纸篓, 保留 ${kept:-0} 个"
            SUMMARY_LINES+=$'\n'"  Step 1  清理过期中转  ✅ ${cleaned} 目录 -> 🗑"
        else
            log "✅ Step 1 完成: 没有需要清理的过期目录"
            SUMMARY_LINES+=$'\n'"  Step 1  清理过期中转  ✅ 无需清理"
        fi
    elif [ "$test_mode" = true ]; then
        log ""
        log "━━━ Step 1/6: 跳过清理（测试模式）━━━"
    else
        log ""
        log "━━━ Step 1/6: 跳过清理（Step 0 归档验证未通过）━━━"
        SUMMARY_LINES+=$'\n'"  Step 1  清理过期中转  ⏭ 验证未通过"
    fi

    # ═══════════════════════════════════════════════
    # Step 2: Photos.app / iCloud 可清理报告
    # 放在导出之前，避免刚删除的照片又被导出一次
    # macOS Sequoia 的 Photos 10.0 封了脚本删除接口，所以只出报告，用户手动删
    # ═══════════════════════════════════════════════
    if [ "$test_mode" = false ] && [ "$PIPELINE_OK" = true ] && [ "${ENABLE_ICLOUD_CLEANUP:-false}" = "true" ]; then
        log ""
        log "━━━ Step 2/6: iCloud/Photos 可清理报告 ━━━"
        run_py modules.cleanup.icloud_report > "$TMP_DIR/step2.txt" 2>&1 || true
        relay "" "$TMP_DIR/step2.txt"

        report_detail ""
        report_detail "━━━ Step 2: iCloud/Photos 可清理报告 ━━━"
        report_detail "$(cat "$TMP_DIR/step2.txt")"

        log "✅ Step 2 完成: 报告已生成"
        SUMMARY_LINES+=$'\n'"  Step 2  iCloud清理     📋 报告已生成（手动删）"
    elif [ "$test_mode" = true ]; then
        log ""
        log "━━━ Step 2/6: 跳过 iCloud/Photos 报告（测试模式）━━━"
    elif [ "${ENABLE_ICLOUD_CLEANUP:-false}" = "false" ]; then
        log ""
        log "━━━ Step 2/6: 跳过 iCloud/Photos 报告（ENABLE_ICLOUD_CLEANUP=false）━━━"
        SUMMARY_LINES+=$'\n'"  Step 2  iCloud清理     ⏭ 未启用"
    else
        log ""
        log "━━━ Step 2/6: 跳过 iCloud/Photos 报告（Step 0 归档验证未通过）━━━"
        SUMMARY_LINES+=$'\n'"  Step 2  iCloud清理     ⏭ 验证未通过"
    fi

    # ═══════════════════════════════════════════════
    # Step 3: 等待 iCloud 同步 → 导出新增照片
    # 清理之后才导出，中转目录此时只含窗口期内文件 + 本次新增
    # ═══════════════════════════════════════════════
    if [ "$test_mode" = false ] && [ "$PIPELINE_OK" = true ]; then
        log ""
        log "━━━ Pre-export: 检查 iCloud 照片同步状态 ━━━"
        local icloud_max_wait=1800
        if pgrep -f cloudphotod > /dev/null 2>&1; then
            log "cloudphotod 正在运行，等待 iCloud 同步完成..."
            local icloud_wait=0 cpu_pct=""
            while [ $icloud_wait -lt $icloud_max_wait ]; do
                # CPU < 5% 视为同步基本结束；cloudphotod 退出则彻底结束
                cpu_pct=$(ps -eo pid,pcpu,comm | awk '/cloudphotod/ && !/awk/ {print $2}' | head -1)
                if [ -z "$cpu_pct" ]; then
                    log "cloudphotod 已退出，同步完成"
                    break
                fi
                if [ "$(echo "$cpu_pct < 5" | bc -l 2>/dev/null || echo 0)" = "1" ]; then
                    log "cloudphotod 低负载 ($cpu_pct% CPU)，同步基本完成"
                    break
                fi
                sleep 30
                icloud_wait=$((icloud_wait + 30))
                if [ $((icloud_wait % 120)) -eq 0 ]; then
                    log "  iCloud 同步中... (${icloud_wait}s / ${icloud_max_wait}s, CPU ${cpu_pct}%)"
                fi
            done
            if [ $icloud_wait -ge $icloud_max_wait ]; then
                log "⚠️  iCloud 同步等待超时，继续导出（可能有照片未同步）"
            else
                log "✅ iCloud 同步已就绪"
            fi
        else
            log "cloudphotod 未运行，跳过同步等待"
        fi
    fi

    if [ "$test_mode" = false ] && [ "$PIPELINE_OK" != true ]; then
        log ""
        log "━━━ Step 3/6: 跳过导出（前置步骤未通过）━━━"
        SUMMARY_LINES+=$'\n'"  Step 3  照片导出      ⏭ 前置步骤失败"
    else
        log ""
        log "━━━ Step 3/6: 照片导出与分类 ━━━"
        local export_ok=0
        if [ "$test_mode" = true ]; then
            run_py modules.export.photo_export --dry-run > "$TMP_DIR/step3.txt" 2>&1 || export_ok=$?
        else
            run_py modules.export.photo_export > "$TMP_DIR/step3.txt" 2>&1 || export_ok=$?
        fi
        relay "[export]" "$TMP_DIR/step3.txt"

        local export_count export_fail
        export_count=$(result_field "$TMP_DIR/step3.txt" exported)
        export_fail=$(result_field "$TMP_DIR/step3.txt" failed)
        report_detail "$(cat "$TMP_DIR/step3.txt")"

        if [ "$export_ok" -eq 0 ] && [ "${export_fail:-0}" -eq 0 ]; then
            log "✅ Step 3 完成: 照片导出"
            SUMMARY_LINES+=$'\n'"  Step 3  照片导出      ✅ ${export_count:-0} 文件"
        else
            log "❌ Step 3 失败: 照片导出"
            SUMMARY_LINES+=$'\n'"  Step 3  照片导出      ❌ 失败 ${export_fail:-?} 个"
            PIPELINE_OK=false
        fi
    fi

    # ═══════════════════════════════════════════════
    # Step 4: rsync 备份到 115 网盘
    # ═══════════════════════════════════════════════
    if [ "$test_mode" = false ] && [ "$PIPELINE_OK" = true ]; then
        log ""
        log "━━━ Step 4/6: 备份到 115 网盘 ━━━"
        local step4_ok=0
        bash "$MODULES_DIR/backup/115/backup_to_115.sh" > "$TMP_DIR/step4.txt" 2>&1 || step4_ok=$?
        relay "[115]" "$TMP_DIR/step4.txt"

        report_detail ""
        report_detail "━━━ Step 4: 115 网盘同步 ━━━"
        grep -E '(Number of files|transferred|Total file size|speedup|sent.*bytes|✅|❌)' \
            "$TMP_DIR/step4.txt" 2>/dev/null | while IFS= read -r stats_line; do
            report_detail "  $stats_line"
        done

        if [ "$step4_ok" -eq 0 ]; then
            log "✅ Step 4 完成: 115 备份"
            SUMMARY_LINES+=$'\n'"  Step 4  115 同步       ✅ 完成"
        else
            log "❌ Step 4 失败: 115 备份"
            SUMMARY_LINES+=$'\n'"  Step 4  115 同步       ❌ 失败"
            PIPELINE_OK=false
        fi
    elif [ "$test_mode" = true ]; then
        log ""
        log "━━━ Step 4/6: 跳过 115 备份（测试模式）━━━"
    fi

    # ═══════════════════════════════════════════════
    # Step 5: NAS 冷备（仅每月第一周周一，且 NAS_ENABLED=true）
    # ═══════════════════════════════════════════════
    local current_week=0 do_nas_backup=false
    local interval="${NAS_BACKUP_WEEKS_INTERVAL:-4}"
    if [ "$interval" -gt 0 ] && [ "${NAS_ENABLED:-false}" = "true" ]; then
        # date +%W 可能有前导零，10# 强制按十进制解释，否则被当成八进制
        current_week=$((10#$(date +%W)))
        # 只在周一跑（date +%u: 1=Mon, 7=Sun），避免同一周周四重复跑
        if [ $((current_week % interval)) -eq 0 ] && [ "$(date +%u)" = "1" ]; then
            do_nas_backup=true
        fi
    fi

    if [ "${NAS_ENABLED:-false}" != "true" ]; then
        log ""
        log "━━━ Step 5/6: 跳过 NAS 备份（NAS_ENABLED=false）━━━"
        SUMMARY_LINES+=$'\n'"  Step 5  NAS 冷备      ⏭ 未启用"
    elif [ "$test_mode" = false ] && [ "$PIPELINE_OK" = true ] && [ "$do_nas_backup" = true ]; then
        log ""
        log "━━━ Step 5/6: 备份到 NAS (冷备份周 周一, wk=$current_week) ━━━"
        local step5_ok=0
        bash "$MODULES_DIR/backup/nas/backup_to_nas.sh" > "$TMP_DIR/step5.txt" 2>&1 || step5_ok=$?
        relay "[NAS]" "$TMP_DIR/step5.txt"
        report_detail ""
        report_detail "━━━ Step 5: NAS 冷备 ━━━"
        report_detail "$(cat "$TMP_DIR/step5.txt")"

        if [ "$step5_ok" -eq 0 ]; then
            log "✅ Step 5 完成: NAS 备份"
            SUMMARY_LINES+=$'\n'"  Step 5  NAS 冷备      ✅ 完成"
        else
            # NAS 失败不阻塞整体 —— 115 才是主备份
            log "⚠️  Step 5 部分失败: NAS 备份"
            SUMMARY_LINES+=$'\n'"  Step 5  NAS 冷备      ⚠️ 部分失败"
        fi
    elif [ "$test_mode" = true ]; then
        log ""
        log "━━━ Step 5/6: 跳过 NAS 备份（测试模式）━━━"
    else
        log ""
        log "━━━ Step 5/6: 跳过 NAS 备份（非冷备周, wk=$current_week）━━━"
        SUMMARY_LINES+=$'\n'"  Step 5  NAS 冷备      ⏭ 非冷备周"
    fi

    # ═══════════════════════════════════════════════
    # Step 6: 通知与汇总
    # ═══════════════════════════════════════════════
    log ""
    log "━━━ Step 6/6: 汇总 ━━━"

    local end_time duration minutes seconds elapsed
    end_time=$(date +%s)
    duration=$((end_time - START_TIME))
    minutes=$((duration / 60))
    seconds=$((duration % 60))
    elapsed="${minutes}m ${seconds}s"

    report_detail ""
    report_detail "总耗时: $elapsed"
    report_detail ""
    report_detail "═══════════════════════════════════════════"

    local status_icon status_line
    if [ "$PIPELINE_OK" = true ]; then
        status_icon="✅"
        status_line="全部完成"
    else
        status_icon="❌"
        status_line="有步骤失败，详见附件 TXT"
    fi

    local email_body
    email_body=$(printf '%s\n' \
        "ODC 备份报告 · $(date '+%Y-%m-%d %H:%M')" \
        "" \
        "${status_icon} ${status_line}  (耗时 ${elapsed})" \
        "" \
        "${SUMMARY_LINES}" \
        "" \
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" \
        "📋 附件 TXT 含:" \
        "  Step 0  归档验证（逐文件 stat 比对）" \
        "  Step 1  中转清理（各目录处置记录）" \
        "  Step 2  iCloud 清理建议（按年月列出）" \
        "  Step 3  导出日志（新增/大小/设备分布）" \
        "  Step 4  115 rsync 统计（文件数/传输量）" \
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # 清理旧报告（与中转保留期限一致）
    local report_retention_days=$(( ${STAGING_RETENTION_MONTHS:-3} * 30 ))
    find "$REPORT_DIR" -name "report-*.txt" -mtime +"${report_retention_days}" -delete 2>/dev/null || true

    if [ "${EMAIL_ENABLED:-false}" = "true" ]; then
        log "邮件通知: 已启用"
    else
        log "邮件通知: 未启用（EMAIL_ENABLED=false）"
    fi

    if [ "$PIPELINE_OK" = true ]; then
        log "========================================="
        log "✅ 流水线全部完成 (耗时 $elapsed)"
        log "========================================="
        if [ "$test_mode" = false ]; then
            notify_backup_complete 2>/dev/null || true
            run_py modules.utils.email_notify \
                "✅ ODC · $(date '+%m/%d %H:%M')" \
                "$email_body" \
                --attach "$REPORT_FILE" 2>/dev/null || true
        fi
    else
        log "========================================="
        log "❌ 流水线有失败项 (耗时 $elapsed)"
        log "========================================="
        if [ "$test_mode" = false ]; then
            notify_backup_failed "流水线" 2>/dev/null || true
            run_py modules.utils.email_notify \
                "❌ ODC · $(date '+%m/%d %H:%M')" \
                "$email_body" \
                --attach "$REPORT_FILE" 2>/dev/null || true
        fi
    fi
}

main "$@"
