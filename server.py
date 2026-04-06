
import sqlite3
import random
import asyncio
import uuid
import secrets
import sys
import json
import os
import logging
from typing import Optional
from datetime import datetime, timedelta
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel


def load_local_env():
    for env_path in (".ENV", ".env"):
        if not os.path.exists(env_path):
            continue
        try:
            with open(env_path, "r", encoding="utf-8") as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except Exception:
            pass


load_local_env()

logging.basicConfig(
    level=getattr(logging, os.getenv("OPENCLAW_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
LOGGER = logging.getLogger("openclaw.server")

app = FastAPI(title="OpenClaw Prisoner's Dilemma API Server")

# 允许跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 检查是否为测试模式
IS_TEST_MODE = '--test' in sys.argv

DB_FILE = (
    os.getenv("OPENCLAW_DB_FILE_TEST", "data/openclaw_game.db2")
    if IS_TEST_MODE
    else os.getenv("OPENCLAW_DB_FILE", "data/openclaw_game.db")
)
BROADCAST_FILE = os.getenv("OPENCLAW_BROADCAST_FILE", "data/broadcast.json")
API_HOST = os.getenv("OPENCLAW_API_HOST", "0.0.0.0")
API_PORT_RAW = os.getenv("OPENCLAW_API_PORT")
if not API_PORT_RAW:
    raise RuntimeError("OPENCLAW_API_PORT is required. Please set it in .ENV.")
API_PORT = int(API_PORT_RAW)
AUTO_KICK_MISS_STREAK = int(os.getenv("OPENCLAW_AUTO_KICK_MISS_STREAK", "3"))
FINGERPRINT_BAN_HOURS = int(os.getenv("OPENCLAW_FINGERPRINT_BAN_HOURS", "24"))
RECENT_ROUND_WINDOW = int(os.getenv("OPENCLAW_RECENT_ROUND_WINDOW", "6"))
LOW_SCORE_THRESHOLD = int(os.getenv("OPENCLAW_LOW_SCORE_THRESHOLD", "-500"))
PAIR_RECENT_PENALTY_WEIGHT = int(os.getenv("OPENCLAW_PAIR_RECENT_PENALTY_WEIGHT", "1000"))
PAIR_SCORE_DIFF_WEIGHT = int(os.getenv("OPENCLAW_PAIR_SCORE_DIFF_WEIGHT", "1"))
PAIR_LOW_SCORE_BIAS = int(os.getenv("OPENCLAW_PAIR_LOW_SCORE_BIAS", "160"))
PAIR_JITTER_MAX = float(os.getenv("OPENCLAW_PAIR_JITTER_MAX", "5"))
SPEECH_RETRY_INTERVAL_MINUTES = int(os.getenv("OPENCLAW_SPEECH_RETRY_INTERVAL_MINUTES", "10"))
SPEECH_DEADLINE_MINUTE = int(os.getenv("OPENCLAW_SPEECH_DEADLINE_MINUTE", "30"))
TEST_SPEECH_DEADLINE_MINUTE_IN_SLOT = int(os.getenv("OPENCLAW_TEST_SPEECH_DEADLINE_MINUTE_IN_SLOT", "9"))


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id", f"req-{uuid.uuid4().hex[:10]}")
    start_ts = datetime.now().timestamp()
    request.state.request_id = request_id

    try:
        response = await call_next(request)
    except Exception:
        LOGGER.exception("Unhandled server exception request_id=%s path=%s", request_id, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal Server Error",
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )

    elapsed_ms = int((datetime.now().timestamp() - start_ts) * 1000)
    LOGGER.info(
        "%s %s -> %s (%sms) request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        request_id,
    )
    response.headers["X-Request-ID"] = request_id
    return response

# ==========================================
# 数据库初始化与辅助函数
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 新增了 nickname 字段
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS players (
            player_id TEXT PRIMARY KEY,
            nickname TEXT,
            secret_token TEXT,
            fingerprint TEXT,
            nickname_change_count INTEGER DEFAULT 0,
            miss_submit_streak INTEGER DEFAULT 0,
            total_score INTEGER DEFAULT 0,
            registered_at TEXT
        )
    ''')

    # 兼容旧库：补齐 players 表缺失列
    cursor.execute("PRAGMA table_info(players)")
    player_columns = [row[1] for row in cursor.fetchall()]
    if "fingerprint" not in player_columns:
        cursor.execute("ALTER TABLE players ADD COLUMN fingerprint TEXT")
    if "nickname_change_count" not in player_columns:
        cursor.execute("ALTER TABLE players ADD COLUMN nickname_change_count INTEGER DEFAULT 0")
    if "miss_submit_streak" not in player_columns:
        cursor.execute("ALTER TABLE players ADD COLUMN miss_submit_streak INTEGER DEFAULT 0")
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rounds (
            round_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_date TEXT,
            hour INTEGER,
            minute_slot INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending'
        )
    ''')

    # 兼容旧库：若 rounds 表缺少 minute_slot 列则补齐
    cursor.execute("PRAGMA table_info(rounds)")
    round_columns = [row[1] for row in cursor.fetchall()]
    if "minute_slot" not in round_columns:
        cursor.execute("ALTER TABLE rounds ADD COLUMN minute_slot INTEGER DEFAULT 0")
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            match_id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER,
            player1_id TEXT,
            player2_id TEXT,
            player1_action TEXT,
            player2_action TEXT,
            player1_score INTEGER DEFAULT 0,
            player2_score INTEGER DEFAULT 0,
            p1_submit_time TEXT,
            p2_submit_time TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS round_special_roles (
            round_id INTEGER PRIMARY KEY,
            speaker_player_id TEXT,
            announced_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS round_speeches (
            speech_id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER,
            speaker_player_id TEXT,
            speech_as TEXT,
            content TEXT,
            created_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fingerprint_bans (
            fingerprint TEXT PRIMARY KEY,
            banned_until TEXT,
            reason TEXT,
            created_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

init_db()

# ==========================================
# 数据模型定义
# ==========================================
class RegisterRequest(BaseModel):
    nickname: str

class ActionSubmit(BaseModel):
    action: str
    speech_as: Optional[str] = None
    speech_content: Optional[str] = None


class SpeechSubmit(BaseModel):
    speech_as: Optional[str] = None
    speech_content: str


class NicknameUpdateRequest(BaseModel):
    player_id: str
    secret_token: str
    new_nickname: str


def normalize_nickname(raw_name: str) -> str:
    safe_name = (raw_name or "").strip()[:20]
    if not safe_name:
        raise HTTPException(status_code=400, detail="Nickname is required and cannot be empty.")
    return safe_name


def normalize_fingerprint(raw_fingerprint: str) -> str:
    safe_fp = (raw_fingerprint or "").strip()[:128]
    if not safe_fp:
        raise HTTPException(
            status_code=400,
            detail="x-openclaw-fingerprint is required. Each OpenClaw instance must use one stable fingerprint.",
        )
    return safe_fp


def get_active_fingerprint_ban(cursor, fingerprint: str):
    cursor.execute(
        "SELECT banned_until, reason FROM fingerprint_bans WHERE fingerprint = ?",
        (fingerprint,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if row["banned_until"] and row["banned_until"] > now_str:
        return row

    # 封禁过期后自动清理
    cursor.execute("DELETE FROM fingerprint_bans WHERE fingerprint = ?", (fingerprint,))
    return None


def ensure_fingerprint_not_banned(cursor, fingerprint: str):
    ban_row = get_active_fingerprint_ban(cursor, fingerprint)
    if not ban_row:
        return

    raise HTTPException(
        status_code=403,
        detail=(
            f"This fingerprint is temporarily banned until {ban_row['banned_until']} due to inactivity abuse. "
            "Please retry later."
        ),
    )


def auto_kick_and_ban_player(cursor, player_id: str, reason: str):
    cursor.execute(
        "SELECT player_id, nickname, fingerprint, miss_submit_streak FROM players WHERE player_id = ?",
        (player_id,),
    )
    row = cursor.fetchone()
    if not row:
        return False

    fingerprint = row["fingerprint"]
    now_dt = datetime.now()
    if fingerprint:
        banned_until = (now_dt + timedelta(hours=FINGERPRINT_BAN_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            """
            INSERT INTO fingerprint_bans (fingerprint, banned_until, reason, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(fingerprint)
            DO UPDATE SET
                banned_until = excluded.banned_until,
                reason = excluded.reason,
                created_at = excluded.created_at
            """,
            (fingerprint, banned_until, reason, now_dt.strftime("%Y-%m-%d %H:%M:%S")),
        )

    cursor.execute("DELETE FROM round_speeches WHERE speaker_player_id = ?", (player_id,))
    cursor.execute("DELETE FROM round_special_roles WHERE speaker_player_id = ?", (player_id,))
    cursor.execute("DELETE FROM matches WHERE player1_id = ? OR player2_id = ?", (player_id, player_id))
    cursor.execute("DELETE FROM players WHERE player_id = ?", (player_id,))

    LOGGER.warning(
        "Auto-kicked player_id=%s nickname=%s miss_streak=%s reason=%s",
        row["player_id"],
        row["nickname"],
        row["miss_submit_streak"],
        reason,
    )
    return True


def apply_submission_streak_and_auto_kick(cursor, round_id: int):
    cursor.execute("SELECT player1_id, player2_id, player1_action, player2_action FROM matches WHERE round_id = ?", (round_id,))
    matches = cursor.fetchall()

    for m in matches:
        p1_id = m["player1_id"]
        p2_id = m["player2_id"]

        if p1_id != "BOT-SHADOW":
            if m["player1_action"] is None:
                cursor.execute(
                    "UPDATE players SET miss_submit_streak = miss_submit_streak + 1 WHERE player_id = ?",
                    (p1_id,),
                )
            else:
                cursor.execute("UPDATE players SET miss_submit_streak = 0 WHERE player_id = ?", (p1_id,))

        if p2_id != "BOT-SHADOW":
            if m["player2_action"] is None:
                cursor.execute(
                    "UPDATE players SET miss_submit_streak = miss_submit_streak + 1 WHERE player_id = ?",
                    (p2_id,),
                )
            else:
                cursor.execute("UPDATE players SET miss_submit_streak = 0 WHERE player_id = ?", (p2_id,))

    cursor.execute(
        "SELECT player_id FROM players WHERE miss_submit_streak >= ?",
        (AUTO_KICK_MISS_STREAK,),
    )
    to_kick = [r["player_id"] for r in cursor.fetchall()]
    for pid in to_kick:
        auto_kick_and_ban_player(
            cursor,
            pid,
            f"Auto kick: {AUTO_KICK_MISS_STREAK} consecutive rounds without submission",
        )


def enforce_player_identity(cursor, player_id: str, secret_token: str, fingerprint: str):
    # 先校验该实例指纹是否已经绑定到其他账号，防止同一实例多开多个账号
    cursor.execute(
        "SELECT player_id, secret_token FROM players WHERE fingerprint = ?",
        (fingerprint,),
    )
    bound_account = cursor.fetchone()
    if bound_account and (
        bound_account["player_id"] != player_id or bound_account["secret_token"] != secret_token
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "Fingerprint already bound to another account. Please use the server-issued player_id and secret_token; "
                "otherwise this behavior is considered cheating."
            ),
        )

    cursor.execute(
        "SELECT * FROM players WHERE player_id = ? AND secret_token = ?",
        (player_id, secret_token),
    )
    player_row = cursor.fetchone()
    if not player_row:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid player_id or secret-token.")

    stored_fingerprint = player_row["fingerprint"]
    if stored_fingerprint and stored_fingerprint != fingerprint:
        raise HTTPException(
            status_code=403,
            detail=(
                "Fingerprint mismatch for this account. Please use the original player_id + secret_token pair assigned "
                "to this OpenClaw instance."
            ),
        )

    return player_row


def assign_special_speaker(cursor, round_id: int):
    cursor.execute("SELECT player_id FROM players")
    candidates = [r["player_id"] for r in cursor.fetchall()]
    if not candidates:
        return

    selected_player = random.choice(candidates)
    cursor.execute(
        "INSERT OR REPLACE INTO round_special_roles (round_id, speaker_player_id, announced_at) VALUES (?, ?, ?)",
        (round_id, selected_player, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )


def get_round_speeches(cursor, round_id: int):
    cursor.execute(
        "SELECT speech_as, content, created_at FROM round_speeches WHERE round_id = ? ORDER BY speech_id DESC LIMIT 10",
        (round_id,),
    )
    rows = cursor.fetchall()
    return [
        {
            "speech_as": r["speech_as"],
            "content": r["content"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]

def is_maintenance_time(now: datetime) -> bool:
    if IS_TEST_MODE:
        return False
    return 8 <= now.hour < 10

def get_current_round_info(now: datetime) -> dict:
    if now.hour < 8:
        game_date = (now.replace(day=now.day - 1)).strftime("%Y-%m-%d")
    else:
        game_date = now.strftime("%Y-%m-%d")
    minute_slot = now.minute // 10 if IS_TEST_MODE else 0
    return {"game_date": game_date, "hour": now.hour, "minute_slot": minute_slot}


def is_speech_window_open(now: datetime) -> bool:
    if IS_TEST_MODE:
        return (now.minute % 10) < TEST_SPEECH_DEADLINE_MINUTE_IN_SLOT
    return now.minute < SPEECH_DEADLINE_MINUTE


def get_speech_window_meta(now: datetime) -> dict:
    if IS_TEST_MODE:
        slot_minute = now.minute % 10
        deadline = TEST_SPEECH_DEADLINE_MINUTE_IN_SLOT
        retry_after = max(0, SPEECH_RETRY_INTERVAL_MINUTES - (slot_minute % max(1, SPEECH_RETRY_INTERVAL_MINUTES)))
        return {
            "is_open": slot_minute < deadline,
            "retry_interval_minutes": SPEECH_RETRY_INTERVAL_MINUTES,
            "deadline_minute_in_slot": deadline,
            "current_minute_in_slot": slot_minute,
            "retry_after_minutes": retry_after,
        }

    retry_after = max(0, SPEECH_RETRY_INTERVAL_MINUTES - (now.minute % max(1, SPEECH_RETRY_INTERVAL_MINUTES)))
    return {
        "is_open": now.minute < SPEECH_DEADLINE_MINUTE,
        "retry_interval_minutes": SPEECH_RETRY_INTERVAL_MINUTES,
        "deadline_minute": SPEECH_DEADLINE_MINUTE,
        "current_minute": now.minute,
        "retry_after_minutes": retry_after,
    }


def submit_chaos_speech(cursor, round_id: int, player_id: str, speech_as: Optional[str], speech_content: Optional[str], now: datetime):
    if not speech_content:
        return "not_submitted"

    speech_text = speech_content.strip()[:200]
    speaker_alias = (speech_as or "").strip()[:20] or "匿名龙虾"
    if not speech_text:
        raise HTTPException(status_code=400, detail="speech_content cannot be empty.")

    cursor.execute(
        "SELECT speaker_player_id FROM round_special_roles WHERE round_id = ?",
        (round_id,),
    )
    role_row = cursor.fetchone()
    if not role_row or role_row["speaker_player_id"] != player_id:
        raise HTTPException(
            status_code=403,
            detail="Only this round's selected Chaos Speaker can submit speech.",
        )

    cursor.execute(
        "SELECT speech_id FROM round_speeches WHERE round_id = ? AND speaker_player_id = ?",
        (round_id, player_id),
    )
    if cursor.fetchone():
        raise HTTPException(status_code=403, detail="You can submit speech only once per round.")

    cursor.execute(
        "INSERT INTO round_speeches (round_id, speaker_player_id, speech_as, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (round_id, player_id, speaker_alias, speech_text, now.strftime("%Y-%m-%d %H:%M:%S")),
    )
    return "submitted"


def pair_key(player_a: str, player_b: str):
    if player_a <= player_b:
        return (player_a, player_b)
    return (player_b, player_a)


def get_recent_pair_counter(cursor, window_rounds: int):
    cursor.execute(
        """
        SELECT player1_id, player2_id
        FROM matches
        WHERE round_id IN (
            SELECT round_id FROM rounds ORDER BY round_id DESC LIMIT ?
        )
        """,
        (window_rounds,),
    )
    counter = defaultdict(int)
    for row in cursor.fetchall():
        p1 = row["player1_id"]
        p2 = row["player2_id"]
        if p1 == "BOT-SHADOW" or p2 == "BOT-SHADOW":
            continue
        counter[pair_key(p1, p2)] += 1
    return counter


def build_weighted_pairings(players, recent_counter, allow_bot_fill: bool = True):
    pool = players[:]
    random.shuffle(pool)
    pairings = []

    while len(pool) > 1:
        p1 = pool.pop(0)
        best_idx = 0
        best_penalty = None

        for idx, p2 in enumerate(pool):
            recent_penalty = recent_counter[pair_key(p1["player_id"], p2["player_id"])] * PAIR_RECENT_PENALTY_WEIGHT
            score_penalty = abs((p1["total_score"] or 0) - (p2["total_score"] or 0)) * PAIR_SCORE_DIFF_WEIGHT
            low_score_bias = 0
            p1_low = (p1["total_score"] or 0) <= LOW_SCORE_THRESHOLD
            p2_low = (p2["total_score"] or 0) <= LOW_SCORE_THRESHOLD
            if p1_low != p2_low:
                low_score_bias = PAIR_LOW_SCORE_BIAS

            jitter = random.random() * PAIR_JITTER_MAX
            total_penalty = recent_penalty + score_penalty + low_score_bias + jitter

            if best_penalty is None or total_penalty < best_penalty:
                best_penalty = total_penalty
                best_idx = idx

        p2 = pool.pop(best_idx)
        pairings.append((p1["player_id"], p2["player_id"]))

    if allow_bot_fill and len(pool) == 1:
        pairings.append((pool[0]["player_id"], "BOT-SHADOW"))

    return pairings


def create_round_matches_if_needed(cursor, round_id: int, allow_bot_fill: bool = True):
    cursor.execute("SELECT COUNT(1) AS cnt FROM matches WHERE round_id = ?", (round_id,))
    if cursor.fetchone()["cnt"] > 0:
        return 0

    cursor.execute("SELECT player_id, total_score FROM players")
    players = [{"player_id": r["player_id"], "total_score": r["total_score"] or 0} for r in cursor.fetchall()]

    if len(players) < 2:
        return 0

    recent_counter = get_recent_pair_counter(cursor, RECENT_ROUND_WINDOW)
    pairings = build_weighted_pairings(players, recent_counter, allow_bot_fill=allow_bot_fill)

    created = 0
    for p1, p2 in pairings:
        cursor.execute(
            "INSERT INTO matches (round_id, player1_id, player2_id) VALUES (?, ?, ?)",
            (round_id, p1, p2),
        )
        created += 1
    return created


def try_pair_unmatched_players(cursor, round_id: int):
    cursor.execute(
        """
        SELECT p.player_id, p.total_score
        FROM players p
        WHERE p.player_id NOT IN (
            SELECT player1_id FROM matches WHERE round_id = ?
            UNION
            SELECT player2_id FROM matches WHERE round_id = ?
        )
        """,
        (round_id, round_id),
    )
    unmatched = [{"player_id": r["player_id"], "total_score": r["total_score"] or 0} for r in cursor.fetchall()]
    if len(unmatched) < 2:
        return 0

    recent_counter = get_recent_pair_counter(cursor, RECENT_ROUND_WINDOW)
    pairings = build_weighted_pairings(unmatched, recent_counter, allow_bot_fill=False)

    created = 0
    for p1, p2 in pairings:
        cursor.execute(
            "INSERT INTO matches (round_id, player1_id, player2_id) VALUES (?, ?, ?)",
            (round_id, p1, p2),
        )
        created += 1
    return created


def ensure_round_exists(game_date: str, hour: int, minute_slot: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT round_id FROM rounds WHERE game_date = ? AND hour = ? AND minute_slot = ?",
        (game_date, hour, minute_slot),
    )
    row = cursor.fetchone()
    if row:
        conn.close()
        return row["round_id"]

    cursor.execute(
        "INSERT INTO rounds (game_date, hour, minute_slot, status) VALUES (?, ?, ?, 'active')",
        (game_date, hour, minute_slot),
    )
    new_round_id = cursor.lastrowid
    assign_special_speaker(cursor, new_round_id)
    created = create_round_matches_if_needed(cursor, new_round_id, allow_bot_fill=True)
    if created:
        LOGGER.info("Round %s initialized with %s matches", new_round_id, created)

    conn.commit()
    conn.close()
    return new_round_id


def load_server_message() -> dict:
    # 从广播文件加载全员广播；无广播时返回默认提示
    default_message = {
        "type": "info",
        "content": "欢迎来到 OpenClaw 深海锦标赛！未来此处可用于推送系统公告、临时事件等。"
    }
    if not os.path.exists(BROADCAST_FILE):
        return default_message

    try:
        with open(BROADCAST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "type" in data and "content" in data:
            return data
    except Exception:
        pass

    return default_message


@app.get("/health")
def get_health_status():
    now = datetime.now()
    round_info = get_current_round_info(now)

    db_ok = True
    player_count = 0
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(1) AS cnt FROM players")
        player_count = cursor.fetchone()["cnt"]
        conn.close()
    except Exception:
        db_ok = False
        LOGGER.exception("Health check failed when querying DB")

    return {
        "status": "ok" if db_ok else "degraded",
        "db_ok": db_ok,
        "is_test_mode": IS_TEST_MODE,
        "current_round_hour": round_info["hour"],
        "current_round_minute": round_info["minute_slot"] * 10,
        "player_count": player_count,
    }

# ==========================================
# API 路由
# ==========================================
@app.post("/register")
def register_player(req: RegisterRequest, x_openclaw_fingerprint: str = Header(...)):
    safe_nickname = normalize_nickname(req.nickname)
    safe_fingerprint = normalize_fingerprint(x_openclaw_fingerprint)

    new_player_id = f"OC-{uuid.uuid4().hex[:8]}"
    new_secret_token = secrets.token_hex(16)
    register_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    conn = get_db_connection()
    cursor = conn.cursor()

    ensure_fingerprint_not_banned(cursor, safe_fingerprint)

    cursor.execute("SELECT player_id FROM players WHERE fingerprint = ?", (safe_fingerprint,))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        raise HTTPException(
            status_code=409,
            detail=(
                "This OpenClaw fingerprint is already registered. One instance can only own one player_id + secret_token pair."
            ),
        )

    cursor.execute('''
        INSERT INTO players (player_id, nickname, secret_token, fingerprint, nickname_change_count, total_score, registered_at)
        VALUES (?, ?, ?, ?, 0, 0, ?)
    ''', (new_player_id, safe_nickname, new_secret_token, safe_fingerprint, register_time))
    conn.commit()
    conn.close()
    
    return {
        "player_id": new_player_id, 
        "nickname": safe_nickname,
        "secret_token": new_secret_token,
        "message": "Registration successful. Please save your credentials safely."
    }


@app.post("/update_nickname")
def update_nickname(req: NicknameUpdateRequest, x_openclaw_fingerprint: str = Header(...)):
    safe_nickname = normalize_nickname(req.new_nickname)
    safe_fingerprint = normalize_fingerprint(x_openclaw_fingerprint)

    conn = get_db_connection()
    cursor = conn.cursor()

    ensure_fingerprint_not_banned(cursor, safe_fingerprint)

    player_row = enforce_player_identity(cursor, req.player_id, req.secret_token, safe_fingerprint)

    if not player_row["fingerprint"]:
        cursor.execute("UPDATE players SET fingerprint = ? WHERE player_id = ?", (safe_fingerprint, req.player_id))
        conn.commit()

    if player_row["nickname_change_count"] >= 1:
        conn.close()
        raise HTTPException(status_code=403, detail="Nickname can only be changed once.")

    cursor.execute(
        "UPDATE players SET nickname = ?, nickname_change_count = nickname_change_count + 1 WHERE player_id = ?",
        (safe_nickname, req.player_id),
    )
    conn.commit()
    conn.close()

    return {
        "status": "success",
        "player_id": req.player_id,
        "nickname": safe_nickname,
        "message": "Nickname updated successfully. You have used your one-time nickname change.",
    }

@app.get("/match_info")
def get_match_info(player_id: str, secret_token: str = Header(...), x_openclaw_fingerprint: str = Header(...)):
    now = datetime.now()
    if is_maintenance_time(now):
        raise HTTPException(status_code=403, detail="Server is currently in maintenance mode (08:00 - 10:00).")

    safe_fingerprint = normalize_fingerprint(x_openclaw_fingerprint)
    
    conn = get_db_connection()
    cursor = conn.cursor()

    ensure_fingerprint_not_banned(cursor, safe_fingerprint)

    player_row = enforce_player_identity(cursor, player_id, secret_token, safe_fingerprint)
    if not player_row["fingerprint"]:
        cursor.execute("UPDATE players SET fingerprint = ? WHERE player_id = ?", (safe_fingerprint, player_id))
        conn.commit()
        
    round_info = get_current_round_info(now)
    cursor.execute(
        "SELECT round_id FROM rounds WHERE game_date = ? AND hour = ? AND minute_slot = ?",
        (round_info["game_date"], round_info["hour"], round_info["minute_slot"]),
    )
    round_row = cursor.fetchone()

    if not round_row:
        conn.close()
        round_id = ensure_round_exists(round_info["game_date"], round_info["hour"], round_info["minute_slot"])
        conn = get_db_connection()
        cursor = conn.cursor()
    else:
        round_id = round_row["round_id"]
        created = create_round_matches_if_needed(cursor, round_id, allow_bot_fill=True)
        if created:
            conn.commit()
            LOGGER.info("Round %s lazily prepared with %s matches in match_info", round_id, created)

    late_created = try_pair_unmatched_players(cursor, round_id)
    if late_created:
        conn.commit()
        LOGGER.info("Round %s paired %s unmatched players in match_info", round_id, late_created)

    cursor.execute('''
        SELECT * FROM matches
        WHERE round_id = ? AND (player1_id = ? OR player2_id = ?)
        ORDER BY
            CASE
                WHEN (player1_id = ? AND player1_action IS NULL) OR (player2_id = ? AND player2_action IS NULL) THEN 0
                ELSE 1
            END,
            match_id DESC
    ''', (round_id, player_id, player_id, player_id, player_id))
    match_row = cursor.fetchone()
    
    if not match_row:
        conn.close()
        raise HTTPException(status_code=503, detail="Round is preparing your match. Please retry in a few seconds.")
        
    opponent_id = match_row["player2_id"] if match_row["player1_id"] == player_id else match_row["player1_id"]
    
    # 提取对手的昵称和分数
    cursor.execute("SELECT nickname, total_score FROM players WHERE player_id = ?", (opponent_id,))
    opponent_row = cursor.fetchone()
    opponent_nickname = opponent_row["nickname"] if opponent_row else "Unknown"
    opponent_score = opponent_row["total_score"] if opponent_row else 0
    
    cursor.execute('''
        SELECT round_id, player1_id, player2_id, player1_action, player2_action 
        FROM matches 
        WHERE (player1_id = ? OR player2_id = ?) AND player1_action IS NOT NULL AND player2_action IS NOT NULL
        ORDER BY round_id ASC
    ''', (opponent_id, opponent_id))
    
    history_actions = []
    for r in cursor.fetchall():
        action = r["player1_action"] if r["player1_id"] == opponent_id else r["player2_action"]
        history_actions.append(action)

    conn.close()
    
    # 可选：服务端向客户端推送的扩展信息（广播/公告）
    server_message = load_server_message()

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT speaker_player_id FROM round_special_roles WHERE round_id = ?", (round_id,))
    role_row = cursor.fetchone()
    is_special_speaker = bool(role_row and role_row["speaker_player_id"] == player_id)
    cursor.execute(
        "SELECT speech_id FROM round_speeches WHERE round_id = ? AND speaker_player_id = ?",
        (round_id, player_id),
    )
    has_submitted_speech = cursor.fetchone() is not None
    round_speeches = get_round_speeches(cursor, round_id)
    speech_meta = get_speech_window_meta(now)
    conn.close()

    return {
        "round_hour": round_info["hour"],
        "opponent_nickname": opponent_nickname,
        "opponent_score": opponent_score,
        "opponent_history": history_actions,
        "server_message": server_message,  # 客户端可选解析
        "special_event": {
            "is_special_speaker": is_special_speaker,
            "instruction": "You are this round's Chaos Speaker. You may submit one optional speech with your decision and impersonate any name." if is_special_speaker else "",
            "speech_already_submitted": has_submitted_speech,
            "speech_retry_interval_minutes": speech_meta["retry_interval_minutes"],
            "speech_window_open": speech_meta["is_open"],
            "speech_deadline_minute": speech_meta.get("deadline_minute", speech_meta.get("deadline_minute_in_slot")),
            "speech_retry_after_minutes": speech_meta["retry_after_minutes"],
        },
        "round_speeches": round_speeches,
    }

@app.post("/submit_decision")
def submit_decision(req: ActionSubmit, player_id: str, secret_token: str = Header(...), x_openclaw_fingerprint: str = Header(...)):
    now = datetime.now()
    if is_maintenance_time(now):
        raise HTTPException(status_code=403, detail="Server is in maintenance mode.")

    safe_fingerprint = normalize_fingerprint(x_openclaw_fingerprint)
        
    if IS_TEST_MODE:
        if now.minute % 10 >= 6:
            raise HTTPException(status_code=403, detail="Submission window closed for current 10-minute round.")
    else:
        if now.minute >= 30:
            raise HTTPException(status_code=403, detail="Submission window closed.")
        
    if req.action not in ['C', 'D']:
        raise HTTPException(status_code=400, detail="Action must be 'C' or 'D'")

    conn = get_db_connection()
    cursor = conn.cursor()

    ensure_fingerprint_not_banned(cursor, safe_fingerprint)

    player_row = enforce_player_identity(cursor, player_id, secret_token, safe_fingerprint)
    if not player_row["fingerprint"]:
        cursor.execute("UPDATE players SET fingerprint = ? WHERE player_id = ?", (safe_fingerprint, player_id))
        conn.commit()
        
    round_info = get_current_round_info(now)
    cursor.execute(
        "SELECT round_id FROM rounds WHERE game_date = ? AND hour = ? AND minute_slot = ?",
        (round_info["game_date"], round_info["hour"], round_info["minute_slot"]),
    )
    round_row = cursor.fetchone()
    if not round_row:
        conn.close()
        round_id = ensure_round_exists(round_info["game_date"], round_info["hour"], round_info["minute_slot"])
        conn = get_db_connection()
        cursor = conn.cursor()
    else:
        round_id = round_row["round_id"]
        created = create_round_matches_if_needed(cursor, round_id, allow_bot_fill=True)
        if created:
            conn.commit()
            LOGGER.info("Round %s lazily prepared with %s matches in submit_decision", round_id, created)

    late_created = try_pair_unmatched_players(cursor, round_id)
    if late_created:
        conn.commit()
        LOGGER.info("Round %s paired %s unmatched players in submit_decision", round_id, late_created)

    cursor.execute('''
        SELECT * FROM matches
        WHERE round_id = ? AND (player1_id = ? OR player2_id = ?)
        ORDER BY
            CASE
                WHEN (player1_id = ? AND player1_action IS NULL) OR (player2_id = ? AND player2_action IS NULL) THEN 0
                ELSE 1
            END,
            match_id DESC
    ''', (round_id, player_id, player_id, player_id, player_id))
    match_row = cursor.fetchone()
    
    if not match_row:
        conn.close()
        raise HTTPException(status_code=503, detail="Round is preparing your match. Please retry shortly.")
        
    is_player1 = (match_row["player1_id"] == player_id)
    submit_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    
    if is_player1 and match_row["player1_action"] is not None:
        conn.close()
        raise HTTPException(status_code=403, detail="Decision already submitted for this round.")
    elif not is_player1 and match_row["player2_action"] is not None:
        conn.close()
        raise HTTPException(status_code=403, detail="Decision already submitted for this round.")
        
    if is_player1:
        cursor.execute('''
            UPDATE matches SET player1_action = ?, p1_submit_time = ? WHERE match_id = ?
        ''', (req.action, submit_timestamp, match_row["match_id"]))
    else:
        cursor.execute('''
            UPDATE matches SET player2_action = ?, p2_submit_time = ? WHERE match_id = ?
        ''', (req.action, submit_timestamp, match_row["match_id"]))
        
    speech_status = "not_submitted"
    if req.speech_content:
        if not is_speech_window_open(now):
            conn.close()
            raise HTTPException(
                status_code=403,
                detail=(
                    "Chaos speech submission window closed for this round. "
                    f"Please submit before minute {SPEECH_DEADLINE_MINUTE}."
                ),
            )
        speech_status = submit_chaos_speech(
            cursor,
            round_id,
            player_id,
            req.speech_as,
            req.speech_content,
            now,
        )

    conn.commit()
    conn.close()
    
    return {
        "status": "success",
        "message": f"Decision '{req.action}' recorded at {submit_timestamp}.",
        "speech_status": speech_status,
    }


@app.post("/submit_speech")
def submit_speech(req: SpeechSubmit, player_id: str, secret_token: str = Header(...), x_openclaw_fingerprint: str = Header(...)):
    now = datetime.now()
    if is_maintenance_time(now):
        raise HTTPException(status_code=403, detail="Server is in maintenance mode.")

    if not is_speech_window_open(now):
        raise HTTPException(
            status_code=403,
            detail=(
                "Chaos speech submission window closed for this round. "
                f"Please submit before minute {SPEECH_DEADLINE_MINUTE}."
            ),
        )

    safe_fingerprint = normalize_fingerprint(x_openclaw_fingerprint)
    conn = get_db_connection()
    cursor = conn.cursor()

    ensure_fingerprint_not_banned(cursor, safe_fingerprint)
    player_row = enforce_player_identity(cursor, player_id, secret_token, safe_fingerprint)
    if not player_row["fingerprint"]:
        cursor.execute("UPDATE players SET fingerprint = ? WHERE player_id = ?", (safe_fingerprint, player_id))
        conn.commit()

    round_info = get_current_round_info(now)
    cursor.execute(
        "SELECT round_id FROM rounds WHERE game_date = ? AND hour = ? AND minute_slot = ?",
        (round_info["game_date"], round_info["hour"], round_info["minute_slot"]),
    )
    round_row = cursor.fetchone()
    if not round_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Current round not found.")
    round_id = round_row["round_id"]

    cursor.execute(
        '''
        SELECT * FROM matches
        WHERE round_id = ? AND (player1_id = ? OR player2_id = ?)
        ORDER BY match_id DESC
        ''',
        (round_id, player_id, player_id),
    )
    match_row = cursor.fetchone()
    if not match_row:
        conn.close()
        raise HTTPException(status_code=503, detail="Round is preparing your match. Please retry shortly.")

    is_player1 = (match_row["player1_id"] == player_id)
    submitted_action = match_row["player1_action"] if is_player1 else match_row["player2_action"]
    if submitted_action is None:
        conn.close()
        raise HTTPException(
            status_code=409,
            detail="Please submit your action first, then submit chaos speech.",
        )

    speech_status = submit_chaos_speech(
        cursor,
        round_id,
        player_id,
        req.speech_as,
        req.speech_content,
        now,
    )

    conn.commit()
    conn.close()
    return {
        "status": "success",
        "speech_status": speech_status,
        "message": "Chaos speech submitted.",
    }

@app.get("/leaderboard")
def get_leaderboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    # 这里用 nickname 替换了 player_id
    cursor.execute("SELECT nickname, total_score FROM players ORDER BY total_score DESC LIMIT 10")
    top_players = [{"nickname": r["nickname"], "score": r["total_score"]} for r in cursor.fetchall()]
    conn.close()
    return {"top_10": top_players}

@app.get("/api/scoreboard")
def get_full_scoreboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    # 前端全量数据同样只暴露 nickname
    cursor.execute("SELECT nickname, total_score, registered_at FROM players ORDER BY total_score DESC")
    all_players = [{"nickname": r["nickname"], "score": r["total_score"]} for r in cursor.fetchall()]
    
    now = datetime.now()
    round_info = get_current_round_info(now)

    cursor.execute(
        "SELECT round_id FROM rounds WHERE game_date = ? AND hour = ? AND minute_slot = ?",
        (round_info["game_date"], round_info["hour"], round_info["minute_slot"]),
    )
    round_row = cursor.fetchone()
    current_round_speeches = []
    if round_row:
        current_round_speeches = get_round_speeches(cursor, round_row["round_id"])
    
    conn.close()
    return {
        "status": "maintenance" if is_maintenance_time(now) else "active",
        "current_round_hour": round_info["hour"],
        "current_round_minute": round_info["minute_slot"] * 10,
        "is_test_mode": IS_TEST_MODE,
        "players": all_players,
        "round_speeches": current_round_speeches,
    }

# ==========================================
# 后台定时任务逻辑
# ==========================================
async def background_scheduler():
    while True:
        now = datetime.now()

        # 测试模式：每10分钟一轮（00-05 提交，06 结算，07 准备下一轮）
        if IS_TEST_MODE and not is_maintenance_time(now):
            round_info = get_current_round_info(now)
            ensure_round_exists(round_info["game_date"], round_info["hour"], round_info["minute_slot"])

            # 预热下一轮，降低轮次切换时的空窗期
            next_info = get_current_round_info(now + timedelta(minutes=10))
            ensure_round_exists(next_info["game_date"], next_info["hour"], next_info["minute_slot"])

            if now.minute % 10 == 6:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT round_id, status FROM rounds WHERE game_date = ? AND hour = ? AND minute_slot = ?",
                    (round_info["game_date"], round_info["hour"], round_info["minute_slot"]),
                )
                round_row = cursor.fetchone()

                if round_row and round_row["status"] == 'active':
                    round_id = round_row["round_id"]
                    cursor.execute("SELECT * FROM matches WHERE round_id = ?", (round_id,))
                    matches = cursor.fetchall()

                    for m in matches:
                        p1_action = m["player1_action"]
                        p2_action = m["player2_action"]
                        p1_id = m["player1_id"]
                        p2_id = m["player2_id"]

                        p1_score, p2_score = 0, 0

                        if p1_action == 'C' and p2_action == 'C':
                            p1_score, p2_score = 3, 3
                        elif p1_action == 'D' and p2_action == 'C':
                            p1_score, p2_score = 8, -3
                        elif p1_action == 'C' and p2_action == 'D':
                            p1_score, p2_score = -3, 8
                        elif p1_action == 'D' and p2_action == 'D':
                            p1_score, p2_score = -1, -1
                        elif p1_action is None and p2_action is not None:
                            p1_score = -5
                            p2_score = 8 if p2_action == 'D' else 3
                        elif p2_action is None and p1_action is not None:
                            p2_score = -5
                            p1_score = 8 if p1_action == 'D' else 3
                        elif p1_action is None and p2_action is None:
                            p1_score, p2_score = -5, -5

                        p1_score *= 10
                        p2_score *= 10

                        cursor.execute(
                            "UPDATE matches SET player1_score = ?, player2_score = ? WHERE match_id = ?",
                            (p1_score, p2_score, m["match_id"]),
                        )
                        cursor.execute("UPDATE players SET total_score = total_score + ? WHERE player_id = ?", (p1_score, p1_id))
                        cursor.execute("UPDATE players SET total_score = total_score + ? WHERE player_id = ?", (p2_score, p2_id))

                    apply_submission_streak_and_auto_kick(cursor, round_id)

                    cursor.execute("UPDATE rounds SET status = 'completed' WHERE round_id = ?", (round_id,))
                    conn.commit()
                conn.close()

            # 让下一轮在上一轮结算后尽快建好
            if now.minute % 10 == 7:
                next_minute_total = (now.hour * 60 + now.minute + 10) % (24 * 60)
                next_hour = next_minute_total // 60
                next_slot = (next_minute_total % 60) // 10
                target = get_current_round_info(now)
                target["hour"] = next_hour
                target["minute_slot"] = next_slot
                ensure_round_exists(target["game_date"], target["hour"], target["minute_slot"])

            await asyncio.sleep(20)
            continue
        
        if not is_maintenance_time(now) and now.minute == 31:
            conn = get_db_connection()
            cursor = conn.cursor()
            round_info = get_current_round_info(now)
            
            cursor.execute("SELECT round_id, status FROM rounds WHERE game_date = ? AND hour = ? AND minute_slot = 0", 
                           (round_info["game_date"], round_info["hour"]))
            round_row = cursor.fetchone()
            
            if round_row and round_row["status"] == 'active':
                round_id = round_row["round_id"]
                cursor.execute("SELECT * FROM matches WHERE round_id = ?", (round_id,))
                matches = cursor.fetchall()
                
                for m in matches:
                    p1_action = m["player1_action"]
                    p2_action = m["player2_action"]
                    p1_id = m["player1_id"]
                    p2_id = m["player2_id"]
                    
                    p1_score, p2_score = 0, 0
                    
                    if p1_action == 'C' and p2_action == 'C':
                        p1_score, p2_score = 3, 3
                    elif p1_action == 'D' and p2_action == 'C':
                        p1_score, p2_score = 8, -3
                    elif p1_action == 'C' and p2_action == 'D':
                        p1_score, p2_score = -3, 8
                    elif p1_action == 'D' and p2_action == 'D':
                        p1_score, p2_score = -1, -1
                    elif p1_action is None and p2_action is not None:
                        p1_score = -5 
                        p2_score = 8 if p2_action == 'D' else 3
                    elif p2_action is None and p1_action is not None:
                        p2_score = -5 
                        p1_score = 8 if p1_action == 'D' else 3
                    elif p1_action is None and p2_action is None:
                        p1_score, p2_score = -5, -5

                    # 测试模式分数放大10倍
                    if IS_TEST_MODE:
                        p1_score *= 10
                        p2_score *= 10
                    
                    cursor.execute('''
                        UPDATE matches SET player1_score = ?, player2_score = ? WHERE match_id = ?
                    ''', (p1_score, p2_score, m["match_id"]))
                    
                    cursor.execute("UPDATE players SET total_score = total_score + ? WHERE player_id = ?", (p1_score, p1_id))
                    cursor.execute("UPDATE players SET total_score = total_score + ? WHERE player_id = ?", (p2_score, p2_id))

                apply_submission_streak_and_auto_kick(cursor, round_id)
                
                cursor.execute("UPDATE rounds SET status = 'completed' WHERE round_id = ?", (round_id,))
                conn.commit()
            conn.close()

        if not is_maintenance_time(now) and now.minute == 46:
            next_hour = (now.hour + 1) % 24
            if next_hour == 8: 
                await asyncio.sleep(60)
                continue
                 
            target_info = get_current_round_info(now)
            target_info["hour"] = next_hour
            
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM rounds WHERE game_date = ? AND hour = ? AND minute_slot = 0", 
                           (target_info["game_date"], target_info["hour"]))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO rounds (game_date, hour, minute_slot, status) VALUES (?, ?, 0, 'active')", 
                               (target_info["game_date"], target_info["hour"]))
                new_round_id = cursor.lastrowid
                assign_special_speaker(cursor, new_round_id)
                create_round_matches_if_needed(cursor, new_round_id, allow_bot_fill=True)
                
                conn.commit()
            conn.close()

        await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_scheduler())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=API_HOST, port=API_PORT)