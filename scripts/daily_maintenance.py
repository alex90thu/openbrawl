from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .daily_settlement import build_daily_settlement_summary
from .runtime import DB_FILE


LOG_DIR = Path("log") / "games"
RECORD_DIR = Path("data") / "records"


def _ensure_ops_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS maintenance_ops (
            op_key TEXT PRIMARY KEY,
            done_at TEXT,
            payload_json TEXT
        )
        """
    )


def _is_done(cursor, op_key: str) -> bool:
    cursor.execute("SELECT 1 FROM maintenance_ops WHERE op_key = ?", (op_key,))
    return cursor.fetchone() is not None


def _mark_done(cursor, op_key: str, payload: dict[str, Any] | None = None) -> None:
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        INSERT OR REPLACE INTO maintenance_ops (op_key, done_at, payload_json)
        VALUES (?, ?, ?)
        """,
        (
            op_key,
            now_text,
            json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
        ),
    )


def _load_avatar_map() -> dict[str, dict[str, str]]:
    empty = {"players": {}, "nicknames": {}}
    path = Path("data") / "avatar_map.json"
    if not path.exists():
        return empty

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty

    if not isinstance(payload, dict):
        return empty

    players = payload.get("players") if isinstance(payload.get("players"), dict) else {}
    nicknames = payload.get("nicknames") if isinstance(payload.get("nicknames"), dict) else {}

    if players or nicknames:
        return {"players": players, "nicknames": nicknames}

    # Backward compatibility: map root object as players mapping.
    return {"players": payload, "nicknames": {}}


def _resolve_avatar_key(player_id: str, nickname: str, avatar_map: dict[str, dict[str, str]]) -> str:
    by_id = str((avatar_map.get("players") or {}).get(player_id) or "").strip()
    if by_id:
        return by_id
    by_name = str((avatar_map.get("nicknames") or {}).get(nickname) or "").strip()
    if by_name:
        return by_name
    return player_id


def _collect_player_profiles(cursor) -> list[dict[str, Any]]:
    avatar_map = _load_avatar_map()
    cursor.execute(
        """
        SELECT player_id, nickname, total_score, registered_at
        FROM players
        ORDER BY total_score DESC, nickname ASC
        """
    )
    rows = cursor.fetchall()
    profiles: list[dict[str, Any]] = []
    for row in rows:
        player_id = str(row["player_id"] or "")
        nickname = str(row["nickname"] or player_id)
        profiles.append(
            {
                "player_id": player_id,
                "nickname": nickname,
                "avatar_key": _resolve_avatar_key(player_id, nickname, avatar_map),
                "total_score": int(row["total_score"] or 0),
                "registered_at": row["registered_at"],
            }
        )
    return profiles


def _write_json_log(prefix: str, payload: dict[str, Any]) -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_{ts}.json"
    path = LOG_DIR / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def maybe_write_settlement_log(conn, now: datetime) -> str | None:
    if not (now.hour == 8 and now.minute < 5):
        return None

    cursor = conn.cursor()
    _ensure_ops_table(cursor)

    target_date = (now.date() - timedelta(days=1)).strftime("%Y-%m-%d")
    op_key = f"settlement_log_{target_date}"
    if _is_done(cursor, op_key):
        return None

    summary = build_daily_settlement_summary(cursor, target_date)
    payload = {
        "event": "daily_settlement",
        "target_date": target_date,
        "logged_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "window": summary.get("window", {}),
        "sections": summary.get("sections", []),
        "players": _collect_player_profiles(cursor),
    }
    log_path = _write_json_log("settlement", payload)

    _mark_done(cursor, op_key, {"log_file": log_path})
    conn.commit()
    return log_path


def run_new_game_equivalent(conn, now: datetime) -> str:
    cursor = conn.cursor()

    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    backup_name = f"{Path(DB_FILE).name}_{now.strftime('%Y%m%d_%H%M%S')}"
    backup_path = RECORD_DIR / backup_name
    if Path(DB_FILE).exists():
        shutil.copy2(DB_FILE, backup_path)

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {str(row[0]) for row in cursor.fetchall()}

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
            cursor.execute(f"DELETE FROM {table_name}")

    if "players" in tables:
        cursor.execute("UPDATE players SET total_score = 0, miss_submit_streak = 0")

    log_payload = {
        "event": "season_reset",
        "triggered_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "backup_file": str(backup_path) if backup_path else "",
        "player_count_after_reset": cursor.execute("SELECT COUNT(1) FROM players").fetchone()[0] if "players" in tables else 0,
    }
    log_path = _write_json_log("season_reset", log_payload)
    conn.commit()
    return log_path


def maybe_rollover_after_10(conn, now: datetime) -> str | None:
    if now.hour < 10:
        return None

    cursor = conn.cursor()
    _ensure_ops_table(cursor)
    op_key = f"season_reset_{now.strftime('%Y-%m-%d')}"
    if _is_done(cursor, op_key):
        return None

    log_path = run_new_game_equivalent(conn, now)
    _mark_done(cursor, op_key, {"log_file": log_path})
    conn.commit()
    return log_path