import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .features import FeatureEvent, dispatch_feature_event, register_feature_handler


DEFAULT_ACHIEVEMENT_CATALOG = [
    {
        "key": "predator_strike",
        "name": "Predator Strike",
        "description": "Defect against a cooperating opponent for the first time.",
        "score_bonus": 15,
        "trigger": {
            "event_type": "match_resolved",
            "action_pattern": "DC",
            "required_occurrences": 1,
            "require_consecutive": False,
            "recent_matches_window": 10,
        },
    },
    {
        "key": "peacekeeper",
        "name": "Peacekeeper",
        "description": "Cooperate in a fully cooperative match for the first time.",
        "score_bonus": 10,
        "trigger": {
            "event_type": "match_resolved",
            "action_pattern": "CC",
            "required_occurrences": 1,
            "require_consecutive": False,
            "recent_matches_window": 10,
        },
    },
    {
        "key": "iron_clash",
        "name": "Iron Clash",
        "description": "Mutual defection for the first time.",
        "score_bonus": 8,
        "trigger": {
            "event_type": "match_resolved",
            "action_pattern": "DD",
            "required_occurrences": 1,
            "require_consecutive": False,
            "recent_matches_window": 10,
        },
    },
    {
        "key": "chaos_orator",
        "name": "Chaos Orator",
        "description": "Submit a valid Chaos Speaker speech.",
        "score_bonus": 12,
        "trigger": {
            "event_type": "speech_submitted",
            "min_speech_content_length": 1,
        },
    },
]

CATALOG_FILE = Path(__file__).resolve().parent.parent / "data" / "achievement_catalog.json"


def _normalize_catalog(raw_catalog: Any) -> list[dict[str, Any]]:
    if isinstance(raw_catalog, dict):
        raw_catalog = raw_catalog.get("achievements", [])
    if not isinstance(raw_catalog, list):
        return DEFAULT_ACHIEVEMENT_CATALOG

    normalized: list[dict[str, Any]] = []
    for item in raw_catalog:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        name = str(item.get("name", "")).strip()
        description = str(item.get("description", "")).strip()
        en_name = str(item.get("en_name", "")).strip()
        en_description = str(item.get("en_description", "")).strip()
        score_bonus = item.get("score_bonus", 0)

        if not key or not name:
            continue

        try:
            score_bonus_int = int(score_bonus)
        except (TypeError, ValueError):
            continue

        normalized.append(
            {
                "key": key,
                "name": name,
                "description": description,
                "en_name": en_name,
                "en_description": en_description,
                "score_bonus": score_bonus_int,
                "trigger": item.get("trigger") if isinstance(item.get("trigger"), dict) else None,
            }
        )

    return normalized or DEFAULT_ACHIEVEMENT_CATALOG


def _load_catalog_from_file() -> list[dict[str, Any]]:
    if not CATALOG_FILE.exists():
        return DEFAULT_ACHIEVEMENT_CATALOG

    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_ACHIEVEMENT_CATALOG

    return _normalize_catalog(data)


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return default


def _default_trigger_for_key(achievement_key: str) -> dict[str, Any]:
    normalized_key = str(achievement_key or "").strip().lower()
    defaults = {
        "predator_strike": {
            "event_type": "match_resolved",
            "action_pattern": "DC",
            "required_occurrences": 1,
            "require_consecutive": False,
            "recent_matches_window": 10,
        },
        "peacekeeper": {
            "event_type": "match_resolved",
            "action_pattern": "CC",
            "required_occurrences": 1,
            "require_consecutive": False,
            "recent_matches_window": 10,
        },
        "iron_clash": {
            "event_type": "match_resolved",
            "action_pattern": "DD",
            "required_occurrences": 1,
            "require_consecutive": False,
            "recent_matches_window": 10,
        },
        "chaos_orator": {
            "event_type": "speech_submitted",
            "min_speech_content_length": 1,
        },
        "saint": {
            "event_type": "match_resolved",
            "action_pattern": "CC",
            "required_occurrences": 5,
            "require_consecutive": True,
            "recent_matches_window": 10,
            "score_delta_min": 1,
        },
        "seigi no mikata": {
            "event_type": "match_resolved",
            "action_pattern": "DC",
            "required_occurrences": 1,
            "require_consecutive": False,
            "recent_matches_window": 10,
            # 对手上一场自己的行动需为 D，表示其刚背叛过别人
            "opponent_previous_action_in": ["D"],
        },
    }
    return defaults.get(normalized_key, {"event_type": "__disabled__"})


def _resolve_trigger(rule: dict[str, Any]) -> dict[str, Any]:
    raw_trigger = rule.get("trigger")
    if isinstance(raw_trigger, dict):
        trigger = dict(raw_trigger)
    else:
        trigger = _default_trigger_for_key(rule.get("key", ""))

    trigger["event_type"] = str(trigger.get("event_type", "match_resolved")).strip() or "match_resolved"

    action_pattern = trigger.get("action_pattern")
    if action_pattern is not None:
        action_pattern = str(action_pattern).strip().upper()
        if len(action_pattern) != 2:
            action_pattern = None
    trigger["action_pattern"] = action_pattern

    trigger["required_occurrences"] = max(1, _to_int(trigger.get("required_occurrences", 1), 1))
    trigger["recent_matches_window"] = max(1, _to_int(trigger.get("recent_matches_window", 10), 10))
    trigger["require_consecutive"] = _to_bool(trigger.get("require_consecutive", False), False)

    if "score_delta_min" in trigger and trigger["score_delta_min"] is not None:
        trigger["score_delta_min"] = _to_int(trigger["score_delta_min"], 0)
    if "score_delta_max" in trigger and trigger["score_delta_max"] is not None:
        trigger["score_delta_max"] = _to_int(trigger["score_delta_max"], 0)
    if "min_speech_content_length" in trigger and trigger["min_speech_content_length"] is not None:
        trigger["min_speech_content_length"] = max(0, _to_int(trigger["min_speech_content_length"], 0))

    if "opponent_previous_action_in" in trigger:
        raw_set = trigger.get("opponent_previous_action_in")
        if isinstance(raw_set, list):
            trigger["opponent_previous_action_in"] = [
                str(x).strip().upper() for x in raw_set if str(x).strip().upper() in {"C", "D"}
            ]
        else:
            trigger["opponent_previous_action_in"] = []

    return trigger


def list_achievement_catalog() -> list[dict[str, Any]]:
    return _load_catalog_from_file()


def get_player_achievements(cursor, player_id: str) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT achievement_key, achievement_name, score_bonus, source_event, details_json, awarded_at
        FROM player_achievements
        WHERE player_id = ?
        ORDER BY awarded_at DESC, achievement_id DESC
        """,
        (player_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _award_once(
    cursor,
    player_id: str,
    achievement_key: str,
    achievement_name: str,
    score_bonus: int,
    source_event: str,
    details: dict[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    cursor.execute(
        "SELECT 1 FROM player_achievements WHERE player_id = ? AND achievement_key = ?",
        (player_id, achievement_key),
    )
    if cursor.fetchone():
        return None

    details_json = json.dumps(details, ensure_ascii=False, sort_keys=True)
    cursor.execute(
        """
        INSERT INTO player_achievements
            (player_id, achievement_key, achievement_name, score_bonus, source_event, details_json, awarded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            player_id,
            achievement_key,
            achievement_name,
            score_bonus,
            source_event,
            details_json,
            now.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    cursor.execute(
        "UPDATE players SET total_score = total_score + ? WHERE player_id = ?",
        (score_bonus, player_id),
    )
    return {
        "player_id": player_id,
        "achievement_key": achievement_key,
        "achievement_name": achievement_name,
        "score_bonus": score_bonus,
        "source_event": source_event,
    }


def _get_player_recent_action_patterns(cursor, player_id: str, limit_rows: int) -> list[str]:
    cursor.execute(
        """
        SELECT player1_id, player2_id, player1_action, player2_action
        FROM matches
        WHERE (player1_id = ? OR player2_id = ?)
          AND player1_action IS NOT NULL
          AND player2_action IS NOT NULL
        ORDER BY round_id DESC, match_id DESC
        LIMIT ?
        """,
        (player_id, player_id, limit_rows),
    )

    patterns: list[str] = []
    for row in cursor.fetchall():
        if row["player1_id"] == player_id:
            own = row["player1_action"]
            opp = row["player2_action"]
        else:
            own = row["player2_action"]
            opp = row["player1_action"]
        patterns.append(f"{own}{opp}")
    return patterns


def _match_pattern_requirements(
    cursor,
    player_id: str,
    current_pattern: str,
    trigger: dict[str, Any],
) -> bool:
    action_pattern = trigger.get("action_pattern")
    required_occurrences = trigger.get("required_occurrences", 1)
    recent_window = trigger.get("recent_matches_window", 10)
    require_consecutive = trigger.get("require_consecutive", False)

    if action_pattern and current_pattern != action_pattern:
        return False

    if required_occurrences <= 1:
        return True

    history = _get_player_recent_action_patterns(cursor, player_id, recent_window)
    sequence = [current_pattern] + history

    if action_pattern:
        if require_consecutive:
            return len(sequence) >= required_occurrences and all(
                p == action_pattern for p in sequence[:required_occurrences]
            )
        return sequence.count(action_pattern) >= required_occurrences

    # 未指定 action_pattern 时，按当前 pattern 统计
    if require_consecutive:
        return len(sequence) >= required_occurrences and all(
            p == current_pattern for p in sequence[:required_occurrences]
        )
    return sequence.count(current_pattern) >= required_occurrences


def _check_score_delta(trigger: dict[str, Any], own_score: int) -> bool:
    score_delta_min = trigger.get("score_delta_min")
    score_delta_max = trigger.get("score_delta_max")
    if score_delta_min is not None and own_score < score_delta_min:
        return False
    if score_delta_max is not None and own_score > score_delta_max:
        return False
    return True


def _get_opponent_previous_action(cursor, opponent_id: str, current_round_id: int | None) -> str | None:
    if not opponent_id:
        return None

    if current_round_id is not None:
        cursor.execute(
            """
            SELECT player1_id, player2_id, player1_action, player2_action
            FROM matches
            WHERE (player1_id = ? OR player2_id = ?)
              AND player1_action IS NOT NULL
              AND player2_action IS NOT NULL
              AND round_id < ?
            ORDER BY round_id DESC, match_id DESC
            LIMIT 1
            """,
            (opponent_id, opponent_id, current_round_id),
        )
    else:
        cursor.execute(
            """
            SELECT player1_id, player2_id, player1_action, player2_action
            FROM matches
            WHERE (player1_id = ? OR player2_id = ?)
              AND player1_action IS NOT NULL
              AND player2_action IS NOT NULL
            ORDER BY round_id DESC, match_id DESC
            LIMIT 1
            """,
            (opponent_id, opponent_id),
        )

    row = cursor.fetchone()
    if not row:
        return None

    if row["player1_id"] == opponent_id:
        return row["player1_action"]
    return row["player2_action"]


def _check_opponent_constraints(
    cursor,
    trigger: dict[str, Any],
    opponent_id: str | None,
    current_round_id: int | None,
) -> bool:
    required_actions = trigger.get("opponent_previous_action_in")
    if not required_actions:
        return True

    opponent_prev_action = _get_opponent_previous_action(cursor, opponent_id or "", current_round_id)
    return opponent_prev_action in set(required_actions)


@register_feature_handler("match_resolved")
def award_match_achievements(cursor, event: FeatureEvent) -> list[dict[str, Any]]:
    payload = event.payload
    now = event.created_at
    awards: list[dict[str, Any]] = []
    catalog = _load_catalog_from_file()

    match_contexts = [
        {
            "player_id": payload.get("player1_id"),
            "own_action": payload.get("player1_action"),
            "opp_action": payload.get("player2_action"),
            "own_score": payload.get("player1_score", 0),
            "opp_id": payload.get("player2_id"),
        },
        {
            "player_id": payload.get("player2_id"),
            "own_action": payload.get("player2_action"),
            "opp_action": payload.get("player1_action"),
            "own_score": payload.get("player2_score", 0),
            "opp_id": payload.get("player1_id"),
        },
    ]

    for context in match_contexts:
        player_id = context["player_id"]
        if not player_id or player_id == "BOT-SHADOW":
            continue

        own_action = context.get("own_action")
        opp_action = context.get("opp_action")
        if own_action not in {"C", "D"} or opp_action not in {"C", "D"}:
            continue

        current_pattern = f"{own_action}{opp_action}"
        own_score = _to_int(context.get("own_score", 0), 0)
        opponent_id = context.get("opp_id")
        current_round_id = payload.get("round_id")

        for rule in catalog:
            trigger = _resolve_trigger(rule)
            if trigger.get("event_type") != "match_resolved":
                continue

            if not _check_score_delta(trigger, own_score):
                continue

            if not _match_pattern_requirements(cursor, player_id, current_pattern, trigger):
                continue

            if not _check_opponent_constraints(cursor, trigger, opponent_id, current_round_id):
                continue

            award = _award_once(
                cursor,
                player_id,
                rule["key"],
                rule["name"],
                _to_int(rule.get("score_bonus", 0), 0),
                "match_resolved",
                {
                    "match_id": payload.get("match_id"),
                    "round_id": payload.get("round_id"),
                    "own_action": own_action,
                    "opp_action": opp_action,
                    "pattern": current_pattern,
                    "own_score": own_score,
                    "trigger": trigger,
                },
                now,
            )
            if award:
                awards.append(award)

    return awards


@register_feature_handler("speech_submitted")
def award_speech_achievements(cursor, event: FeatureEvent) -> list[dict[str, Any]]:
    payload = event.payload
    player_id = payload.get("player_id")
    if not player_id or player_id == "BOT-SHADOW":
        return []

    catalog = _load_catalog_from_file()
    awards: list[dict[str, Any]] = []
    speech_content_length = len(payload.get("speech_content") or "")

    for rule in catalog:
        trigger = _resolve_trigger(rule)
        if trigger.get("event_type") != "speech_submitted":
            continue

        min_len = _to_int(trigger.get("min_speech_content_length", 1), 1)
        if speech_content_length < min_len:
            continue

        award = _award_once(
            cursor,
            player_id,
            rule["key"],
            rule["name"],
            _to_int(rule.get("score_bonus", 0), 0),
            "speech_submitted",
            {
                "round_id": payload.get("round_id"),
                "speech_as": payload.get("speech_as"),
                "speech_content_length": speech_content_length,
                "trigger": trigger,
            },
            event.created_at,
        )
        if award:
            awards.append(award)

    return awards


def process_feature_event(cursor, event_type: str, payload: dict[str, Any], round_id: int | None = None, player_id: str | None = None) -> list[dict[str, Any]]:
    return dispatch_feature_event(
        cursor,
        FeatureEvent(
            event_type=event_type,
            round_id=round_id,
            player_id=player_id,
            payload=payload,
        ),
    )
