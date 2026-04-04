#!/bin/bash

# OpenClaw 综合服务管理脚本
# 负责同时管理 FastAPI 后端服务器和静态网页前端服务器

# ==========================================
# 配置变量区
# ==========================================
PYTHON_CMD="python3"

# 后端 API 服务器配置 (18187端口由 server.py 内部配置)
API_APP="server.py"
API_LOG="openclaw_api.log"
API_PID_FILE="openclaw_api.pid"

# 前端 Web 服务器配置
WEB_PORT="18186"
WEB_LOG="openclaw_web.log"
WEB_PID_FILE="openclaw_web.pid"

# ==========================================
# 核心功能函数
# ==========================================
start_service() {
    local NAME=$1
    local CMD=$2
    local LOG=$3
    local PID_F=$4

    if [ -f "$PID_F" ]; then
        PID=$(cat "$PID_F")
        if ps -p $PID > /dev/null; then
            echo "⚠️  [$NAME] 已经在运行中 (PID: $PID)"
            return 1
        else
            rm -f "$PID_F"
        fi
    fi

    echo "正在启动 [$NAME]..."
    nohup $CMD > "$LOG" 2>&1 &
    local NEW_PID=$!
    echo $NEW_PID > "$PID_F"
    echo "✅ [$NAME] 启动成功！进程 ID: $NEW_PID"
    return 0
}

stop_service() {
    local NAME=$1
    local PID_F=$2

    if [ -f "$PID_F" ]; then
        PID=$(cat "$PID_F")
        if ps -p $PID > /dev/null; then
            echo "正在停止 [$NAME] (PID: $PID)..."
            kill $PID
            while ps -p $PID > /dev/null; do sleep 1; done
            echo "🛑 [$NAME] 已停止。"
        fi
        rm -f "$PID_F"
    else
        echo "⚠️  [$NAME] 未运行。"
    fi
}

check_status() {
    local NAME=$1
    local PID_F=$2

    if [ -f "$PID_F" ]; then
        PID=$(cat "$PID_F")
        if ps -p $PID > /dev/null; then
            echo "🟢 [$NAME] 正在运行 (PID: $PID)"
        else
            echo "🔴 [$NAME] 未运行 (检测到失效的 PID 文件)"
        fi
    else
        echo "🔴 [$NAME] 未运行。"
    fi
}

start() {
    echo "=========================================="
    start_service "API 服务" "$PYTHON_CMD $API_APP" "$API_LOG" "$API_PID_FILE"
    # 使用 Python 内置的 http.server 挂载当前目录的网页
    start_service "Web 前端服务 (端口 $WEB_PORT)" "$PYTHON_CMD -m http.server $WEB_PORT" "$WEB_LOG" "$WEB_PID_FILE"
    echo "=========================================="
    echo "▶️ API 日志: tail -f $API_LOG"
    echo "▶️ Web 日志: tail -f $WEB_LOG"
}

stop() {
    echo "=========================================="
    stop_service "API 服务" "$API_PID_FILE"
    stop_service "Web 前端服务" "$WEB_PID_FILE"
    echo "=========================================="
}

status() {
    echo "=========================================="
    check_status "API 服务" "$API_PID_FILE"
    check_status "Web 前端服务" "$WEB_PID_FILE"
    echo "=========================================="
}


restart() {
    stop
    sleep 2
    start
}

# 新一轮游戏：备份数据库并重启
new_game() {
    echo "=========================================="
    NOW=$(date +"%Y%m%d_%H%M%S")
    mkdir -p records
    for DB in openclaw_game.db openclaw_game.db2; do
        if [ -f "$DB" ]; then
            cp "$DB" "records/${DB}_$NOW"
            : > "$DB"  # 清空原文件
            echo "已备份并清空 $DB -> records/${DB}_$NOW"
        fi
    done
    restart
    echo "新一轮游戏已开始。"
    echo "=========================================="
}

# ==========================================
# 脚本入口与参数解析
# ==========================================

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    new)
        new_game
        ;;
    *)
        echo "用法错误。请使用以下指令管理服务器："
        echo "  $0 start    - 启动所有服务"
        echo "  $0 stop     - 停止所有服务"
        echo "  $0 restart  - 重启所有服务"
        echo "  $0 status   - 查看运行状态"
        echo "  $0 new      - 新一轮游戏（备份并重置数据库）"
        exit 1
        ;;
esac

exit 0