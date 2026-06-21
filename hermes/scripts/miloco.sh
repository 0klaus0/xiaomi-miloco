#!/usr/bin/env bash
# miloco.sh — Miloco 全套服务一键管理
# 用法: ./miloco.sh {start|stop|restart|status}
#
# 默认目录结构（可通过环境变量覆盖）：
#   MILOCO_HOME      → ~/.hermes/miloco          (miloco 数据目录)
#   MILOCO_BACKEND   → miloco 源码目录            (后端源码位置)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MILOCO_HOME="${MILOCO_HOME:-$HOME/.hermes/miloco}"

SUPERVISOR_BIN="${SUPERVISOR_BIN:-$HOME/.local/bin/supervisord}"
SUPERVISORCTL="${SUPERVISORCTL:-$HOME/.local/bin/supervisorctl}"
SUPERVISOR_CONF="${MILOCO_HOME}/supervisord.conf"
SUPERVISOR_SOCK="${MILOCO_HOME}/supervisor.sock"
SUPERVISOR_PID="${MILOCO_HOME}/supervisord.pid"

BRIDGE_PID_FILE="${MILOCO_HOME}/miloco-bridge.pid"
BRIDGE_LOG="${MILOCO_HOME}/log/miloco-bridge.log"
BRIDGE_SCRIPT="${SCRIPT_DIR}/miloco-bridge.py"
BRIDGE_PORT="1811"

MILOCO_BACKEND_PORT="1810"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[miloco]${NC} $*"; }
warn() { echo -e "${YELLOW}[miloco]${NC} $*"; }
err()  { echo -e "${RED}[miloco]${NC} $*"; }

# ── 状态检查 ──────────────────────────────────────────────
is_supervisor_running() {
    [ -S "$SUPERVISOR_SOCK" ] && "$SUPERVISORCTL" -c "$SUPERVISOR_CONF" status &>/dev/null
}

is_bridge_running() {
    [ -f "$BRIDGE_PID_FILE" ] && kill -0 "$(cat "$BRIDGE_PID_FILE")" 2>/dev/null
}

is_backend_running() {
    curl -s --max-time 2 "http://127.0.0.1:${MILOCO_BACKEND_PORT}/health" &>/dev/null
}

# ── 启动 ──────────────────────────────────────────────────
do_start() {
    log "启动 Miloco 全套服务..."

    # 1. 启动 supervisord（管理 miloco-backend）
    if is_supervisor_running; then
        warn "supervisord 已在运行"
    else
        log "启动 supervisord..."
        mkdir -p "$(dirname "$SUPERVISOR_PID")"
        mkdir -p "$(dirname "$BRIDGE_LOG")"
        "$SUPERVISOR_BIN" -c "$SUPERVISOR_CONF"
        sleep 3
        if is_supervisor_running; then
            log "supervisord 启动成功"
        else
            err "supervisord 启动失败"
            return 1
        fi
    fi

    # 2. 确保 miloco-backend 在跑
    sleep 2
    local status
    status=$("$SUPERVISORCTL" -c "$SUPERVISOR_CONF" status miloco-backend 2>/dev/null | awk '{print $2}')
    if [ "$status" = "RUNNING" ]; then
        log "miloco-backend 已在运行"
    else
        log "启动 miloco-backend..."
        "$SUPERVISORCTL" -c "$SUPERVISOR_CONF" start miloco-backend &>/dev/null
        sleep 5
    fi

    # 3. 等待 backend 就绪
    log "等待 backend 就绪..."
    for i in $(seq 1 15); do
        if is_backend_running; then
            log "miloco-backend 就绪 (http://127.0.0.1:${MILOCO_BACKEND_PORT})"
            break
        fi
        [ "$i" -eq 15 ] && { err "backend 启动超时"; return 1; }
        sleep 2
    done

    # 4. 启动 miloco-bridge
    if is_bridge_running; then
        warn "miloco-bridge 已在运行"
    else
        # 释放可能被残留占用的端口
        local port_hex
        port_hex=$(printf '%04X' "${BRIDGE_PORT}")
        awk -v p="$port_hex" '$2 ~ /:'"$p"'$/ && $4=="0A" {print $10}' /proc/net/tcp 2>/dev/null | while read -r inode; do
            pid=$(find /proc/[0-9]*/fd -lname "socket:\\[$inode\\]" 2>/dev/null | head -1 | cut -d/ -f3)
            [ -n "$pid" ] && kill "$pid" 2>/dev/null
        done
        sleep 1
        log "启动 miloco-bridge..."
        python3 "$BRIDGE_SCRIPT" --port "$BRIDGE_PORT" >> "$BRIDGE_LOG" 2>&1 &
        local bridge_pid=$!
        echo "$bridge_pid" > "$BRIDGE_PID_FILE"
        sleep 3
        if kill -0 "$bridge_pid" 2>/dev/null; then
            log "miloco-bridge 启动成功 (port ${BRIDGE_PORT}, pid ${bridge_pid})"
        else
            err "miloco-bridge 启动失败，查看日志: $BRIDGE_LOG"
            return 1
        fi
    fi

    log "全部服务启动完成 ✅"
    do_status
}

# ── 停止 ──────────────────────────────────────────────────
do_stop() {
    log "停止 Miloco 全套服务..."

    # 1. 停 bridge
    if is_bridge_running; then
        log "停止 miloco-bridge..."
        kill "$(cat "$BRIDGE_PID_FILE")" 2>/dev/null || true
        rm -f "$BRIDGE_PID_FILE"
        sleep 1
        log "miloco-bridge 已停止"
    else
        warn "miloco-bridge 未运行"
    fi

    # 2. 停 supervisord（连带停 backend）
    if is_supervisor_running; then
        log "停止 supervisord + miloco-backend..."
        "$SUPERVISORCTL" -c "$SUPERVISOR_CONF" stop miloco-backend &>/dev/null || true
        "$SUPERVISORCTL" -c "$SUPERVISOR_CONF" shutdown &>/dev/null || true

        # 硬杀残留
        if [ -f "$SUPERVISOR_PID" ]; then
            kill "$(cat "$SUPERVISOR_PID")" 2>/dev/null || true
            rm -f "$SUPERVISOR_PID"
        fi
        pkill -f "miloco.main" 2>/dev/null || true
        log "supervisord 已停止"
    else
        # supervisord 没跑，但 backend 可能还在
        pkill -f "miloco.main" 2>/dev/null && log "残留 miloco-backend 已清理" || warn "miloco-backend 未运行"
    fi

    rm -f "$SUPERVISOR_SOCK"
    log "全部服务已停止"
}

# ── 重启 ──────────────────────────────────────────────────
do_restart() {
    do_stop
    sleep 2
    do_start
}

# ── 状态 ──────────────────────────────────────────────────
do_status() {
    echo ""
    echo "═══════════════════════════════════════"
    echo "  Miloco 服务状态"
    echo "═══════════════════════════════════════"

    # Backend (via supervisor)
    if is_supervisor_running; then
        local b_status
        b_status=$("$SUPERVISORCTL" -c "$SUPERVISOR_CONF" status miloco-backend 2>/dev/null)
        echo -e "  miloco-backend: ${GREEN}$b_status${NC}"
    else
        echo -e "  supervisord:    ${RED}stopped${NC}"
        echo -e "  miloco-backend: ${RED}stopped${NC}"
    fi

    # Bridge
    if is_bridge_running; then
        echo -e "  miloco-bridge:  ${GREEN}running (pid $(cat $BRIDGE_PID_FILE))${NC}"
    else
        echo -e "  miloco-bridge:  ${RED}stopped${NC}"
    fi

    # Health
    if is_backend_running; then
        echo -e "  health check:   ${GREEN}ok${NC}"
    else
        echo -e "  health check:   ${RED}fail${NC}"
    fi

    # Ports
    echo "  ───────────────────────────────────"
    echo "  Web UI:  http://127.0.0.1:${MILOCO_BACKEND_PORT}"
    echo "  Bridge:  http://127.0.0.1:${BRIDGE_PORT}"
    echo "═══════════════════════════════════════"
    echo ""
}

# ── 入口 ──────────────────────────────────────────────────
case "${1:-}" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart) do_restart ;;
    status)  do_status ;;
    *)
        echo "用法: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
