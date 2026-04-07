import json


def build_previous_round_spotlight(cursor):
    """Return previous-round spotlight battle payload for leaderboard API.

    Selection rule: choose the match from the latest completed round with
    the maximum sum of absolute net score changes for both players, where
    net change includes base match score and achievement score deltas.
    """
    cursor.execute(
        """
        SELECT round_id, hour, minute_slot
        FROM rounds
        WHERE status = 'completed'
        ORDER BY round_id DESC
        LIMIT 1
        """
    )
    latest_round = cursor.fetchone()
    if not latest_round:
        return None

    round_id = latest_round["round_id"]
    cursor.execute(
        """
        SELECT match_id, player1_id, player2_id, player1_action, player2_action, player1_score, player2_score
        FROM matches
        WHERE round_id = ?
        """,
        (round_id,),
    )
    round_matches = cursor.fetchall()
    if not round_matches:
        return None

    player_ids = set()
    player_to_match = {}
    matches_by_id = {}
    for m in round_matches:
        matches_by_id[m["match_id"]] = m
        p1_id = m["player1_id"]
        p2_id = m["player2_id"]
        if p1_id:
            player_ids.add(p1_id)
            player_to_match[p1_id] = m["match_id"]
        if p2_id:
            player_ids.add(p2_id)
            player_to_match[p2_id] = m["match_id"]

    nickname_map = {}
    if player_ids:
        placeholders = ",".join(["?"] * len(player_ids))
        cursor.execute(
            f"SELECT player_id, nickname FROM players WHERE player_id IN ({placeholders})",
            tuple(player_ids),
        )
        for row in cursor.fetchall():
            nickname_map[row["player_id"]] = row["nickname"]

    achievements_by_match_player = {}
    round_tag = f'%"round_id": {round_id}%'
    cursor.execute(
        """
        SELECT player_id, achievement_key, achievement_name, score_bonus, details_json
        FROM player_achievements
        WHERE details_json LIKE ?
        """,
        (round_tag,),
    )

    for row in cursor.fetchall():
        details_json = row["details_json"]
        try:
            details = json.loads(details_json) if details_json else {}
        except Exception:
            details = {}

        if details.get("round_id") != round_id:
            continue

        match_id = details.get("match_id")
        if match_id is None:
            match_id = player_to_match.get(row["player_id"])
        if match_id not in matches_by_id:
            continue

        key = (match_id, row["player_id"])
        achievements_by_match_player.setdefault(key, []).append(
            {
                "achievement_key": row["achievement_key"],
                "achievement_name": row["achievement_name"],
                "score_bonus": int(row["score_bonus"] or 0),
            }
        )

    best = None
    for m in round_matches:
        p1_id = m["player1_id"]
        p2_id = m["player2_id"]
        p1_base = int(m["player1_score"] or 0)
        p2_base = int(m["player2_score"] or 0)
        p1_achievements = achievements_by_match_player.get((m["match_id"], p1_id), [])
        p2_achievements = achievements_by_match_player.get((m["match_id"], p2_id), [])
        p1_ach_delta = sum(int(a.get("score_bonus") or 0) for a in p1_achievements)
        p2_ach_delta = sum(int(a.get("score_bonus") or 0) for a in p2_achievements)

        p1_total = p1_base + p1_ach_delta
        p2_total = p2_base + p2_ach_delta
        swing_abs_sum = abs(p1_total) + abs(p2_total)

        candidate = {
            "round_id": round_id,
            "round_hour": latest_round["hour"],
            "round_minute": int(latest_round["minute_slot"] or 0) * 10,
            "match_id": m["match_id"],
            "left_player": {
                "player_id": p1_id,
                "nickname": nickname_map.get(p1_id, p1_id),
                "avatar_key": p1_id,
            },
            "right_player": {
                "player_id": p2_id,
                "nickname": nickname_map.get(p2_id, p2_id),
                "avatar_key": p2_id,
            },
            "left_action": m["player1_action"],
            "right_action": m["player2_action"],
            "left_base_delta": p1_base,
            "right_base_delta": p2_base,
            "left_achievement_delta": p1_ach_delta,
            "right_achievement_delta": p2_ach_delta,
            "left_total_delta": p1_total,
            "right_total_delta": p2_total,
            "left_achievements": p1_achievements,
            "right_achievements": p2_achievements,
            "swing_abs_sum": swing_abs_sum,
        }

        if best is None or candidate["swing_abs_sum"] > best["swing_abs_sum"]:
            best = candidate

    return best
