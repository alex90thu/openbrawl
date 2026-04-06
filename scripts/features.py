from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional
import json
import sqlite3
from collections import defaultdict


@dataclass
class FeatureEvent:
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    round_id: Optional[int] = None
    player_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)


FeatureHandler = Callable[[sqlite3.Cursor, FeatureEvent], list[dict[str, Any]]]
FEATURE_HANDLERS: dict[str, list[FeatureHandler]] = defaultdict(list)


def register_feature_handler(event_type: str):
    def decorator(handler: FeatureHandler):
        FEATURE_HANDLERS[event_type].append(handler)
        return handler

    return decorator


def dispatch_feature_event(cursor: sqlite3.Cursor, event: FeatureEvent) -> list[dict[str, Any]]:
    cursor.execute(
        """
        INSERT INTO feature_event_log (event_type, player_id, round_id, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            event.event_type,
            event.player_id,
            event.round_id,
            json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
            event.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )

    awards: list[dict[str, Any]] = []
    for handler in FEATURE_HANDLERS.get(event.event_type, []):
        awards.extend(handler(cursor, event))
    return awards
