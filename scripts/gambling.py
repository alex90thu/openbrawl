import json
import os
from datetime import datetime
from typing import Any


GAMBLING_LOG_FILE = os.getenv("OPENCLAW_GAMBLING_LOG_FILE", "log/gambling_round.log")


def _write_gambling_log(line: str):
    try:
        log_dir = os.path.dirname(GAMBLING_LOG_FILE)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(GAMBLING_LOG_FILE, "a", encoding="utf-8") as fp:
            fp.write(line + "\n")
    except Exception:
        pass


def _apply_multiplier_score(score_before: int, multiplier: float) -> int:
    """Apply multiplier and avoid zero-delta on tiny non-zero scores.

    Using round() alone can keep 1 at 1 when multiplied by 0.9, which makes a
    failed bet look like no settlement. This helper keeps the multiplier rule
    but guarantees at least 1-point movement for non-zero scores when needed.
    """
    score_after = int(round(score_before * multiplier))
    if score_before != 0 and score_after == score_before:
        if multiplier > 1:
            score_after = score_before + (1 if score_before > 0 else -1)
        elif multiplier < 1:
            score_after = score_before - (1 if score_before > 0 else -1)
    return score_after


def parse_gambling_choice(choice: Any) -> tuple[str | None, str]:
    """Return (bet_on, normalized_raw).

    bet_on values:
    - 'C': user bets C majority
    - 'D': user bets D majority
    - None: user does not participate this round
    """
    normalized = json.dumps(choice, ensure_ascii=False, sort_keys=True)

    if isinstance(choice, bool):
        return ("C" if choice else "D"), normalized

    if isinstance(choice, str):
        token = choice.strip().lower()
        if token in {"t", "true", "1", "yes", "y"}:
            return "C", normalized
        if token in {"f", "false", "0", "no", "n"}:
            return "D", normalized

    return None, normalized


def save_player_gambling_choice(cursor, round_id: int, player_id: str, choice: Any):
    bet_on, raw_choice_json = parse_gambling_choice(choice)
    cursor.execute(
        """
        INSERT INTO player_round_gambling (round_id, player_id, raw_choice_json, bet_on)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(round_id, player_id)
        DO UPDATE SET raw_choice_json = excluded.raw_choice_json, bet_on = excluded.bet_on
        """,
        (round_id, player_id, raw_choice_json, bet_on),
    )
    return {"bet_on": bet_on, "raw_choice_json": raw_choice_json}


def _fetch_existing_vote_snapshot(cursor, round_id: int) -> dict | None:
    cursor.execute("SELECT votes_json FROM round_vote_snapshots WHERE round_id = ?", (round_id,))
    row = cursor.fetchone()
    if not row:
        return None
    try:
        return json.loads(row["votes_json"])
    except Exception:
        return None


def _fetch_round_matches(cursor, round_id: int):
    cursor.execute(
        """
        SELECT match_id, player1_id, player2_id, player1_action, player2_action, p1_submit_time, p2_submit_time
        FROM matches
        WHERE round_id = ?
        ORDER BY match_id ASC
        """,
        (round_id,),
    )
    return cursor.fetchall()


def _fetch_nicknames(cursor, player_ids: list[str]) -> dict[str, str]:
    ids = [pid for pid in player_ids if pid]
    if not ids:
        return {}

    placeholders = ",".join("?" for _ in ids)
    cursor.execute(f"SELECT player_id, nickname FROM players WHERE player_id IN ({placeholders})", ids)
    return {row["player_id"]: row["nickname"] for row in cursor.fetchall()}


def try_record_votes_and_settle_gambling(
    cursor,
    round_id: int,
    now: datetime,
    bot_player_id: str = "BOT-SHADOW",
    bot_nickname: str = "基尼太美",
    bot_fixed_action: str = "C",
) -> dict[str, Any]:
    """Try to settle round gambling when all required votes are in.

    This function is idempotent per round. It only settles once and only after
    every non-bot player has submitted an action.
    """
    cursor.execute("SELECT round_id FROM gambling_round_settlements WHERE round_id = ?", (round_id,))
    if cursor.fetchone():
        existing_snapshot = _fetch_existing_vote_snapshot(cursor, round_id)
        _write_gambling_log(
            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] round_id={round_id} status=already_settled"
        )
        return {
            "status": "already_settled",
            "round_id": round_id,
            "vote_snapshot": existing_snapshot,
        }

    rows = _fetch_round_matches(cursor, round_id)
    if not rows:
        _write_gambling_log(
            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] round_id={round_id} status=not_ready reason=no_matches"
        )
        return {"status": "not_ready", "reason": "no_matches", "round_id": round_id}

    votes: list[dict[str, Any]] = []
    has_pending_human_vote = False

    for row in rows:
        pairs = [
            {
                "player_id": row["player1_id"],
                "action": row["player1_action"],
                "submit_time": row["p1_submit_time"],
                "is_player1": True,
            },
            {
                "player_id": row["player2_id"],
                "action": row["player2_action"],
                "submit_time": row["p2_submit_time"],
                "is_player1": False,
            },
        ]

        for slot in pairs:
            player_id = slot["player_id"]
            action = slot["action"]
            if player_id == bot_player_id and action is None:
                action = bot_fixed_action
                if slot["is_player1"]:
                    cursor.execute("UPDATE matches SET player1_action = ? WHERE match_id = ?", (action, row["match_id"]))
                else:
                    cursor.execute("UPDATE matches SET player2_action = ? WHERE match_id = ?", (action, row["match_id"]))

            if player_id != bot_player_id and action not in {"C", "D"}:
                has_pending_human_vote = True

            votes.append(
                {
                    "match_id": row["match_id"],
                    "player_id": player_id,
                    "action": action,
                    "submitted_at": slot["submit_time"],
                    "is_bot": player_id == bot_player_id,
                }
            )

    if has_pending_human_vote:
        _write_gambling_log(
            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] round_id={round_id} status=not_ready reason=pending_human_votes"
        )
        return {
            "status": "not_ready",
            "reason": "pending_human_votes",
            "round_id": round_id,
        }

    nickname_map = _fetch_nicknames(cursor, [v["player_id"] for v in votes if not v["is_bot"]])
    for vote in votes:
        if vote["is_bot"]:
            vote["nickname"] = bot_nickname
        else:
            vote["nickname"] = nickname_map.get(vote["player_id"], vote["player_id"])

    human_votes = [v for v in votes if not v["is_bot"] and v["action"] in {"C", "D"}]
    c_votes = sum(1 for v in human_votes if v["action"] == "C")
    d_votes = sum(1 for v in human_votes if v["action"] == "D")

    if c_votes > d_votes:
        majority_action = "C"
    elif d_votes > c_votes:
        majority_action = "D"
    else:
        majority_action = "TIE"

    cursor.execute("SELECT game_date, hour, minute_slot FROM rounds WHERE round_id = ?", (round_id,))
    round_row = cursor.fetchone()
    game_date = round_row["game_date"] if round_row else ""
    hour = int(round_row["hour"]) if round_row and round_row["hour"] is not None else 0
    minute_slot = int(round_row["minute_slot"]) if round_row and round_row["minute_slot"] is not None else 0

    vote_snapshot = {
        "round_id": round_id,
        "game_date": game_date,
        "hour": hour,
        "minute_slot": minute_slot,
        "recorded_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "votes": votes,
        "vote_counts": {"C": c_votes, "D": d_votes},
    }

    cursor.execute(
        """
        INSERT OR REPLACE INTO round_vote_snapshots (round_id, game_date, hour, minute_slot, votes_json, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            round_id,
            game_date,
            hour,
            minute_slot,
            json.dumps(vote_snapshot, ensure_ascii=False, sort_keys=True),
            now.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )

    cursor.execute(
        """
        SELECT round_id, player_id, bet_on
        FROM player_round_gambling
        WHERE round_id = ? AND bet_on IN ('C', 'D')
        """,
        (round_id,),
    )
    participants = cursor.fetchall()

    winners = 0
    settlement_rows: list[dict[str, Any]] = []
    winner_nicknames: list[str] = []

    for p in participants:
        player_id = p["player_id"]
        bet_on = p["bet_on"]

        cursor.execute("SELECT total_score FROM players WHERE player_id = ?", (player_id,))
        player_row = cursor.fetchone()
        if not player_row:
            continue

        nickname = _fetch_nicknames(cursor, [player_id]).get(player_id, player_id)

        score_before = int(player_row["total_score"] or 0)

        if majority_action == "TIE":
            multiplier = 0.9
            won = False
        elif bet_on == majority_action:
            multiplier = 1.2
            won = True
            winners += 1
        else:
            multiplier = 0.9
            won = False

        score_after = _apply_multiplier_score(score_before, multiplier)
        score_delta = score_after - score_before

        cursor.execute("UPDATE players SET total_score = ? WHERE player_id = ?", (score_after, player_id))
        cursor.execute(
            """
            UPDATE player_round_gambling
            SET settled = 1,
                won = ?,
                multiplier = ?,
                score_before = ?,
                score_after = ?,
                score_delta = ?,
                settled_at = ?
            WHERE round_id = ? AND player_id = ?
            """,
            (
                won if won is None else int(won),
                multiplier,
                score_before,
                score_after,
                score_delta,
                now.strftime("%Y-%m-%d %H:%M:%S"),
                round_id,
                player_id,
            ),
        )

        settlement_rows.append(
            {
                "player_id": player_id,
                "nickname": nickname,
                "bet_on": bet_on,
                "won": won,
                "multiplier": multiplier,
                "score_before": score_before,
                "score_after": score_after,
                "score_delta": score_delta,
            }
        )

        if won is True:
            winner_nicknames.append(nickname)

        _write_gambling_log(
            "[{ts}] round_id={rid} player_id={pid} nickname={nick} bet_on={bet} won={won} multiplier={mul} before={before} after={after} delta={delta}".format(
                ts=now.strftime("%Y-%m-%d %H:%M:%S"),
                rid=round_id,
                pid=player_id,
                nick=nickname,
                bet=bet_on,
                won=won,
                mul=multiplier,
                before=score_before,
                after=score_after,
                delta=score_delta,
            )
        )

    summary = {
        "round_id": round_id,
        "majority_action": majority_action,
        "c_votes": c_votes,
        "d_votes": d_votes,
        "participating_players": len(participants),
        "winners": winners,
        "winner_nicknames": winner_nicknames,
        "settled_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "players": settlement_rows,
    }

    cursor.execute(
        """
        INSERT INTO gambling_round_settlements
        (round_id, majority_action, c_votes, d_votes, participating_players, winners, summary_json, settled_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            round_id,
            majority_action,
            c_votes,
            d_votes,
            len(participants),
            winners,
            json.dumps(summary, ensure_ascii=False, sort_keys=True),
            now.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )

    _write_gambling_log(
        "[{ts}] round_id={rid} status=settled majority={majority} c_votes={c} d_votes={d} participants={p} winners={w}".format(
            ts=now.strftime("%Y-%m-%d %H:%M:%S"),
            rid=round_id,
            majority=majority_action,
            c=c_votes,
            d=d_votes,
            p=len(participants),
            w=winners,
        )
    )

    return {
        "status": "settled",
        "round_id": round_id,
        "majority_action": majority_action,
        "vote_snapshot": vote_snapshot,
        "gambling_summary": summary,
    }
