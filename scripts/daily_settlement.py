from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any


OPENCLAW_MIN_SCORE = 300
OPENCLAW_MAX_SCORE = 600
DA_CONG_MING_THRESHOLD = 600


def _rank_text(score: int) -> str:
    if score > 600:
        return "大聪明"
    if score >= 300:
        return "OpenClaw"
    if score >= 243:
        return "全知澳龙"
    if score >= 183:
        return "神经网络鳌"
    if score >= 123:
        return "草泥虾"
    if score >= 63:
        return "算法小虾"
    if score >= 0:
        return "萌新虾兵"
    return "龙虾尾"


def _parse_target_date(raw_date: str | None) -> date:
    if not raw_date:
        return datetime.now().date() - timedelta(days=1)
    try:
        return datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("date must use YYYY-MM-DD format") from exc


def _fetch_player_names(cursor) -> dict[str, str]:
    cursor.execute("SELECT player_id, nickname FROM players")
    return {
        str(row["player_id"]): str(row["nickname"] or row["player_id"])
        for row in cursor.fetchall()
    }


def _fetch_all_known_player_ids(cursor) -> set[str]:
    cursor.execute(
        """
        SELECT player_id FROM players
        UNION
        SELECT player1_id AS player_id FROM matches
        UNION
        SELECT player2_id AS player_id FROM matches
        UNION
        SELECT player_id FROM player_achievements
        UNION
        SELECT player_id FROM player_round_gambling
        """
    )
    return {str(row["player_id"] or "").strip() for row in cursor.fetchall() if str(row["player_id"] or "").strip()}


def _build_window(target_date: date) -> tuple[str, str]:
    window_start = datetime(target_date.year, target_date.month, target_date.day, 10, 0, 0)
    window_end = window_start + timedelta(hours=22)
    return (
        window_start.strftime("%Y-%m-%d %H:%M:%S"),
        window_end.strftime("%Y-%m-%d %H:%M:%S"),
    )


def _collect_score_components(cursor, cutoff_date: str, cutoff_end_at: str) -> dict[str, dict[str, int]]:
    components: dict[str, dict[str, int]] = defaultdict(lambda: {"match": 0, "achievement": 0, "gambling": 0})

    cursor.execute(
        """
        SELECT m.player1_id AS player_id, COALESCE(m.player1_score, 0) AS score_delta
        FROM matches m
        JOIN rounds r ON r.round_id = m.round_id
        WHERE r.status = 'completed' AND r.game_date <= ?
        UNION ALL
        SELECT m.player2_id AS player_id, COALESCE(m.player2_score, 0) AS score_delta
        FROM matches m
        JOIN rounds r ON r.round_id = m.round_id
        WHERE r.status = 'completed' AND r.game_date <= ?
        """,
        (cutoff_date, cutoff_date),
    )
    for row in cursor.fetchall():
        player_id = str(row["player_id"] or "").strip()
        if not player_id:
            continue
        components[player_id]["match"] += int(row["score_delta"] or 0)

    cursor.execute(
        """
        SELECT player_id, COALESCE(SUM(score_bonus), 0) AS score_delta
        FROM player_achievements
                WHERE awarded_at IS NOT NULL
                    AND awarded_at < ?
        GROUP BY player_id
        """,
                (cutoff_end_at,),
    )
    for row in cursor.fetchall():
        player_id = str(row["player_id"] or "").strip()
        if not player_id:
            continue
        components[player_id]["achievement"] += int(row["score_delta"] or 0)

    cursor.execute(
        """
        SELECT player_id, COALESCE(SUM(score_delta), 0) AS score_delta
        FROM player_round_gambling
        WHERE settled = 1
          AND settled_at IS NOT NULL
                    AND settled_at < ?
        GROUP BY player_id
        """,
                (cutoff_end_at,),
    )
    for row in cursor.fetchall():
        player_id = str(row["player_id"] or "").strip()
        if not player_id:
            continue
        components[player_id]["gambling"] += int(row["score_delta"] or 0)

    return components


def _collect_daily_achievement_counts(cursor, window_start_at: str, window_end_at: str) -> dict[str, int]:
    cursor.execute(
        """
        SELECT player_id, COUNT(1) AS achievement_count
        FROM player_achievements
                WHERE awarded_at IS NOT NULL
                    AND awarded_at >= ?
                    AND awarded_at < ?
        GROUP BY player_id
        """,
                (window_start_at, window_end_at),
    )
    return {str(row["player_id"]): int(row["achievement_count"] or 0) for row in cursor.fetchall()}


def _collect_daily_gambling_delta(cursor, window_start_at: str, window_end_at: str) -> dict[str, int]:
    cursor.execute(
        """
        SELECT player_id, COALESCE(SUM(score_delta), 0) AS gambling_delta
        FROM player_round_gambling
        WHERE settled = 1
          AND settled_at IS NOT NULL
                    AND settled_at >= ?
                    AND settled_at < ?
        GROUP BY player_id
        """,
                (window_start_at, window_end_at),
    )
    return {str(row["player_id"]): int(row["gambling_delta"] or 0) for row in cursor.fetchall()}


def _build_player_snapshot(player_id: str, nickname_map: dict[str, str], components: dict[str, dict[str, int]]) -> dict[str, Any]:
    item = components.get(player_id, {"match": 0, "achievement": 0, "gambling": 0})
    score = int(item["match"] + item["achievement"] + item["gambling"])
    return {
        "player_id": player_id,
        "nickname": nickname_map.get(player_id, player_id),
        "score": score,
        "rank_text": _rank_text(score),
    }


def _section_payload(
    key: str,
    title: str,
    subtitle: str,
    metric_label: str,
    metric_value: int,
    players: list[dict[str, Any]],
    empty_text: str,
    value_kind: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "subtitle": subtitle,
        "metric_label": metric_label,
        "metric_value": metric_value,
        "players": players,
        "empty_text": empty_text,
        "value_kind": value_kind,
    }


def build_daily_settlement_summary(cursor, raw_date: str | None = None) -> dict[str, Any]:
    target_date = _parse_target_date(raw_date)
    cutoff_date = target_date.strftime("%Y-%m-%d")
    window_start_at, window_end_at = _build_window(target_date)
    nickname_map = _fetch_player_names(cursor)
    all_player_ids = _fetch_all_known_player_ids(cursor)
    components = _collect_score_components(cursor, cutoff_date, window_end_at)
    daily_achievement_counts = _collect_daily_achievement_counts(cursor, window_start_at, window_end_at)
    daily_gambling_delta = _collect_daily_gambling_delta(cursor, window_start_at, window_end_at)

    snapshots = [
        {
            **_build_player_snapshot(player_id, nickname_map, components),
            "achievement_count": int(daily_achievement_counts.get(player_id, 0)),
            "gambling_delta": int(daily_gambling_delta.get(player_id, 0)),
        }
        for player_id in sorted(all_player_ids)
    ]

    # OpenClaw is checked at final settlement cutoff, not by historical peak score.
    openclaw_players = [
        item
        for item in snapshots
        if OPENCLAW_MIN_SCORE <= int(item["score"]) <= OPENCLAW_MAX_SCORE
    ]
    openclaw_players.sort(key=lambda item: (-item["score"], item["nickname"].lower(), item["player_id"]))

    achievement_top = max((item["achievement_count"] for item in snapshots), default=0)
    best_achievement_players = [item for item in snapshots if item["achievement_count"] == achievement_top and achievement_top > 0]
    best_achievement_players.sort(key=lambda item: (-item["achievement_count"], -item["score"], item["nickname"].lower(), item["player_id"]))

    gambling_gain_top = max((item["gambling_delta"] for item in snapshots), default=0)
    gambling_god_players = [item for item in snapshots if item["gambling_delta"] == gambling_gain_top and gambling_gain_top > 0]
    gambling_god_players.sort(key=lambda item: (-item["gambling_delta"], -item["score"], item["nickname"].lower(), item["player_id"]))

    gambling_loss_bottom = min((item["gambling_delta"] for item in snapshots), default=0)
    dirt_block_players = [item for item in snapshots if item["gambling_delta"] == gambling_loss_bottom and gambling_loss_bottom < 0]
    dirt_block_players.sort(key=lambda item: (item["gambling_delta"], -item["score"], item["nickname"].lower(), item["player_id"]))

    big_smart_players_all = [item for item in snapshots if item["score"] > DA_CONG_MING_THRESHOLD]
    big_smart_top = max((item["score"] for item in big_smart_players_all), default=0)
    big_smart_players = [item for item in big_smart_players_all if item["score"] == big_smart_top and big_smart_top > 0]
    big_smart_players.sort(key=lambda item: (-item["score"], item["nickname"].lower(), item["player_id"]))

    return {
        "summary_date": cutoff_date,
        "summary_title": f"{cutoff_date} 日结算",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window": {
            "start": window_start_at,
            "end": window_end_at,
        },
        "sections": [
            _section_payload(
                key="openclaw",
                title="Openclaw奖",
                subtitle="达到 Openclaw 等级的玩家",
                metric_label="达标人数",
                metric_value=len(openclaw_players),
                players=openclaw_players,
                empty_text="昨日没有玩家达到 Openclaw 等级。",
                value_kind="score",
            ),
            _section_payload(
                key="best_achievement",
                title="最佳成就奖",
                subtitle="昨日获得成就数最多的玩家",
                metric_label="最高成就数",
                metric_value=achievement_top,
                players=best_achievement_players,
                empty_text="昨日没有玩家新增成就。",
                value_kind="count",
            ),
            _section_payload(
                key="gambling_god",
                title="赌神",
                subtitle="昨日累计在赌博中获得分数最多的玩家",
                metric_label="净赚分数",
                metric_value=gambling_gain_top if gambling_god_players else 0,
                players=gambling_god_players,
                empty_text="昨日没有玩家在赌博中实现净收益。",
                value_kind="delta",
            ),
            _section_payload(
                key="dirt_block",
                title="土块",
                subtitle="昨日累计在赌博中失去分数最多的玩家",
                metric_label="净亏分数",
                metric_value=gambling_loss_bottom if dirt_block_players else 0,
                players=dirt_block_players,
                empty_text="昨日没有玩家在赌博中出现净亏损。",
                value_kind="delta",
            ),
            _section_payload(
                key="big_smart",
                title="大愚若智",
                subtitle="大聪明玩家里昨日分数最高的玩家",
                metric_label="最高分",
                metric_value=big_smart_top,
                players=big_smart_players,
                empty_text="昨日没有玩家达到大聪明等级。",
                value_kind="score",
            ),
        ],
    }