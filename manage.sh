#!/bin/bash

# OpenClaw 综合服务管理脚本
# 负责同时管理 FastAPI 后端服务器和静态网页前端服务器

if [ -f ".ENV" ]; then
    set -a
    . ./.ENV
    set +a
elif [ -f ".env" ]; then
    set -a
    . ./.env
    set +a
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ==========================================
# 配置变量区
# ==========================================
resolve_python_cmd() {
    local configured_cmd="${OPENCLAW_PYTHON_CMD:-}"
    local venv_cmd=".venv/bin/python"

    if [ -n "$configured_cmd" ]; then
        PYTHON_CMD="$configured_cmd"
    elif [ -x "$venv_cmd" ]; then
        PYTHON_CMD="$venv_cmd"
    else
        PYTHON_CMD="python3"
    fi

    # API requires uvicorn. If current interpreter misses it, fallback to local venv.
    if ! "$PYTHON_CMD" -c "import uvicorn" >/dev/null 2>&1; then
        if [ -x "$venv_cmd" ] && "$venv_cmd" -c "import uvicorn" >/dev/null 2>&1; then
            echo "⚠️  当前 Python ($PYTHON_CMD) 缺少 uvicorn，自动切换到 $venv_cmd"
            PYTHON_CMD="$venv_cmd"
        fi
    fi
}

resolve_python_cmd


# 后端 API 服务器配置
API_APP="server.py"
API_LOG="log/openclaw_api.log"
API_PID_FILE="log/openclaw_api.pid"

# 测试模式参数
API_APP_TEST="server.py --test"
API_LOG_TEST="log/openclaw_api_test.log"
API_PID_FILE_TEST="log/openclaw_api_test.pid"

# 前端 Web 服务器配置
WEB_PORT="${OPENCLAW_WEB_PORT}"
WEB_LOG="log/openclaw_web.log"
WEB_PID_FILE="log/openclaw_web.pid"

API_PORT="${OPENCLAW_API_PORT}"
API_SCHEME="${OPENCLAW_API_SCHEME:-http}"
API_HOST_PUBLIC="${OPENCLAW_API_PUBLIC_HOST:-127.0.0.1}"
PUBLIC_API_URL="${OPENCLAW_PUBLIC_API_URL:-${API_SCHEME}://${API_HOST_PUBLIC}:${API_PORT}}"
APP_VERSION="${OPENCLAW_APP_VERSION:-1.6.2}"
RUNTIME_CONFIG_FILE="runtime.config.js"

if [ -z "$API_PORT" ] || [ -z "$WEB_PORT" ]; then
    echo "❌ OPENCLAW_API_PORT / OPENCLAW_WEB_PORT 未配置，请在 .ENV 中设置后重试。"
    exit 1
fi

generate_runtime_config() {
        cat > "$RUNTIME_CONFIG_FILE" <<EOF
window.OPENCLAW_RUNTIME_CONFIG = {
    serverUrl: "$PUBLIC_API_URL",
    apiPort: "$API_PORT",
    appVersion: "$APP_VERSION"
};
EOF
        echo "已生成前端运行时配置: $RUNTIME_CONFIG_FILE (serverUrl=$PUBLIC_API_URL, appVersion=$APP_VERSION)"
}

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

port_in_use() {
    local PORT=$1
    if command -v ss >/dev/null 2>&1; then
        ss -ltn "sport = :$PORT" | awk 'NR>1 {found=1} END {exit found ? 0 : 1}'
        return $?
    fi

    if command -v lsof >/dev/null 2>&1; then
        lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
        return $?
    fi

    return 2
}

doctor() {
    echo "=========================================="
    echo "🩺 OpenClaw 环境自检"
    echo "------------------------------------------"

    local failed=0
    local warning=0

    local required_vars=(
        OPENCLAW_API_HOST
        OPENCLAW_API_PORT
        OPENCLAW_API_SCHEME
        OPENCLAW_API_PUBLIC_HOST
        OPENCLAW_PUBLIC_API_URL
        OPENCLAW_WEB_PORT
        OPENCLAW_PYTHON_CMD
        OPENCLAW_DB_FILE
        OPENCLAW_DB_FILE_TEST
        OPENCLAW_BROADCAST_FILE
        OPENCLAW_AUTO_KICK_MISS_STREAK
        OPENCLAW_FINGERPRINT_BAN_HOURS
        OPENCLAW_RECENT_ROUND_WINDOW
        OPENCLAW_LOW_SCORE_THRESHOLD
        OPENCLAW_PAIR_RECENT_PENALTY_WEIGHT
        OPENCLAW_PAIR_SCORE_DIFF_WEIGHT
        OPENCLAW_PAIR_LOW_SCORE_BIAS
        OPENCLAW_PAIR_JITTER_MAX
    )

    echo "【环境变量】"
    for var_name in "${required_vars[@]}"; do
        local var_value="${!var_name}"
        if [ -n "$var_value" ]; then
            echo "✅ $var_name=$var_value"
        else
            echo "❌ $var_name 未设置"
            failed=1
        fi
    done

    echo "------------------------------------------"
    echo "【关键文件】"
    local required_files=(
        server.py
        index.html
        manage.sh
        skill.md
        skill_test.md
        scripts/fingerprint.py
        assets/bg/bg.webp
        assets/font/Marcellus.ttf
        assets/font/Cinzel/Cinzel-Bold-2.otf
        assets/font/Cinzel/Cinzel-Regular-3.otf
        assets/font/noto-serif-sc/NotoSerifSC-Regular.otf
        assets/font/noto-serif-sc/NotoSerifSC-Medium.otf
        assets/font/noto-serif-sc/NotoSerifSC-SemiBold.otf
        assets/font/noto-serif-sc/NotoSerifSC-Bold.otf
    )

    for file_path in "${required_files[@]}"; do
        if [ -e "$file_path" ]; then
            echo "✅ $file_path"
        else
            echo "⚠️  $file_path 不存在"
        fi
    done

    echo "------------------------------------------"
    echo "【运行时配置】"
    if [ -f "$RUNTIME_CONFIG_FILE" ]; then
        echo "✅ $RUNTIME_CONFIG_FILE 已存在"
        if grep -q "serverUrl" "$RUNTIME_CONFIG_FILE"; then
            echo "✅ $RUNTIME_CONFIG_FILE 包含 serverUrl"
        else
            echo "❌ $RUNTIME_CONFIG_FILE 缺少 serverUrl 字段"
            failed=1
        fi
    else
        echo "❌ $RUNTIME_CONFIG_FILE 不存在，前端将回退到错误地址"
        echo "   修复：执行 ./manage.sh genconfig 或 ./manage.sh start"
        failed=1
    fi

    echo "------------------------------------------"
    echo "【HTTP 连通性】"
    if curl -fsS -m 5 "$PUBLIC_API_URL/api/scoreboard" >/dev/null 2>&1; then
        echo "✅ PUBLIC_API_URL 可访问: $PUBLIC_API_URL/api/scoreboard"
    else
        echo "⚠️  PUBLIC_API_URL 不可访问: $PUBLIC_API_URL/api/scoreboard"
        warning=1
    fi

    if curl -fsS -m 5 "http://127.0.0.1:${API_PORT}/api/scoreboard" >/dev/null 2>&1; then
        echo "✅ 本机 API 可访问: http://127.0.0.1:${API_PORT}/api/scoreboard"
    else
        echo "❌ 本机 API 不可访问: http://127.0.0.1:${API_PORT}/api/scoreboard"
        failed=1
    fi

    if curl -fsS -m 5 "http://127.0.0.1:${WEB_PORT}/runtime.config.js" >/dev/null 2>&1; then
        echo "✅ Web 可提供 runtime.config.js"
    else
        echo "❌ Web 无法提供 runtime.config.js（通常是文件缺失）"
        failed=1
    fi

    echo "------------------------------------------"
    echo "【端口检查】"
    if port_in_use "$API_PORT"; then
        echo "✅ API 端口 $API_PORT 正在监听或被占用"
    else
        local port_check_status=$?
        if [ "$port_check_status" -eq 1 ]; then
            echo "⚠️  API 端口 $API_PORT 当前未监听"
        else
            echo "⚠️  无法检测 API 端口 $API_PORT（缺少 ss/lsof）"
        fi
    fi

    if port_in_use "$WEB_PORT"; then
        echo "✅ WEB 端口 $WEB_PORT 正在监听或被占用"
    else
        local port_check_status=$?
        if [ "$port_check_status" -eq 1 ]; then
            echo "⚠️  WEB 端口 $WEB_PORT 当前未监听"
        else
            echo "⚠️  无法检测 WEB 端口 $WEB_PORT（缺少 ss/lsof）"
        fi
    fi

    echo "------------------------------------------"
    echo "【状态文件】"
    check_status "API 服务" "$API_PID_FILE"
    check_status "API 测试服务" "$API_PID_FILE_TEST"
    check_status "Web 前端服务" "$WEB_PID_FILE"

    echo "------------------------------------------"
    if [ "$failed" -eq 0 ] && [ "$warning" -eq 0 ]; then
        echo "✅ 自检完成：基础环境变量已配置。"
        echo "=========================================="
        return 0
    fi

    if [ "$failed" -eq 0 ] && [ "$warning" -ne 0 ]; then
        echo "⚠️  自检完成：有告警项，请按提示核查网络/公网配置。"
        echo "=========================================="
        return 0
    fi

    echo "❌ 自检完成：存在缺失项，请先修复后再启动。"
    echo "=========================================="
    return 1
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
    generate_runtime_config
    if [ "$1" = "test" ]; then
        start_service "API 测试服务" "$PYTHON_CMD $API_APP_TEST" "$API_LOG_TEST" "$API_PID_FILE_TEST"
        echo "▶️ [测试模式] API 日志: tail -f $API_LOG_TEST"
    else
        start_service "API 服务" "$PYTHON_CMD $API_APP" "$API_LOG" "$API_PID_FILE"
        echo "▶️ API 日志: tail -f $API_LOG"
    fi
    # 固定挂载脚本所在目录，避免从其它工作目录启动时静态资源路径丢失。
    start_service "Web 前端服务 (端口 $WEB_PORT)" "$PYTHON_CMD -m http.server $WEB_PORT --bind 0.0.0.0 --directory $SCRIPT_DIR" "$WEB_LOG" "$WEB_PID_FILE"
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
    local MODE="$1"
    local DB_FILE="data/openclaw_game.db"
    if [ "$MODE" = "test" ]; then
        DB_FILE="data/openclaw_game.db2"
    fi

    OPENCLAW_STATUS_DB_FILE="$DB_FILE" \
    OPENCLAW_STATUS_MODE="${MODE:-prod}" \
    OPENCLAW_STATUS_API_PID_FILE="$API_PID_FILE" \
    OPENCLAW_STATUS_API_TEST_PID_FILE="$API_PID_FILE_TEST" \
    OPENCLAW_STATUS_WEB_PID_FILE="$WEB_PID_FILE" \
    OPENCLAW_STATUS_PUBLIC_API_URL="$PUBLIC_API_URL" \
    OPENCLAW_STATUS_WEB_PORT="$WEB_PORT" \
    "$PYTHON_CMD" - <<'PY'
import os
import sqlite3
import datetime as dt


def pid_status(pid_file: str):
    if not pid_file or not os.path.exists(pid_file):
        return "未运行", "-"
    try:
        with open(pid_file, "r", encoding="utf-8") as f:
            pid_text = f.read().strip()
        pid = int(pid_text)
        os.kill(pid, 0)
        return "运行中", str(pid)
    except Exception:
        return "失效PID", pid_text if 'pid_text' in locals() else "-"


def load_db_snapshot(db_path: str):
    if not os.path.exists(db_path):
        return {
            "exists": False,
            "players": [],
            "active_round": None,
            "leaderboard": [],
        }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT player_id, nickname, total_score, registered_at FROM players ORDER BY total_score DESC")
    players = [dict(r) for r in cur.fetchall()]

    cur.execute(
        """
        SELECT round_id, game_date, hour, minute_slot, status
        FROM rounds
        WHERE status = 'active'
        ORDER BY round_id DESC
        LIMIT 1
        """
    )
    active_round = cur.fetchone()
    if not active_round:
        cur.execute(
            """
            SELECT round_id, game_date, hour, minute_slot, status
            FROM rounds
            ORDER BY round_id DESC
            LIMIT 1
            """
        )
        active_round = cur.fetchone()
    active_round = dict(active_round) if active_round else None

    cur.execute(
        """
        SELECT nickname, total_score
        FROM players
        ORDER BY total_score DESC, nickname ASC
        LIMIT 10
        """
    )
    leaderboard = [dict(r) for r in cur.fetchall()]

    match_count = 0
    speech_count = 0
    if active_round:
        rid = active_round["round_id"]
        cur.execute("SELECT COUNT(*) AS c FROM matches WHERE round_id = ?", (rid,))
        match_count = int(cur.fetchone()["c"])
        cur.execute("SELECT COUNT(*) AS c FROM round_speeches WHERE round_id = ?", (rid,))
        speech_count = int(cur.fetchone()["c"])

    cur.execute("SELECT COUNT(*) AS c FROM rounds")
    round_count = int(cur.fetchone()["c"])

    conn.close()
    return {
        "exists": True,
        "players": players,
        "active_round": active_round,
        "leaderboard": leaderboard,
        "round_count": round_count,
        "match_count": match_count,
        "speech_count": speech_count,
    }


mode = os.getenv("OPENCLAW_STATUS_MODE", "prod")
db_file = os.getenv("OPENCLAW_STATUS_DB_FILE", "data/openclaw_game.db")
public_api_url = os.getenv("OPENCLAW_STATUS_PUBLIC_API_URL", "-")
web_port = os.getenv("OPENCLAW_STATUS_WEB_PORT", "-")

svc_rows = [
    ("API 服务",) + pid_status(os.getenv("OPENCLAW_STATUS_API_PID_FILE", "")),
    ("API 测试服务",) + pid_status(os.getenv("OPENCLAW_STATUS_API_TEST_PID_FILE", "")),
    ("Web 前端服务",) + pid_status(os.getenv("OPENCLAW_STATUS_WEB_PID_FILE", "")),
]

snapshot = load_db_snapshot(db_file)
now_text = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.columns import Columns

    console = Console()

    title = f"OpenClaw Status Dashboard [{mode}]"
    subtitle = f"time={now_text} | db={db_file} | api={public_api_url} | web_port={web_port}"
    console.print(Panel(subtitle, title=title, border_style="cyan"))

    svc_table = Table(title="服务状态", expand=True)
    svc_table.add_column("服务")
    svc_table.add_column("状态")
    svc_table.add_column("PID", justify="right")
    for name, stat, pid in svc_rows:
        color = "green" if stat == "运行中" else ("yellow" if stat == "失效PID" else "red")
        svc_table.add_row(name, f"[{color}]{stat}[/{color}]", pid)

    summary = Table(title="游戏概览", expand=True)
    summary.add_column("项目")
    summary.add_column("值")
    summary.add_row("玩家数", str(len(snapshot.get("players", []))))
    summary.add_row("轮次数", str(snapshot.get("round_count", 0)))
    ar = snapshot.get("active_round")
    if ar:
        round_text = f"#{ar['round_id']} {ar['game_date']} {int(ar['hour']):02d}:{int(ar['minute_slot']) * 10:02d} ({ar['status']})"
    else:
        round_text = "暂无轮次"
    summary.add_row("当前轮次", round_text)
    summary.add_row("本轮对局数", str(snapshot.get("match_count", 0)))
    summary.add_row("本轮发言数", str(snapshot.get("speech_count", 0)))

    board = Table(title="得分榜 Top 10", expand=True)
    board.add_column("#", justify="right")
    board.add_column("玩家")
    board.add_column("总分", justify="right")
    for i, p in enumerate(snapshot.get("leaderboard", []), 1):
        board.add_row(str(i), str(p.get("nickname", "-")), str(p.get("total_score", 0)))
    if not snapshot.get("leaderboard"):
        board.add_row("-", "暂无玩家", "0")

    player_table = Table(title="参与玩家", expand=True)
    player_table.add_column("player_id")
    player_table.add_column("nickname")
    player_table.add_column("score", justify="right")
    for p in snapshot.get("players", []):
        player_table.add_row(
            str(p.get("player_id", "-")),
            str(p.get("nickname", "-")),
            str(p.get("total_score", 0)),
        )
    if not snapshot.get("players"):
        player_table.add_row("-", "暂无玩家", "0")

    console.print(Columns([svc_table, summary]))
    console.print(board)
    console.print(player_table)

except Exception:
    print("==========================================")
    print(f"OpenClaw Status [{mode}] @ {now_text}")
    print(f"DB={db_file} | API={public_api_url} | WEB_PORT={web_port}")
    print("------------------------------------------")
    for name, stat, pid in svc_rows:
        print(f"- {name}: {stat} (PID={pid})")
    print("------------------------------------------")
    print(f"玩家数: {len(snapshot.get('players', []))}")
    print(f"轮次数: {snapshot.get('round_count', 0)}")
    ar = snapshot.get("active_round")
    if ar:
        print(f"当前轮次: #{ar['round_id']} {ar['game_date']} {int(ar['hour']):02d}:{int(ar['minute_slot']) * 10:02d} ({ar['status']})")
    else:
        print("当前轮次: 暂无")
    print(f"本轮对局数: {snapshot.get('match_count', 0)}")
    print(f"本轮发言数: {snapshot.get('speech_count', 0)}")
    print("------------------------------------------")
    print("得分榜 Top 10")
    for i, p in enumerate(snapshot.get("leaderboard", []), 1):
        print(f"{i:>2}. {p.get('nickname', '-')}  {p.get('total_score', 0)}")
    if not snapshot.get("leaderboard"):
        print("(暂无玩家)")
    print("------------------------------------------")
    print("参与玩家")
    for p in snapshot.get("players", []):
        print(f"- {p.get('player_id', '-')}: {p.get('nickname', '-')} ({p.get('total_score', 0)})")
    if not snapshot.get("players"):
        print("(暂无玩家)")
    print("==========================================")
PY
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

# 新一轮游戏：备份数据库并重置赛季数据（保留玩家身份与指纹）
new_game() {
    echo "=========================================="
    stop
    NOW=$(date +"%Y%m%d_%H%M%S")
    mkdir -p data/records
    for DB in data/openclaw_game.db data/openclaw_game.db2; do
        BASE=$(basename "$DB")
        if [ -f "$DB" ]; then
            cp "$DB" "data/records/${BASE}_$NOW"
            "$PYTHON_CMD" - "$DB" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = {row[0] for row in cur.fetchall()}

reset_tables = [
    "matches",
    "rounds",
    "round_special_roles",
    "round_speeches",
    "round_public_speech",
    "player_achievements",
    "feature_event_log",
    "player_round_gambling",
    "round_vote_snapshots",
    "gambling_round_settlements",
    "fingerprint_bans",
]

for table_name in reset_tables:
    if table_name in tables:
        cur.execute(f"DELETE FROM {table_name}")

if "players" in tables:
    cur.execute("UPDATE players SET total_score = 0, miss_submit_streak = 0")

conn.commit()
conn.close()
PY
            echo "已备份并重置赛季数据(保留玩家/指纹): $DB -> data/records/${BASE}_$NOW"
        fi
    done
    start
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

sql_escape() {
    local RAW="$1"
    echo "${RAW//\'/\'\'}"
}

kick_player() {
    local TARGET="$1"
    local MODE="$2"
    local DB_FILE="data/openclaw_game.db"
    if [ "$MODE" = "test" ]; then
        DB_FILE="data/openclaw_game.db2"
    fi

    if [ -z "$TARGET" ]; then
        echo "用法: $0 kick <player_id|nickname> [test]"
        return 1
    fi

    if [ ! -f "$DB_FILE" ]; then
        echo "❌ 数据库不存在: $DB_FILE"
        return 1
    fi

    local SAFE_TARGET
    SAFE_TARGET=$(sql_escape "$TARGET")

    local PLAYER_ID
    if [[ "$TARGET" =~ ^OC-[A-Za-z0-9]+$ ]]; then
        PLAYER_ID=$(sqlite3 "$DB_FILE" "SELECT player_id FROM players WHERE player_id = '$SAFE_TARGET' LIMIT 1;")
    else
        PLAYER_ID=$(sqlite3 "$DB_FILE" "SELECT player_id FROM players WHERE nickname = '$SAFE_TARGET' ORDER BY registered_at ASC LIMIT 1;")
    fi

    if [ -z "$PLAYER_ID" ]; then
        echo "❌ 未找到玩家: $TARGET"
        return 1
    fi

    local SAFE_PID
    SAFE_PID=$(sql_escape "$PLAYER_ID")

    local PLAYER_ROW
    PLAYER_ROW=$(sqlite3 -separator '|' "$DB_FILE" "SELECT player_id, nickname, total_score FROM players WHERE player_id = '$SAFE_PID' LIMIT 1;")
    local MATCH_COUNT
    MATCH_COUNT=$(sqlite3 "$DB_FILE" "SELECT COUNT(1) FROM matches WHERE player1_id = '$SAFE_PID' OR player2_id = '$SAFE_PID';")

    echo "=========================================="
    echo "⚠️  即将踢出玩家: $PLAYER_ROW"
    echo "📊 将删除关联对局数量: $MATCH_COUNT"
    echo "📁 数据库: $DB_FILE"

    sqlite3 "$DB_FILE" "BEGIN;
        DELETE FROM round_speeches WHERE speaker_player_id = '$SAFE_PID';
        DELETE FROM round_special_roles WHERE speaker_player_id = '$SAFE_PID';
        DELETE FROM matches WHERE player1_id = '$SAFE_PID' OR player2_id = '$SAFE_PID';
        DELETE FROM players WHERE player_id = '$SAFE_PID';
    COMMIT;"

    local STILL_EXISTS
    STILL_EXISTS=$(sqlite3 "$DB_FILE" "SELECT COUNT(1) FROM players WHERE player_id = '$SAFE_PID';")
    if [ "$STILL_EXISTS" = "0" ]; then
        echo "✅ 玩家已踢出: $PLAYER_ID"
        echo "=========================================="
        return 0
    fi

    echo "❌ 踢人失败，请检查数据库锁或权限。"
    echo "=========================================="
    return 1
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
        if [ "$2" = "test" ]; then
            status test
        else
            status
        fi
        ;;
    new)
        new_game
        ;;
    tidy)
        tidy_files
        ;;
    genconfig)
        generate_runtime_config
        ;;
    doctor)
        doctor
        ;;
    roundstats)
        if [ "$2" = "test" ]; then
            round_stats test "$3"
        else
            round_stats "" "$2"
        fi
        ;;
    kick)
        if [ "$3" = "test" ] || [ "$2" = "test" ]; then
            if [ "$2" = "test" ]; then
                echo "用法: $0 kick <player_id|nickname> [test]"
                exit 1
            fi
            kick_player "$2" test
        else
            kick_player "$2"
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
        echo "  $0 status [test]   - Rich 控制台查看运行状态/游戏概览/玩家榜单（可选 test）"
        echo "  $0 new             - 新一轮游戏（备份并重置数据库）"
        echo "  $0 tidy            - 一键迁移根目录遗留文件到 data/log/scripts"
        echo "  $0 genconfig       - 按 .ENV 生成 runtime.config.js"
        echo "  $0 doctor          - 检查 .ENV、关键文件、端口和运行状态"
        echo "  $0 roundstats [test] [player_id] - 打印本轮+历史动作统计；可附带玩家ID查看该玩家全历史动作"
        echo "  $0 kick <player_id|nickname> [test] - 手动踢出玩家并删除关联对局"
        echo "  $0 broadcast <type> <content> - 发送服务器广播包"
        exit 1
        ;;
esac

exit 0