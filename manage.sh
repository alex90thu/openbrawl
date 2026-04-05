#!/bin/bash

# OpenClaw 综合服务管理脚本
# 负责同时管理 FastAPI 后端服务器和静态网页前端服务器

# ==========================================
# 配置变量区
# ==========================================
PYTHON_CMD="python3"


# 后端 API 服务器配置 (18187端口由 server.py 内部配置)
API_APP="server.py"
API_LOG="log/openclaw_api.log"
API_PID_FILE="log/openclaw_api.pid"

# 测试模式参数
API_APP_TEST="server.py --test"
API_LOG_TEST="log/openclaw_api_test.log"
API_PID_FILE_TEST="log/openclaw_api_test.pid"

# 前端 Web 服务器配置
WEB_PORT="18186"
WEB_LOG="log/openclaw_web.log"
WEB_PID_FILE="log/openclaw_web.pid"

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

tidy_files() {
    echo "=========================================="
    mkdir -p data log scripts data/records

    for f in openclaw_api.log openclaw_api_test.log openclaw_web.log openclaw_server.log; do
        if [ -f "$f" ]; then
            mv -f "$f" "log/$f"
            echo "已迁移日志: $f -> log/$f"
        fi
    done

    for f in openclaw_api.pid openclaw_api_test.pid openclaw_web.pid; do
        if [ -f "$f" ]; then
            mv -f "$f" "log/$f"
            echo "已迁移 PID: $f -> log/$f"
        fi
    done

    for f in openclaw_game.db openclaw_game.db2; do
        if [ -f "$f" ]; then
            mv -f "$f" "data/$f"
            echo "已迁移数据库: $f -> data/$f"
        fi
    done

    if [ -f "broadcast.py" ]; then
        mv -f "broadcast.py" "scripts/broadcast.py"
        echo "已迁移脚本: broadcast.py -> scripts/broadcast.py"
    fi

    echo "根目录迁移扫描完成。"
    echo "=========================================="
}


start() {
    echo "=========================================="
    if [ "$1" = "test" ]; then
        start_service "API 测试服务" "$PYTHON_CMD $API_APP_TEST" "$API_LOG_TEST" "$API_PID_FILE_TEST"
        echo "▶️ [测试模式] API 日志: tail -f $API_LOG_TEST"
    else
        start_service "API 服务" "$PYTHON_CMD $API_APP" "$API_LOG" "$API_PID_FILE"
        echo "▶️ API 日志: tail -f $API_LOG"
    fi
    # 使用 Python 内置的 http.server 挂载当前目录的网页
    start_service "Web 前端服务 (端口 $WEB_PORT)" "$PYTHON_CMD -m http.server $WEB_PORT" "$WEB_LOG" "$WEB_PID_FILE"
    echo "=========================================="
    echo "▶️ Web 日志: tail -f $WEB_LOG"
}


stop() {
    echo "=========================================="
    stop_service "API 服务" "$API_PID_FILE"
    stop_service "API 测试服务" "$API_PID_FILE_TEST"
    stop_service "Web 前端服务" "$WEB_PID_FILE"
    echo "=========================================="
}

status() {
    echo "=========================================="
    check_status "API 服务" "$API_PID_FILE"
    check_status "API 测试服务" "$API_PID_FILE_TEST"
    check_status "Web 前端服务" "$WEB_PID_FILE"
    echo "=========================================="
}



restart() {
    stop
    sleep 2
    if [ "$1" = "test" ]; then
        start test
    else
        start
    fi
}

# 新一轮游戏：备份数据库并重启
new_game() {
    echo "=========================================="
    NOW=$(date +"%Y%m%d_%H%M%S")
    mkdir -p data/records
    for DB in data/openclaw_game.db data/openclaw_game.db2; do
        BASE=$(basename "$DB")
        if [ -f "$DB" ]; then
            cp "$DB" "data/records/${BASE}_$NOW"
            : > "$DB"  # 清空原文件
            echo "已备份并清空 $DB -> data/records/${BASE}_$NOW"
        fi
    done
    restart
    echo "新一轮游戏已开始。"
    echo "=========================================="
}

# 本轮+历史决策统计（仅屏幕打印）
round_stats() {
    local MODE="$1"
    local TARGET_PLAYER="$2"
    local DB_FILE="data/openclaw_game.db"
    if [ "$MODE" = "test" ]; then
        DB_FILE="data/openclaw_game.db2"
    fi

    if [ ! -f "$DB_FILE" ]; then
        echo "❌ 数据库不存在: $DB_FILE"
        return 1
    fi

    local ROUND_ID
    ROUND_ID=$(sqlite3 "$DB_FILE" "SELECT round_id FROM rounds WHERE status='active' ORDER BY round_id DESC LIMIT 1;")
    if [ -z "$ROUND_ID" ]; then
        ROUND_ID=$(sqlite3 "$DB_FILE" "SELECT round_id FROM rounds ORDER BY round_id DESC LIMIT 1;")
    fi

    if [ -z "$ROUND_ID" ]; then
        echo "⚠️ 当前数据库还没有轮次记录。"
        return 0
    fi

    echo "=========================================="
    echo "📊 决策统计 (${MODE:-prod})"
    echo "📁 数据库: $DB_FILE"
    echo "🎯 轮次 ID: $ROUND_ID"
    echo "------------------------------------------"

    sqlite3 -cmd ".mode table" "$DB_FILE" "
        SELECT round_id, game_date, hour, minute_slot, status
        FROM rounds WHERE round_id = $ROUND_ID;
    "

    echo "------------------------------------------"
    echo "【动作汇总】"
    sqlite3 -cmd ".mode table" "$DB_FILE" "
        WITH actions AS (
            SELECT player1_id AS player_id, COALESCE(player1_action, '(未提交)') AS action FROM matches WHERE round_id = $ROUND_ID
            UNION ALL
            SELECT player2_id AS player_id, COALESCE(player2_action, '(未提交)') AS action FROM matches WHERE round_id = $ROUND_ID
        )
        SELECT action AS decision, COUNT(*) AS count
        FROM actions
        GROUP BY action
        ORDER BY CASE action WHEN 'C' THEN 1 WHEN 'D' THEN 2 ELSE 3 END;
    "

    echo "------------------------------------------"
    echo "【玩家明细】"
    sqlite3 -cmd ".mode table" "$DB_FILE" "
        WITH actions AS (
            SELECT player1_id AS player_id, COALESCE(player1_action, '(未提交)') AS action FROM matches WHERE round_id = $ROUND_ID
            UNION ALL
            SELECT player2_id AS player_id, COALESCE(player2_action, '(未提交)') AS action FROM matches WHERE round_id = $ROUND_ID
        )
        SELECT p.player_id, p.nickname, GROUP_CONCAT(a.action, ',') AS actions
        FROM actions a
        LEFT JOIN players p ON p.player_id = a.player_id
        GROUP BY p.player_id, p.nickname
        ORDER BY p.player_id;
    "

    echo "------------------------------------------"
    echo "【历史动作汇总】"
    sqlite3 -cmd ".mode table" "$DB_FILE" "
        WITH all_actions AS (
            SELECT player1_action AS action FROM matches
            UNION ALL
            SELECT player2_action AS action FROM matches
        )
        SELECT COALESCE(action, '(未提交)') AS decision, COUNT(*) AS count
        FROM all_actions
        GROUP BY COALESCE(action, '(未提交)')
        ORDER BY CASE COALESCE(action, '(未提交)') WHEN 'C' THEN 1 WHEN 'D' THEN 2 ELSE 3 END;
    "

    echo "------------------------------------------"
    echo "【所有玩家历史动作】"
    sqlite3 -cmd ".mode table" "$DB_FILE" "
        WITH all_actions AS (
            SELECT round_id, player1_id AS player_id, player1_action AS action FROM matches WHERE player1_action IS NOT NULL
            UNION ALL
            SELECT round_id, player2_id AS player_id, player2_action AS action FROM matches WHERE player2_action IS NOT NULL
        ),
        ordered_actions AS (
            SELECT * FROM all_actions ORDER BY round_id ASC
        )
        SELECT p.player_id,
               p.nickname,
               SUM(CASE WHEN oa.action = 'C' THEN 1 ELSE 0 END) AS c_count,
               SUM(CASE WHEN oa.action = 'D' THEN 1 ELSE 0 END) AS d_count,
               COUNT(*) AS total_actions,
               GROUP_CONCAT(oa.action, '') AS action_sequence
        FROM ordered_actions oa
        LEFT JOIN players p ON p.player_id = oa.player_id
        GROUP BY p.player_id, p.nickname
        ORDER BY total_actions DESC, p.player_id;
    "

    if [ -n "$TARGET_PLAYER" ]; then
        echo "------------------------------------------"
        echo "【指定玩家历史动作】player_id=$TARGET_PLAYER"
        sqlite3 -cmd ".mode table" "$DB_FILE" "
            WITH player_actions AS (
                SELECT round_id, player1_id AS player_id, player2_id AS opponent_id, player1_action AS action FROM matches
                UNION ALL
                SELECT round_id, player2_id AS player_id, player1_id AS opponent_id, player2_action AS action FROM matches
            )
            SELECT pa.round_id,
                   r.game_date,
                   r.hour,
                   r.minute_slot,
                   pa.opponent_id,
                   COALESCE(pa.action, '(未提交)') AS action
            FROM player_actions pa
            JOIN rounds r ON r.round_id = pa.round_id
            WHERE pa.player_id = '$TARGET_PLAYER'
            ORDER BY pa.round_id ASC;
        "

        sqlite3 -cmd ".mode table" "$DB_FILE" "
            WITH player_actions AS (
                SELECT round_id, player1_id AS player_id, player1_action AS action FROM matches
                UNION ALL
                SELECT round_id, player2_id AS player_id, player2_action AS action FROM matches
            )
            SELECT '$TARGET_PLAYER' AS player_id,
                   COALESCE(GROUP_CONCAT(action, ''), '(暂无已提交动作)') AS all_submitted_actions
            FROM (
                SELECT action
                FROM player_actions
                WHERE player_id = '$TARGET_PLAYER' AND action IS NOT NULL
                ORDER BY round_id ASC
            );
        "
    fi

    echo "=========================================="
}

# ==========================================
# 脚本入口与参数解析
# ==========================================


case "$1" in
    start)
        if [ "$2" = "test" ]; then
            start test
        else
            start
        fi
        ;;
    stop)
        stop
        ;;
    restart)
        if [ "$2" = "test" ]; then
            restart test
        else
            restart
        fi
        ;;
    status)
        status
        ;;
    new)
        new_game
        ;;
    tidy)
        tidy_files
        ;;
    roundstats)
        if [ "$2" = "test" ]; then
            round_stats test "$3"
        else
            round_stats "" "$2"
        fi
        ;;

    broadcast)
        if [ $# -lt 3 ]; then
            echo "用法: $0 broadcast <type> <content>"
            exit 1
        fi
        $PYTHON_CMD scripts/broadcast.py "$2" "$3"
        ;;
    *)
        echo "用法错误。请使用以下指令管理服务器："
        echo "  $0 start [test]    - 启动所有服务（加 test 为测试模式）"
        echo "  $0 stop            - 停止所有服务"
        echo "  $0 restart [test]  - 重启所有服务（加 test 为测试模式）"
        echo "  $0 status          - 查看运行状态"
        echo "  $0 new             - 新一轮游戏（备份并重置数据库）"
        echo "  $0 tidy            - 一键迁移根目录遗留文件到 data/log/scripts"
        echo "  $0 roundstats [test] [player_id] - 打印本轮+历史动作统计；可附带玩家ID查看该玩家全历史动作"
        echo "  $0 broadcast <type> <content> - 发送服务器广播包"
        exit 1
        ;;
esac

exit 0