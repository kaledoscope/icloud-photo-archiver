#!/bin/bash
# =============================================================
# load.sh — 统一配置装载入口（所有 bash 模块共用）
# =============================================================
# 设计原则：代码只负责功能，凡"随环境变化"的值一律放在 config/ 下。
# 本文件是读取配置的唯一入口，各模块不再自行解析配置。
#
# 用法（从任意模块）:
#     source "$PROJECT_DIR/config/load.sh"
#
# 装载内容:
#     config/config.env     公用配置（路径、阈值、开关）
#     config/secrets.env    个人 / 保密配置（邮箱、授权码、内网地址）
#
# 优先级:  调用者已设置的环境变量  >  配置文件
#     便于临时测试而不改动任何文件:
#         BACKUP_ROOT=/tmp/t bash modules/scheduler/photo_backup_weekly.sh
#
# 两个文件都允许缺失（缺失即该文件不提供任何值），
# 因此测试环境可以只放 config.env、不放 secrets.env。
# =============================================================

_ODC_CONFIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_odc_load_file() {
    local file="$1"
    [ -f "$file" ] || return 0

    # 1. 找出该文件会定义哪些变量
    local -a names=()
    local name
    while IFS= read -r name; do
        [ -n "$name" ] && names+=("$name")
    done < <(sed -n 's/^[[:space:]]*export[[:space:]]\{1,\}\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$file")

    # 2. 记住调用者已经设过的那些 —— 它们优先级更高，装载后要还原
    #    （用 eval 测变量是否存在，bash 3.2 没有 [ -v ]）
    local -a keep_names=() keep_values=()
    for name in "${names[@]}"; do
        if eval "[ -n \"\${${name}+x}\" ]"; then
            keep_names+=("$name")
            keep_values+=("$(eval "printf '%s' \"\${${name}}\"")")
        fi
    done

    # 3. 装载（set -a 让文件里的变量自动导出给子进程）
    set -a
    # shellcheck disable=SC1090
    source "$file"
    set +a

    # 4. 还原调用者的值
    local i
    for i in "${!keep_names[@]}"; do
        printf -v "${keep_names[$i]}" '%s' "${keep_values[$i]}"
        export "${keep_names[$i]}"
    done
}

_odc_load_file "$_ODC_CONFIG_DIR/config.env"
_odc_load_file "$_ODC_CONFIG_DIR/secrets.env"

unset -f _odc_load_file
unset _ODC_CONFIG_DIR
