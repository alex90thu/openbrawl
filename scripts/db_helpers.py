import json
import logging
import os
import random
import sqlite3
from threading import Lock
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException

from .runtime import (
    AUTO_KICK_MISS_STREAK,
    BROADCAST_FILE,
    DB_FILE,
    FINGERPRINT_BAN_HOURS,
    IS_TEST_MODE,
    SPEECH_DEADLINE_MINUTE,
    SPEECH_RETRY_INTERVAL_MINUTES,
    TEST_SPEECH_DEADLINE_MINUTE_IN_SLOT,
)


LOGGER = logging.getLogger("openclaw.server")
SPEECH_ROUND_DEBUG = os.getenv("OPENCLAW_SPEECH_ROUND_DEBUG", "1") == "1"
SPEECH_ROUND_LOG_FILE = os.getenv("OPENCLAW_SPEECH_ROUND_LOG_FILE", "log/speech_round.log")
SPEECH_ROUND_LOG_INTERVAL_SECONDS = int(os.getenv("OPENCLAW_SPEECH_ROUND_LOG_INTERVAL_SECONDS", "3600"))
_SPEECH_ROUND_LAST_LOG_TS: dict[int, float] = {}
_SPEECH_ROUND_LOG_LOCK = Lock()


def _write_speech_round_log(line: str):
    if not SPEECH_ROUND_DEBUG:
        return
    try:
        log_dir = os.path.dirname(SPEECH_ROUND_LOG_FILE)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(SPEECH_ROUND_LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")
    except Exception as exc:
        LOGGER.warning("Failed to write speech round debug log: %s", exc)


def _log_round_speeches_snapshot(cursor, round_id: int, selected_speech_id: Optional[int], reason: str):
    if not SPEECH_ROUND_DEBUG:
        return

    now_ts = datetime.now().timestamp()
    with _SPEECH_ROUND_LOG_LOCK:
        # 常规快照同一轮最多每 1 小时写一次；选中事件始终记录。
        if reason != "public_speech_selected":
            last_ts = _SPEECH_ROUND_LAST_LOG_TS.get(round_id, 0.0)
            if now_ts - last_ts < max(1, SPEECH_ROUND_LOG_INTERVAL_SECONDS):
                return
        _SPEECH_ROUND_LAST_LOG_TS[round_id] = now_ts

    cursor.execute(
        """
        SELECT rs.speech_id, rs.speaker_player_id, rs.speech_as, rs.content, rs.created_at, p.nickname
        FROM round_speeches rs
        LEFT JOIN players p ON p.player_id = rs.speaker_player_id
        WHERE rs.round_id = ?
        ORDER BY rs.speech_id ASC
        """,
        (round_id,),
    )
    rows = cursor.fetchall()

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write_speech_round_log(
        f"[{ts}] round_id={round_id} reason={reason} selected_speech_id={selected_speech_id or 'None'} total_speeches={len(rows)}"
    )

    for row in rows:
        selected_flag = "YES" if selected_speech_id and row["speech_id"] == selected_speech_id else "NO"
        display_name = (row["nickname"] or row["speech_as"] or "匿名龙虾").strip()[:20]
        alias_name = (row["speech_as"] or "").strip()[:20]
        content_preview = (row["content"] or "").replace("\n", " ").strip()[:80]
        _write_speech_round_log(
            "  - speech_id={speech_id} selected={selected} speaker_player_id={speaker} display={display} alias={alias} created_at={created_at} content={content}".format(
                speech_id=row["speech_id"],
                selected=selected_flag,
                speaker=row["speaker_player_id"],
                display=display_name,
                alias=alias_name,
                created_at=row["created_at"],
                content=content_preview,
            )
        )


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        """
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
        """
    )

    cursor.execute("PRAGMA table_info(players)")
    player_columns = [row[1] for row in cursor.fetchall()]
    if "fingerprint" not in player_columns:
        cursor.execute("ALTER TABLE players ADD COLUMN fingerprint TEXT")
    if "nickname_change_count" not in player_columns:
        cursor.execute("ALTER TABLE players ADD COLUMN nickname_change_count INTEGER DEFAULT 0")
    if "miss_submit_streak" not in player_columns:
        cursor.execute("ALTER TABLE players ADD COLUMN miss_submit_streak INTEGER DEFAULT 0")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS rounds (
            round_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_date TEXT,
            hour INTEGER,
            minute_slot INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending'
        )
        """
    )

    cursor.execute("PRAGMA table_info(rounds)")
    round_columns = [row[1] for row in cursor.fetchall()]
    if "minute_slot" not in round_columns:
        cursor.execute("ALTER TABLE rounds ADD COLUMN minute_slot INTEGER DEFAULT 0")

    cursor.execute(
        """
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
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS round_special_roles (
            round_id INTEGER PRIMARY KEY,
            speaker_player_id TEXT,
            announced_at TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS round_speeches (
            speech_id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER,
            speaker_player_id TEXT,
            speech_as TEXT,
            content TEXT,
            created_at TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS round_public_speech (
            round_id INTEGER PRIMARY KEY,
            speech_id INTEGER,
            selected_at TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fingerprint_bans (
            fingerprint TEXT PRIMARY KEY,
            banned_until TEXT,
            reason TEXT,
            created_at TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS player_achievements (
            achievement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id TEXT,
            achievement_key TEXT,
            achievement_name TEXT,
            score_bonus INTEGER DEFAULT 0,
            source_event TEXT,
            details_json TEXT,
            awarded_at TEXT,
            UNIQUE(player_id, achievement_key)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_event_log (
            feature_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            player_id TEXT,
            round_id INTEGER,
            payload_json TEXT,
            created_at TEXT
        )
        """
    )

    conn.commit()
    conn.close()


def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


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
            detail="x-openclaw-fingerprint is required. Each OpenBrawl instance must use one stable fingerprint.",
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

    for match_row in matches:
        p1_id = match_row["player1_id"]
        p2_id = match_row["player2_id"]

        if p1_id != "BOT-SHADOW":
            if match_row["player1_action"] is None:
                cursor.execute(
                    "UPDATE players SET miss_submit_streak = miss_submit_streak + 1 WHERE player_id = ?",
                    (p1_id,),
                )
            else:
                cursor.execute("UPDATE players SET miss_submit_streak = 0 WHERE player_id = ?", (p1_id,))

        if p2_id != "BOT-SHADOW":
            if match_row["player2_action"] is None:
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
    to_kick = [row["player_id"] for row in cursor.fetchall()]
    for player_id in to_kick:
        auto_kick_and_ban_player(
            cursor,
            player_id,
            f"Auto kick: {AUTO_KICK_MISS_STREAK} consecutive rounds without submission",
        )


def enforce_player_identity(cursor, player_id: str, secret_token: str, fingerprint: str):
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
                "to this OpenBrawl instance."
            ),
        )

    return player_row


def is_maintenance_time(now: datetime) -> bool:
    if IS_TEST_MODE:
        return False
    return 8 <= now.hour < 10


def get_current_round_info(now: datetime) -> dict:
    if now.hour < 8:
        game_date = now.replace(day=now.day - 1).strftime("%Y-%m-%d")
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


def assign_special_speaker(cursor, round_id: int):
    cursor.execute("SELECT player_id FROM players")
    candidates = [row["player_id"] for row in cursor.fetchall()]
    if not candidates:
        return

    selected_player = random.choice(candidates)
    cursor.execute(
        "INSERT OR REPLACE INTO round_special_roles (round_id, speaker_player_id, announced_at) VALUES (?, ?, ?)",
        (round_id, selected_player, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )


def get_round_speeches(cursor, round_id: int):
    cursor.execute(
        """
        SELECT rs.speech_id, rs.speaker_player_id, rs.speech_as, rs.content, rs.created_at, p.nickname
        FROM round_public_speech rps
        JOIN round_speeches rs ON rs.speech_id = rps.speech_id
        LEFT JOIN players p ON p.player_id = rs.speaker_player_id
        WHERE rps.round_id = ?
        LIMIT 1
        """,
        (round_id,),
    )
    published_row = cursor.fetchone()
    if published_row and published_row["nickname"]:
        _log_round_speeches_snapshot(cursor, round_id, int(published_row["speech_id"]), "public_speech_read")
        return [
            {
                "speech_as": published_row["nickname"],
                "content": published_row["content"],
                "created_at": published_row["created_at"],
            }
        ]

    cursor.execute(
        """
        SELECT rs.speech_id, rs.speaker_player_id, rs.speech_as, rs.content, rs.created_at, p.nickname
        FROM round_speeches rs
        JOIN players p ON p.player_id = rs.speaker_player_id
        WHERE rs.round_id = ?
        ORDER BY rs.speech_id DESC
        """,
        (round_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        return []

    selected = random.choice(rows)
    cursor.execute(
        "INSERT OR REPLACE INTO round_public_speech (round_id, speech_id, selected_at) VALUES (?, ?, ?)",
        (round_id, selected["speech_id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    # 立即持久化随机发布结果，避免请求间重复随机导致发言墙抖动。
    cursor.connection.commit()
    _log_round_speeches_snapshot(cursor, round_id, int(selected["speech_id"]), "public_speech_selected")
    return [
        {
            "speech_as": selected["nickname"],
            "content": selected["content"],
            "created_at": selected["created_at"],
        }
    ]


def submit_chaos_speech(
    cursor,
    round_id: int,
    player_id: str,
    speech_as: Optional[str],
    speech_content: Optional[str],
    now: datetime,
):
    if not speech_content:
        return "not_submitted"

    speech_text = speech_content.strip()[:200]
    speaker_alias = (speech_as or "").strip()[:20] or "匿名龙虾"
    if not speech_text:
        raise HTTPException(status_code=400, detail="speech_content cannot be empty.")

    cursor.execute("SELECT nickname FROM players WHERE player_id = ?", (player_id,))
    nickname_row = cursor.fetchone()
    if nickname_row and nickname_row["nickname"]:
        # 发言墙展示名统一绑定玩家昵称，避免出现非玩家身份名。
        speaker_alias = str(nickname_row["nickname"]).strip()[:20] or "匿名龙虾"

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

    inserted_speech_id = cursor.lastrowid
    cursor.execute(
        "SELECT speech_id FROM round_public_speech WHERE round_id = ? LIMIT 1",
        (round_id,),
    )
    selected_row = cursor.fetchone()
    selected_speech_id = int(selected_row["speech_id"]) if selected_row else None
    _log_round_speeches_snapshot(
        cursor,
        round_id,
        selected_speech_id,
        f"speech_submitted:new_speech_id={inserted_speech_id}",
    )

    return "submitted"


def load_server_message() -> dict:
    default_message = {
        "type": "info",
        "content": "欢迎来到 OpenBrawl 深海锦标赛！未来此处可用于推送系统公告、临时事件等。",
    }
    if not os.path.exists(BROADCAST_FILE):
        return default_message

    try:
        with open(BROADCAST_FILE, "r", encoding="utf-8") as file_handle:
            data = json.load(file_handle)
        if isinstance(data, dict) and "type" in data and "content" in data:
            return data
    except Exception:
        pass

    return default_message
