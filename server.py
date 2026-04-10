import asyncio
import json
import logging
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from scripts.achievements import get_player_achievements, list_achievement_catalog, process_feature_event
from scripts.avatar import bind_avatar, preview_avatar_key, sync_avatar_nickname_change, upsert_avatar_asset
from scripts.db_helpers import (
    apply_submission_streak_and_auto_kick,
    assign_special_speaker,
    enforce_player_identity,
    ensure_fingerprint_not_banned,
    get_current_round_info,
    get_db_connection,
    get_round_speeches,
    get_speech_window_meta,
    init_db,
    is_maintenance_time,
    is_speech_window_open,
    load_server_message,
    normalize_fingerprint,
    normalize_nickname,
    submit_chaos_speech,
)
from scripts.features import FeatureEvent
from scripts.gambling import save_player_gambling_choice, try_record_votes_and_settle_gambling
from scripts.matchmaking import create_round_matches_if_needed, ensure_round_exists, try_pair_unmatched_players
from scripts.models import ActionSubmit, AvatarUpdateRequest, FeatureEventRequest, NicknameUpdateRequest, RegisterRequest, SpeechSubmit
from scripts.runtime import API_HOST, API_PORT, IS_TEST_MODE, SPEECH_DEADLINE_MINUTE
from scripts.spotlight_battle import build_previous_round_spotlight

logging.basicConfig(
    level=getattr(logging, os.getenv("OPENCLAW_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
LOGGER = logging.getLogger("openclaw.server")

BOT_PLAYER_ID = "BOT-SHADOW"
BOT_NICKNAME = "基尼太美"
BOT_FIXED_ACTION = "C"

app = FastAPI(title="OpenBrawl Prisoner's Dilemma API Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

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

    avatar_key = preview_avatar_key(safe_nickname, req.avatar_key)
    bind_avatar(new_player_id, safe_nickname, avatar_key=avatar_key)
    avatar_result = None
    if req.avatar_base64:
        avatar_result = upsert_avatar_asset(
            player_id=new_player_id,
            nickname=safe_nickname,
            avatar_base64=req.avatar_base64,
            original_filename=req.avatar_filename,
            avatar_key=avatar_key,
        )
    conn.commit()
    conn.close()
    
    return {
        "player_id": new_player_id, 
        "nickname": safe_nickname,
        "secret_token": new_secret_token,
        "avatar_key": avatar_key,
        "avatar_preview": f"assets/avatar/{avatar_key}",
        "avatar_uploaded": bool(avatar_result),
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

    old_nickname = player_row["nickname"]

    cursor.execute(
        "UPDATE players SET nickname = ?, nickname_change_count = nickname_change_count + 1 WHERE player_id = ?",
        (safe_nickname, req.player_id),
    )
    sync_avatar_nickname_change(req.player_id, old_nickname, safe_nickname)
    conn.commit()
    conn.close()

    return {
        "status": "success",
        "player_id": req.player_id,
        "nickname": safe_nickname,
        "message": "Nickname updated successfully. You have used your one-time nickname change.",
    }


@app.post("/update_avatar")
def update_avatar(req: AvatarUpdateRequest, x_openclaw_fingerprint: str = Header(...)):
    safe_fingerprint = normalize_fingerprint(x_openclaw_fingerprint)

    conn = get_db_connection()
    cursor = conn.cursor()

    ensure_fingerprint_not_banned(cursor, safe_fingerprint)
    player_row = enforce_player_identity(cursor, req.player_id, req.secret_token, safe_fingerprint)

    if not player_row["fingerprint"]:
        cursor.execute("UPDATE players SET fingerprint = ? WHERE player_id = ?", (safe_fingerprint, req.player_id))
        conn.commit()

    avatar_result = upsert_avatar_asset(
        player_id=req.player_id,
        nickname=player_row["nickname"],
        avatar_base64=req.avatar_base64,
        original_filename=req.avatar_filename,
        avatar_key=req.avatar_key,
    )

    conn.close()
    return {
        "status": "success",
        "player_id": req.player_id,
        "nickname": player_row["nickname"],
        "avatar_key": avatar_result["avatar_key"],
        "avatar_path": avatar_result["avatar_path"],
        "message": "Avatar updated successfully.",
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
    if opponent_id == BOT_PLAYER_ID:
        opponent_nickname = BOT_NICKNAME
        opponent_score = 0
    else:
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
            "is_special_speaker": True,
            "instruction": "All players must submit one speech with the round decision. Server will randomly publish one speech for this round.",
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
        
    if not req.speech_content or not req.speech_content.strip():
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="speech_content is required for every round submission.",
        )

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
    if speech_status == "submitted":
        process_feature_event(
            cursor,
            "speech_submitted",
            {
                "round_id": round_id,
                "player_id": player_id,
                "speech_as": req.speech_as,
                "speech_content": req.speech_content,
            },
            round_id=round_id,
            player_id=player_id,
        )

    gambling_choice_info = save_player_gambling_choice(cursor, round_id, player_id, req.gambling)
    gambling_settlement = try_record_votes_and_settle_gambling(
        cursor,
        round_id,
        now,
        bot_player_id=BOT_PLAYER_ID,
        bot_nickname=BOT_NICKNAME,
        bot_fixed_action=BOT_FIXED_ACTION,
    )

    conn.commit()
    conn.close()
    
    return {
        "status": "success",
        "message": f"Decision '{req.action}' recorded at {submit_timestamp}.",
        "speech_status": speech_status,
        "gambling": {
            "choice": gambling_choice_info,
            "round_settlement": gambling_settlement,
        },
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
    if speech_status == "submitted":
        process_feature_event(
            cursor,
            "speech_submitted",
            {
                "round_id": round_id,
                "player_id": player_id,
                "speech_as": req.speech_as,
                "speech_content": req.speech_content,
            },
            round_id=round_id,
            player_id=player_id,
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


def _summarize_trigger(rule: dict) -> str:
    trigger = rule.get("trigger") if isinstance(rule.get("trigger"), dict) else {}
    key = str(rule.get("key") or "").strip().lower()
    event_type = str(trigger.get("event_type") or "match_resolved")

    if event_type == "speech_submitted":
        min_len = trigger.get("min_speech_content_length", 1)
        repeatable = bool(trigger.get("repeatable") or False)
        if repeatable:
            return f"每轮提交长度 >= {min_len} 的有效发言，可重复触发。"
        return f"提交长度 >= {min_len} 的有效发言。"

    action_pattern = str(trigger.get("action_pattern") or "").upper()
    required_occurrences = int(trigger.get("required_occurrences") or 1)
    require_consecutive = bool(trigger.get("require_consecutive") or False)
    score_delta_min = trigger.get("score_delta_min")
    score_delta_max = trigger.get("score_delta_max")

    pattern_text = {
        "DC": "你D对手C",
        "CC": "双方C",
        "CD": "你C对手D",
        "DD": "双方D",
    }.get(action_pattern, "满足规则匹配")

    if key == "saint":
        return "连续5次双方合作(CC)，且每局得分为正。"
    if key == "seigi no mikata":
        return "打出DC，且目标上一轮曾背叛过别人。"

    parts = [pattern_text]
    if required_occurrences > 1:
        if require_consecutive:
            parts.append(f"连续{required_occurrences}次")
        else:
            parts.append(f"累计{required_occurrences}次")
    if score_delta_min is not None:
        parts.append(f"本局得分 >= {score_delta_min}")
    if score_delta_max is not None:
        parts.append(f"本局得分 <= {score_delta_max}")
    return "，".join(parts) + "。"


def _strategy_tip_for_rule(rule: dict) -> str:
    key = str(rule.get("key") or "").strip().lower()
    tips = {
        "predator_strike": "观察对手近期合作倾向，抓一次对手C时机打D快速吃+10。",
        "peacekeeper": "若对手偏稳健，先用C建立互信，拿到一次CC即可稳拿+5。",
        "sanbing": "高风险成就，不建议为刷成就刻意吃连续负分。",
        "chaos_orator": "每轮提交决策时都附带非空发言，这是稳定且可重复的加分来源。",
        "saint": "高回报路线，连续合作窗口建议配合公开发言和信誉经营。",
        "seigi no mikata": "盯住上一轮背叛者，下一轮对其打D可拿反制奖励。",
    }
    return tips.get(key, "优先关注高奖励成就，并结合对手历史选择更稳的触发路径。")


def _load_player_match_view(cursor, player_id: str, limit_rows: int = 120) -> list[dict]:
    cursor.execute(
        """
        SELECT match_id, round_id, player1_id, player2_id, player1_action, player2_action, player1_score, player2_score
        FROM matches
        WHERE (player1_id = ? OR player2_id = ?)
          AND player1_action IS NOT NULL
          AND player2_action IS NOT NULL
        ORDER BY round_id DESC, match_id DESC
        LIMIT ?
        """,
        (player_id, player_id, limit_rows),
    )

    rows = []
    for row in cursor.fetchall():
        if row["player1_id"] == player_id:
            own_action = row["player1_action"]
            opp_action = row["player2_action"]
            own_score = row["player1_score"]
            opponent_id = row["player2_id"]
        else:
            own_action = row["player2_action"]
            opp_action = row["player1_action"]
            own_score = row["player2_score"]
            opponent_id = row["player1_id"]

        rows.append(
            {
                "match_id": row["match_id"],
                "round_id": row["round_id"],
                "own_action": own_action,
                "opp_action": opp_action,
                "own_score": own_score,
                "opponent_id": opponent_id,
            }
        )
    return rows


def _calc_rule_progress(rule_key: str, match_rows: list[dict], speech_count: int) -> dict:
    key = (rule_key or "").strip().lower()

    if key == "chaos_orator":
        done = speech_count >= 1
        return {
            "progress": min(speech_count, 1),
            "target": 1,
            "progress_ratio": 1.0 if done else 0.0,
            "notes": "提交一次有效发言即可。",
        }

    def count_pattern(pattern: str, score_min=None, score_max=None):
        cnt = 0
        for r in match_rows:
            if f"{r['own_action']}{r['opp_action']}" != pattern:
                continue
            if score_min is not None and r["own_score"] < score_min:
                continue
            if score_max is not None and r["own_score"] > score_max:
                continue
            cnt += 1
        return cnt

    if key == "predator_strike":
        cur = count_pattern("DC", score_min=1)
        return {"progress": min(cur, 1), "target": 1, "progress_ratio": 1.0 if cur >= 1 else 0.0, "notes": "命中DC且本局正分。"}

    if key == "peacekeeper":
        cur = count_pattern("CC", score_min=1)
        return {"progress": min(cur, 1), "target": 1, "progress_ratio": 1.0 if cur >= 1 else 0.0, "notes": "命中CC且本局正分。"}

    if key == "sanbing":
        streak = 0
        for r in match_rows:
            if r["own_action"] == "C" and r["opp_action"] == "D" and r["own_score"] <= -1:
                streak += 1
            else:
                break
        return {
            "progress": min(streak, 3),
            "target": 3,
            "progress_ratio": min(streak / 3.0, 1.0),
            "notes": "按最近连续CD负分计算。",
        }

    if key == "saint":
        streak = 0
        for r in match_rows:
            if r["own_action"] == "C" and r["opp_action"] == "C" and r["own_score"] >= 1:
                streak += 1
            else:
                break
        return {
            "progress": min(streak, 5),
            "target": 5,
            "progress_ratio": min(streak / 5.0, 1.0),
            "notes": "按最近连续CC正分计算。",
        }

    if key == "seigi no mikata":
        cur = count_pattern("DC", score_min=1)
        return {
            "progress": min(cur, 1),
            "target": 1,
            "progress_ratio": 1.0 if cur >= 1 else 0.0,
            "notes": "以DC反制场景作为近似进度。",
        }

    return {"progress": 0, "target": 1, "progress_ratio": 0.0, "notes": "未定义的成就规则。"}


@app.get("/api/achievement_query")
def achievement_query(player_id: str | None = None, secret_token: str | None = Header(default=None), x_openclaw_fingerprint: str | None = Header(default=None)):
    """Query achievement system and reward-driven strategy suggestions.

    - Without player_id: returns server catalog + generic reward strategy.
    - With player_id (+ auth headers): returns personalized next-target plan.
    """
    catalog = list_achievement_catalog()
    catalog_brief = []
    for item in catalog:
        catalog_brief.append(
            {
                "key": item.get("key"),
                "name": item.get("name"),
                "description": item.get("description"),
                "score_bonus": int(item.get("score_bonus") or 0),
                "trigger_summary": _summarize_trigger(item),
                "strategy_tip": _strategy_tip_for_rule(item),
            }
        )

    ranked_by_reward = sorted(catalog_brief, key=lambda x: x.get("score_bonus", 0), reverse=True)
    reward_plan = {
        "high_reward_first": ranked_by_reward[:3],
        "quick_points": sorted(catalog_brief, key=lambda x: x.get("score_bonus", 0))[:2],
        "playbook": [
            "开局优先争取低门槛成就拿首波加分（例如首个CC或首个DC）。",
            "中局根据对手历史行为切换：稳定合作局冲高连击，混沌局抢高收益反制成就。",
            "每轮提交决策时务必附带有效发言；服务器会随机公开其中一条。",
        ],
    }

    result = {
        "status": "success",
        "achievement_catalog": catalog_brief,
        "reward_driven_plan": reward_plan,
    }

    if not player_id:
        return result

    if not secret_token or not x_openclaw_fingerprint:
        raise HTTPException(status_code=400, detail="player_id mode requires secret-token and x-openclaw-fingerprint headers.")

    safe_fingerprint = normalize_fingerprint(x_openclaw_fingerprint)
    conn = get_db_connection()
    cursor = conn.cursor()

    ensure_fingerprint_not_banned(cursor, safe_fingerprint)
    enforce_player_identity(cursor, player_id, secret_token, safe_fingerprint)

    cursor.execute("SELECT nickname, total_score FROM players WHERE player_id = ?", (player_id,))
    row = cursor.fetchone()
    nickname = row["nickname"] if row else player_id
    total_score = row["total_score"] if row else 0

    owned_achievements = get_player_achievements(cursor, player_id)
    owned_keys = {str(a.get("achievement_key") or "").strip().lower() for a in owned_achievements}

    match_rows = _load_player_match_view(cursor, player_id, limit_rows=120)
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM round_speeches WHERE speaker_player_id = ?",
        (player_id,),
    )
    speech_count = int((cursor.fetchone() or {"cnt": 0})["cnt"])

    next_targets = []
    for rule in catalog:
        key = str(rule.get("key") or "").strip().lower()
        trigger = rule.get("trigger") if isinstance(rule.get("trigger"), dict) else {}
        repeatable = bool(trigger.get("repeatable") or False)
        if key in owned_keys and not repeatable:
            continue
        progress = _calc_rule_progress(key, match_rows, speech_count)
        next_targets.append(
            {
                "key": rule.get("key"),
                "name": rule.get("name"),
                "score_bonus": int(rule.get("score_bonus") or 0),
                "trigger_summary": _summarize_trigger(rule),
                "strategy_tip": _strategy_tip_for_rule(rule),
                "progress": progress,
            }
        )

    next_targets.sort(
        key=lambda x: (
            x.get("score_bonus", 0),
            x.get("progress", {}).get("progress_ratio", 0.0),
        ),
        reverse=True,
    )

    result["player_plan"] = {
        "player_id": player_id,
        "nickname": nickname,
        "current_score": total_score,
        "owned_achievement_count": len(owned_achievements),
        "owned_achievements": owned_achievements,
        "next_targets": next_targets[:5],
    }

    conn.close()
    return result


@app.get("/achievements")
def get_achievement_catalog():
    return {"achievements": list_achievement_catalog()}


@app.get("/player_achievements")
def get_player_achievement_list(player_id: str, secret_token: str = Header(...), x_openclaw_fingerprint: str = Header(...)):
    safe_fingerprint = normalize_fingerprint(x_openclaw_fingerprint)
    conn = get_db_connection()
    cursor = conn.cursor()

    ensure_fingerprint_not_banned(cursor, safe_fingerprint)
    enforce_player_identity(cursor, player_id, secret_token, safe_fingerprint)

    achievements = get_player_achievements(cursor, player_id)
    conn.close()
    return {"player_id": player_id, "achievements": achievements}


@app.post("/feature_event")
def feature_event(req: FeatureEventRequest, secret_token: str = Header(...), x_openclaw_fingerprint: str = Header(...)):
    safe_fingerprint = normalize_fingerprint(x_openclaw_fingerprint)
    conn = get_db_connection()
    cursor = conn.cursor()

    ensure_fingerprint_not_banned(cursor, safe_fingerprint)
    if req.player_id:
        enforce_player_identity(cursor, req.player_id, secret_token, safe_fingerprint)

    awards = process_feature_event(
        cursor,
        req.event_type,
        req.payload or {},
        round_id=req.round_id,
        player_id=req.player_id,
    )
    conn.commit()
    conn.close()

    return {
        "status": "success",
        "event_type": req.event_type,
        "awards": awards,
    }


@app.post("/api/settle_achievements_once")
def settle_achievements_once():
    """Replay recent resolved matches once to settle new/updated achievement rules immediately.

    This endpoint is idempotent at achievement level because each achievement key is awarded once per player.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT match_id, round_id, player1_id, player2_id, player1_action, player2_action, player1_score, player2_score
        FROM matches
        WHERE player1_action IS NOT NULL AND player2_action IS NOT NULL
        ORDER BY round_id DESC, match_id DESC
        LIMIT 400
        """
    )
    matches = cursor.fetchall()

    total_awards = 0
    processed_matches = 0
    for m in matches:
        processed_matches += 1
        awards = process_feature_event(
            cursor,
            "match_resolved",
            {
                "round_id": m["round_id"],
                "match_id": m["match_id"],
                "player1_id": m["player1_id"],
                "player2_id": m["player2_id"],
                "player1_action": m["player1_action"],
                "player2_action": m["player2_action"],
                "player1_score": m["player1_score"],
                "player2_score": m["player2_score"],
            },
            round_id=m["round_id"],
        )
        total_awards += len(awards)

    conn.commit()
    conn.close()
    return {
        "status": "success",
        "processed_matches": processed_matches,
        "new_awards": total_awards,
    }


def _calc_round_scores(p1_action, p2_action):
    if p1_action == 'C' and p2_action == 'C':
        return 3, 3
    if p1_action == 'D' and p2_action == 'C':
        return 8, -3
    if p1_action == 'C' and p2_action == 'D':
        return -3, 8
    if p1_action == 'D' and p2_action == 'D':
        return -1, -1
    if p1_action is None and p2_action is not None:
        return -5, (8 if p2_action == 'D' else 3)
    if p2_action is None and p1_action is not None:
        return (8 if p1_action == 'D' else 3), -5
    return -5, -5


def _settle_round_if_active(cursor, round_id: int) -> bool:
    cursor.execute("SELECT status FROM rounds WHERE round_id = ?", (round_id,))
    row = cursor.fetchone()
    if not row or row["status"] != "active":
        return False

    cursor.execute("SELECT * FROM matches WHERE round_id = ?", (round_id,))
    matches = cursor.fetchall()

    # Double-trigger settlement at round-close stage to avoid misses from mixed client versions.
    try_record_votes_and_settle_gambling(
        cursor,
        round_id,
        datetime.now(),
        bot_player_id=BOT_PLAYER_ID,
        bot_nickname=BOT_NICKNAME,
        bot_fixed_action=BOT_FIXED_ACTION,
        allow_incomplete_human_votes=True,
    )

    for m in matches:
        # 防止重复结算导致积分重复累加。
        if (m["player1_score"] or 0) != 0 or (m["player2_score"] or 0) != 0:
            continue

        p1_id = m["player1_id"]
        p2_id = m["player2_id"]
        p1_action = m["player1_action"]
        p2_action = m["player2_action"]

        if p1_id == BOT_PLAYER_ID and p1_action is None:
            p1_action = BOT_FIXED_ACTION
            cursor.execute("UPDATE matches SET player1_action = ? WHERE match_id = ?", (p1_action, m["match_id"]))
        if p2_id == BOT_PLAYER_ID and p2_action is None:
            p2_action = BOT_FIXED_ACTION
            cursor.execute("UPDATE matches SET player2_action = ? WHERE match_id = ?", (p2_action, m["match_id"]))

        p1_score, p2_score = _calc_round_scores(p1_action, p2_action)
        if IS_TEST_MODE:
            p1_score *= 10
            p2_score *= 10

        cursor.execute(
            "UPDATE matches SET player1_score = ?, player2_score = ? WHERE match_id = ?",
            (p1_score, p2_score, m["match_id"]),
        )
        cursor.execute("UPDATE players SET total_score = total_score + ? WHERE player_id = ?", (p1_score, p1_id))
        cursor.execute("UPDATE players SET total_score = total_score + ? WHERE player_id = ?", (p2_score, p2_id))

        process_feature_event(
            cursor,
            "match_resolved",
            {
                "round_id": round_id,
                "match_id": m["match_id"],
                "player1_id": p1_id,
                "player2_id": p2_id,
                "player1_action": p1_action,
                "player2_action": p2_action,
                "player1_score": p1_score,
                "player2_score": p2_score,
            },
            round_id=round_id,
        )

    apply_submission_streak_and_auto_kick(cursor, round_id)
    cursor.execute("UPDATE rounds SET status = 'completed' WHERE round_id = ?", (round_id,))
    return True


def _settle_overdue_active_rounds(cursor, now: datetime) -> int:
    if is_maintenance_time(now):
        return 0

    round_info = get_current_round_info(now)
    current_date = round_info["game_date"]
    current_hour = int(round_info["hour"])

    cursor.execute(
        "SELECT round_id, game_date, hour, status FROM rounds WHERE status = 'active' ORDER BY round_id ASC"
    )
    active_rounds = cursor.fetchall()

    settled_count = 0
    for r in active_rounds:
        game_date = r["game_date"]
        hour = int(r["hour"])
        is_past_round = (game_date < current_date) or (game_date == current_date and hour < current_hour)
        is_current_due = (game_date == current_date and hour == current_hour and now.minute >= 31)
        if is_past_round or is_current_due:
            if _settle_round_if_active(cursor, int(r["round_id"])):
                settled_count += 1

    return settled_count


@app.get("/api/scoreboard")
def get_full_scoreboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now()

    settled_count = _settle_overdue_active_rounds(cursor, now)
    if settled_count:
        conn.commit()
        LOGGER.info("Scoreboard auto-settled %s overdue round(s)", settled_count)

    # 前端全量数据同样只暴露 nickname
    cursor.execute("SELECT player_id, nickname, total_score, registered_at FROM players ORDER BY total_score DESC")
    player_rows = cursor.fetchall()
    catalog_map = {item.get("key"): item for item in list_achievement_catalog()}
    all_players = []
    for r in player_rows:
        achievements = get_player_achievements(cursor, r["player_id"])
        for ach in achievements:
            details_json = ach.get("details_json")
            try:
                ach["details"] = json.loads(details_json) if details_json else {}
            except Exception:
                ach["details"] = {}
            ach_key = ach.get("achievement_key")
            ach["description"] = (catalog_map.get(ach_key) or {}).get("description", "")
        all_players.append(
            {
                "nickname": r["nickname"],
                "score": r["total_score"],
                "achievements": achievements,
            }
        )
    
    round_info = get_current_round_info(now)

    cursor.execute(
        "SELECT round_id FROM rounds WHERE game_date = ? AND hour = ? AND minute_slot = ?",
        (round_info["game_date"], round_info["hour"], round_info["minute_slot"]),
    )
    round_row = cursor.fetchone()
    current_round_speeches = []
    if round_row:
        current_round_speeches = get_round_speeches(cursor, round_row["round_id"])

    spotlight_battle = build_previous_round_spotlight(cursor)

    cursor.execute(
        "SELECT round_id, votes_json, recorded_at FROM round_vote_snapshots ORDER BY round_id DESC LIMIT 1"
    )
    vote_snapshot_row = cursor.fetchone()
    latest_round_vote_snapshot = None
    if vote_snapshot_row:
        try:
            latest_round_vote_snapshot = json.loads(vote_snapshot_row["votes_json"])
        except Exception:
            latest_round_vote_snapshot = {
                "round_id": vote_snapshot_row["round_id"],
                "recorded_at": vote_snapshot_row["recorded_at"],
                "votes": [],
            }

    cursor.execute(
        "SELECT round_id, summary_json, settled_at FROM gambling_round_settlements ORDER BY round_id DESC LIMIT 1"
    )
    gambling_settlement_row = cursor.fetchone()
    latest_gambling_settlement = None
    if gambling_settlement_row:
        try:
            latest_gambling_settlement = json.loads(gambling_settlement_row["summary_json"])
        except Exception:
            latest_gambling_settlement = {
                "round_id": gambling_settlement_row["round_id"],
                "settled_at": gambling_settlement_row["settled_at"],
            }
    
    conn.close()
    return {
        "status": "maintenance" if is_maintenance_time(now) else "active",
        "current_round_hour": round_info["hour"],
        "current_round_minute": round_info["minute_slot"] * 10,
        "is_test_mode": IS_TEST_MODE,
        "players": all_players,
        "round_speeches": current_round_speeches,
        "spotlight_battle": spotlight_battle,
        "latest_round_vote_snapshot": latest_round_vote_snapshot,
        "latest_gambling_settlement": latest_gambling_settlement,
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

                    try_record_votes_and_settle_gambling(
                        cursor,
                        round_id,
                        now,
                        bot_player_id=BOT_PLAYER_ID,
                        bot_nickname=BOT_NICKNAME,
                        bot_fixed_action=BOT_FIXED_ACTION,
                        allow_incomplete_human_votes=True,
                    )

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
                        process_feature_event(
                            cursor,
                            "match_resolved",
                            {
                                "round_id": round_id,
                                "match_id": m["match_id"],
                                "player1_id": p1_id,
                                "player2_id": p2_id,
                                "player1_action": p1_action,
                                "player2_action": p2_action,
                                "player1_score": p1_score,
                                "player2_score": p2_score,
                            },
                            round_id=round_id,
                        )

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

                try_record_votes_and_settle_gambling(
                    cursor,
                    round_id,
                    now,
                    bot_player_id=BOT_PLAYER_ID,
                    bot_nickname=BOT_NICKNAME,
                    bot_fixed_action=BOT_FIXED_ACTION,
                    allow_incomplete_human_votes=True,
                )
                
                for m in matches:
                    p1_action = m["player1_action"]
                    p2_action = m["player2_action"]
                    p1_id = m["player1_id"]
                    p2_id = m["player2_id"]

                    # BOT 固定只出合作（C），并写回比赛记录，确保回放/焦点展示一致。
                    if p1_id == BOT_PLAYER_ID and p1_action is None:
                        p1_action = BOT_FIXED_ACTION
                        cursor.execute("UPDATE matches SET player1_action = ? WHERE match_id = ?", (p1_action, m["match_id"]))
                    if p2_id == BOT_PLAYER_ID and p2_action is None:
                        p2_action = BOT_FIXED_ACTION
                        cursor.execute("UPDATE matches SET player2_action = ? WHERE match_id = ?", (p2_action, m["match_id"]))

                    # BOT 固定只出合作（C），并写回比赛记录，确保回放/焦点展示一致。
                    if p1_id == BOT_PLAYER_ID and p1_action is None:
                        p1_action = BOT_FIXED_ACTION
                        cursor.execute("UPDATE matches SET player1_action = ? WHERE match_id = ?", (p1_action, m["match_id"]))
                    if p2_id == BOT_PLAYER_ID and p2_action is None:
                        p2_action = BOT_FIXED_ACTION
                        cursor.execute("UPDATE matches SET player2_action = ? WHERE match_id = ?", (p2_action, m["match_id"]))
                    
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
                    process_feature_event(
                        cursor,
                        "match_resolved",
                        {
                            "round_id": round_id,
                            "match_id": m["match_id"],
                            "player1_id": p1_id,
                            "player2_id": p2_id,
                            "player1_action": p1_action,
                            "player2_action": p2_action,
                            "player1_score": p1_score,
                            "player2_score": p2_score,
                        },
                        round_id=round_id,
                    )

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