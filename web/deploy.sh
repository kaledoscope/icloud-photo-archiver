#!/usr/bin/env bash
#
# deploy.sh — 构建前端并把服务装成常驻自启
#
# 用法:
#     web/deploy.sh                构建前端（后端本来就不需要构建）
#     web/deploy.sh --install      构建 + 装 launchd 常驻服务并启动
#     web/deploy.sh --status       看服务状态
#     web/deploy.sh --uninstall    停掉并卸载服务（不动任何文件）
#
# 这个脚本里没有删除操作，最重的动作是 launchctl bootout —— 那只是
# 把服务从 launchd 的注册表里摘掉，不碰磁盘上任何东西。

set -euo pipefail

WEB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$WEB_DIR/.." && pwd)"
FRONTEND_DIR="$WEB_DIR/frontend"
BACKEND_DIR="$WEB_DIR/backend"
STATE_DIR="$WEB_DIR/.state"
LOG_DIR="$STATE_DIR/logs"

LABEL="com.datacenter.photoweb"
PLIST_TEMPLATE="$WEB_DIR/launchd/$LABEL.plist.template"
PLIST_TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT=8787

info()  { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()    { printf '\033[32m✅\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m⚠️\033[0m %s\n' "$*"; }
die()   { printf '\033[31m❌\033[0m %s\n' "$*" >&2; exit 1; }

find_python() {
    # 优先项目虚拟环境；分类模块本身零依赖，没有 venv 时退回系统 python
    if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
        echo "$PROJECT_ROOT/.venv/bin/python"
    elif command -v python3.11 >/dev/null 2>&1; then
        command -v python3.11
    else
        command -v python3
    fi
}

ensure_backend_deps() {
    local py
    py="$(find_python)"
    if ! "$py" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
        info "安装后端依赖到 $PROJECT_ROOT/.venv"
        [ -x "$PROJECT_ROOT/.venv/bin/python" ] || "$py" -m venv "$PROJECT_ROOT/.venv"
        "$PROJECT_ROOT/.venv/bin/pip" install -q --upgrade pip
        "$PROJECT_ROOT/.venv/bin/pip" install -q -r "$BACKEND_DIR/requirements.txt"
    fi
    ok "后端依赖就绪（$(find_python)）"
}

build_frontend() {
    [ -d "$FRONTEND_DIR" ] || die "前端目录不存在: $FRONTEND_DIR"
    command -v npm >/dev/null 2>&1 || die "未找到 npm，请先安装 Node.js"

    if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
        info "安装前端依赖（首次会比较慢）"
        (cd "$FRONTEND_DIR" && npm install)
    fi

    info "构建前端"
    (cd "$FRONTEND_DIR" && npm run build)
    [ -f "$FRONTEND_DIR/dist/index.html" ] || die "构建结束但没有产出 dist/index.html"
    ok "前端已构建 → $FRONTEND_DIR/dist"
}

render_plist() {
    [ -f "$PLIST_TEMPLATE" ] || die "找不到 plist 模板: $PLIST_TEMPLATE"

    mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

    local py
    py="$(find_python)"
    # 路径里不该出现分隔符歧义字符；这里只做最基本的检查
    case "$PROJECT_ROOT" in
        *'&'*|*'|'*) die "项目路径含特殊字符，无法安全写入 plist: $PROJECT_ROOT" ;;
    esac

    sed -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
        -e "s|__PYTHON_BIN__|$py|g" \
        "$PLIST_TEMPLATE" > "$PLIST_TARGET"

    ok "已渲染 plist → $PLIST_TARGET"
}

service_loaded() {
    # 不用 `launchctl list | grep`：那个输出受调用方所在 domain 影响，
    # 曾在服务明明活着的时候报「未加载」，于是 install 跳过 bootout 直接
    # bootstrap → "Bootstrap failed: 5: Input/output error"，旧定义留在
    # launchd 里没被换掉。print 指定 domain，判断是准的。
    launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1
}

wait_unloaded() {
    # bootout 是异步的，立刻 bootstrap 会撞上还没摘干净的定义
    local i
    for i in $(seq 1 20); do
        service_loaded || return 0
        sleep 0.5
    done
    return 1
}

cmd_build() {
    ensure_backend_deps
    build_frontend
}

cmd_install() {
    cmd_build
    render_plist

    if service_loaded; then
        warn "服务已加载，先摘掉旧的定义再重新加载"
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        wait_unloaded || die "旧服务摘不掉，先手动跑: launchctl bootout gui/$(id -u)/$LABEL"
    fi

    info "加载服务"
    # 重装时换的是 plist 内容（比如环境变量），必须真的重新 bootstrap，
    # 否则 launchd 会继续用内存里那份旧定义，改了等于没改。
    launchctl bootstrap "gui/$(id -u)" "$PLIST_TARGET" \
        || { sleep 2; launchctl bootstrap "gui/$(id -u)" "$PLIST_TARGET"; }

    sleep 2
    if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
        ok "服务已启动：http://127.0.0.1:$PORT"
        ok "开机自启已生效，日志在 $LOG_DIR/"
    else
        warn "服务已加载但健康检查不通过，看看日志："
        printf '    tail -50 %s\n' "$LOG_DIR/photoweb.err.log"
        return 1
    fi
}

cmd_uninstall() {
    if service_loaded; then
        info "卸载服务（只从 launchd 摘除，不动任何文件）"
        launchctl bootout "gui/$(id -u)/$LABEL"
        ok "服务已卸载"
    else
        warn "服务本来就没在运行"
    fi
    printf '   plist 还在 %s，不需要了就自己删掉。\n' "$PLIST_TARGET"
}

cmd_status() {
    if service_loaded; then
        ok "launchd 服务已加载"
        launchctl list | grep "$LABEL" || true
    else
        warn "launchd 服务未加载"
    fi

    if curl -fsS "http://127.0.0.1:$PORT/api/health" 2>/dev/null; then
        echo
        ok "后端在跑"

        # 顺带把外部工具的可用情况报出来。ffprobe 缺失时视频会静默降级成
        # Unknown_Device，任务还报成功 —— 只有主动查才看得见。
        local missing
        missing="$(curl -fsS "http://127.0.0.1:$PORT/api/health" 2>/dev/null \
            | "$(find_python)" -c 'import json,sys; print(",".join(json.load(sys.stdin).get("missing_tools",[])))' 2>/dev/null || true)"
        if [ -n "$missing" ]; then
            warn "外部工具缺失：$missing —— 结果会静默降级，不要相信这次分类"
        else
            ok "外部工具就位（sips / ffprobe）"
        fi
    else
        warn "后端没响应 http://127.0.0.1:$PORT"
    fi
}

cmd_run() {
    # 前台跑，方便看日志。Ctrl-C 停。
    ensure_backend_deps
    info "前台启动 → http://127.0.0.1:$PORT  (Ctrl-C 停止)"
    cd "$PROJECT_ROOT"
    exec "$(find_python)" -m uvicorn app:app \
        --app-dir "$BACKEND_DIR" --host 127.0.0.1 --port "$PORT"
}

case "${1:-}" in
    "")              cmd_build ;;
    --install)       cmd_install ;;
    --uninstall)     cmd_uninstall ;;
    --status)        cmd_status ;;
    --run)           cmd_run ;;
    -h|--help)
        sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        ;;
    *)  die "未知参数: $1（试试 --help）" ;;
esac
