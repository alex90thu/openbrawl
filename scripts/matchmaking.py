import random
from collections import defaultdict

from .db_helpers import get_db_connection
from .runtime import (
    LOW_SCORE_THRESHOLD,
    PAIR_JITTER_MAX,
    PAIR_LOW_SCORE_BIAS,
    PAIR_RECENT_PENALTY_WEIGHT,
    PAIR_SCORE_DIFF_WEIGHT,
    RECENT_ROUND_WINDOW,
)


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
    players = [{"player_id": row["player_id"], "total_score": row["total_score"] or 0} for row in cursor.fetchall()]

    if len(players) < 2:
        return 0

    recent_counter = get_recent_pair_counter(cursor, RECENT_ROUND_WINDOW)
    pairings = build_weighted_pairings(players, recent_counter, allow_bot_fill=allow_bot_fill)

    created = 0
    for player_a, player_b in pairings:
        cursor.execute(
            "INSERT INTO matches (round_id, player1_id, player2_id) VALUES (?, ?, ?)",
            (round_id, player_a, player_b),
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
    unmatched = [{"player_id": row["player_id"], "total_score": row["total_score"] or 0} for row in cursor.fetchall()]
    if len(unmatched) < 2:
        return 0

    recent_counter = get_recent_pair_counter(cursor, RECENT_ROUND_WINDOW)
    pairings = build_weighted_pairings(unmatched, recent_counter, allow_bot_fill=False)

    created = 0
    for player_a, player_b in pairings:
        cursor.execute(
            "INSERT INTO matches (round_id, player1_id, player2_id) VALUES (?, ?, ?)",
            (round_id, player_a, player_b),
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
    from .db_helpers import assign_special_speaker

    assign_special_speaker(cursor, new_round_id)
    created = create_round_matches_if_needed(cursor, new_round_id, allow_bot_fill=True)
    if created:
        import logging

        logging.getLogger("openclaw.server").info("Round %s initialized with %s matches", new_round_id, created)

    conn.commit()
    conn.close()
    return new_round_id
