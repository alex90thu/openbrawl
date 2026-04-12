from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any


LOG_DIR = Path("log") / "games"
RECORD_DIR = Path("data") / "records"
TS_PATTERN = re.compile(r"_(\d{8}_\d{6})$")


def _parse_ts(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def _extract_file_ts(path: Path) -> datetime | None:
    matched = TS_PATTERN.search(path.name)
    if not matched:
        return None
    return _parse_ts(matched.group(1))


def _parse_logged_at(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _default_db_name() -> str:
    db_path = os.getenv("OPENCLAW_DB_FILE", "data/openclaw_game.db")
    return Path(db_path).name


def _pick_backup_file(logged_at: datetime | None, db_name: str) -> Path | None:
    candidates: list[tuple[datetime, Path]] = []
    for path in sorted(RECORD_DIR.glob(f"{db_name}_*")):
        ts = _extract_file_ts(path)
        if ts is None:
            continue
        candidates.append((ts, path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])

    if logged_at is None:
        return candidates[-1][1]

    at_or_after = [item for item in candidates if item[0] >= logged_at]
    if at_or_after:
        return at_or_after[0][1]

    return candidates[-1][1]


def _load_settlement_logs() -> list[Path]:
    if not LOG_DIR.exists() or not LOG_DIR.is_dir():
        return []
    return sorted(LOG_DIR.glob("settlement_*.json"), reverse=True)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _select_target_log(paths: list[Path], target_date: str | None) -> tuple[Path, dict[str, Any]] | None:
    for path in paths:
        payload = _load_json(path)
        if not payload:
            continue
        if payload.get("event") != "daily_settlement":
            continue
        if str(payload.get("source_db_backup") or "").strip():
            continue
        if target_date and str(payload.get("target_date") or "").strip() != target_date:
            continue
        return path, payload
    return None


def _backfill_one(target_date: str | None, dry_run: bool, db_name: str) -> int:
    log_paths = _load_settlement_logs()
    target = _select_target_log(log_paths, target_date)
    if not target:
        print("No settlement log needs backfill.")
        return 1

    log_path, payload = target
    logged_at = _parse_logged_at(payload.get("logged_at"))
    backup = _pick_backup_file(logged_at, db_name)
    if backup is None:
        print(f"No backup DB file found in {RECORD_DIR} for prefix {db_name}_.")
        return 2

    payload["source_db_backup"] = str(backup)
    payload["backfilled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if dry_run:
        print(f"[dry-run] target_log={log_path}")
        print(f"[dry-run] selected_backup={backup}")
        return 0

    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Updated settlement log: {log_path}")
    print(f"source_db_backup={backup}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill source_db_backup for settlement logs")
    parser.add_argument("--date", help="Target settlement date in YYYY-MM-DD", default=None)
    parser.add_argument("--db-name", help="Backup DB base name, default from OPENCLAW_DB_FILE", default=_default_db_name())
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no file changes")
    args = parser.parse_args()

    return _backfill_one(args.date, args.dry_run, args.db_name)


if __name__ == "__main__":
    raise SystemExit(main())
