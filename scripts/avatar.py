from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
from pathlib import Path
from typing import Any

try:
    from pypinyin import lazy_pinyin
except Exception:  # pragma: no cover - fallback for environments without pypinyin
    lazy_pinyin = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent
AVATAR_DIR = PROJECT_ROOT / "assets" / "avatar"
AVATAR_MAP_FILE = PROJECT_ROOT / "data" / "avatar_map.json"


def _default_avatar_map() -> dict[str, dict[str, str]]:
    return {"players": {}, "nicknames": {}}


def load_avatar_map() -> dict[str, dict[str, str]]:
    if not AVATAR_MAP_FILE.exists():
        return _default_avatar_map()

    try:
        raw = json.loads(AVATAR_MAP_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _default_avatar_map()

    if not isinstance(raw, dict):
        return _default_avatar_map()

    players = raw.get("players") if isinstance(raw.get("players"), dict) else {}
    nicknames = raw.get("nicknames") if isinstance(raw.get("nicknames"), dict) else {}
    return {
        "players": {str(key): str(value) for key, value in players.items()},
        "nicknames": {str(key): str(value) for key, value in nicknames.items()},
    }


def save_avatar_map(avatar_map: dict[str, dict[str, str]]) -> None:
    AVATAR_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "players": dict(sorted((avatar_map.get("players") or {}).items())),
        "nicknames": dict(sorted((avatar_map.get("nicknames") or {}).items())),
    }
    AVATAR_MAP_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_slug(text: str) -> str:
    slug = re.sub(r"_+", "_", text).strip("_")
    slug = re.sub(r"[^a-z0-9_]+", "_", slug.lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "avatar"


def nickname_to_avatar_key(nickname: str) -> str:
    raw = (nickname or "").strip()
    if not raw:
        return "avatar_unknown"

    if lazy_pinyin is not None:
        tokens = lazy_pinyin(raw, errors="ignore")
        slug = _normalize_slug("_".join(token for token in tokens if token))
    else:
        slug = _normalize_slug(re.sub(r"[^a-z0-9]+", "_", raw.lower()))
    return f"avatar_{slug}"


def normalize_avatar_key(nickname: str | None = None, avatar_key: str | None = None) -> str:
    candidate = (avatar_key or "").strip()
    if candidate:
        if not candidate.startswith("avatar_"):
            candidate = f"avatar_{candidate}"
        return _normalize_slug(candidate)

    return nickname_to_avatar_key(nickname or "")


def resolve_avatar_key(player_id: str | None = None, nickname: str | None = None, avatar_map: dict[str, dict[str, str]] | None = None) -> str:
    amap = avatar_map or load_avatar_map()
    player_id = (player_id or "").strip()
    nickname = (nickname or "").strip()

    if player_id:
        mapped = (amap.get("players") or {}).get(player_id)
        if mapped:
            return str(mapped).strip()

    if nickname:
        mapped = (amap.get("nicknames") or {}).get(nickname)
        if mapped:
            return str(mapped).strip()

    return normalize_avatar_key(nickname=nickname)


def bind_avatar(player_id: str, nickname: str, avatar_key: str | None = None) -> str:
    key = normalize_avatar_key(nickname=nickname, avatar_key=avatar_key)
    avatar_map = load_avatar_map()
    avatar_map.setdefault("players", {})[str(player_id)] = key
    avatar_map.setdefault("nicknames", {})[str(nickname)] = key
    save_avatar_map(avatar_map)
    return key


def sync_avatar_nickname_change(player_id: str, previous_nickname: str, new_nickname: str, avatar_key: str | None = None) -> str:
    key = resolve_avatar_key(player_id=player_id, nickname=previous_nickname)
    if avatar_key:
        key = normalize_avatar_key(nickname=new_nickname, avatar_key=avatar_key)

    avatar_map = load_avatar_map()
    avatar_map.setdefault("players", {})[str(player_id)] = key
    nicknames = avatar_map.setdefault("nicknames", {})
    if previous_nickname and previous_nickname in nicknames and nicknames[previous_nickname] == key:
        del nicknames[previous_nickname]
    nicknames[str(new_nickname)] = key
    save_avatar_map(avatar_map)
    return key


def _guess_extension_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    suffix = Path(filename).suffix.lower().lstrip(".")
    if suffix in {"png", "jpg", "jpeg", "webp", "gif"}:
        return "jpg" if suffix == "jpeg" else suffix
    return None


def _guess_extension_from_bytes(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "gif"
    if len(image_bytes) >= 12 and image_bytes[0:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "webp"
    return "png"


def decode_avatar_base64(avatar_base64: str) -> bytes:
    raw = (avatar_base64 or "").strip()
    if not raw:
        raise ValueError("avatar_base64 is required")
    if "," in raw and raw.startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=True)
    except binascii.Error:
        return base64.b64decode(raw)


def store_avatar_image(
    image_bytes: bytes,
    avatar_key: str,
    original_filename: str | None = None,
    replace_existing: bool = True,
) -> Path:
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    ext = _guess_extension_from_filename(original_filename) or _guess_extension_from_bytes(image_bytes)
    avatar_key = normalize_avatar_key(avatar_key=avatar_key)
    target = AVATAR_DIR / f"{avatar_key}.{ext}"

    if replace_existing:
        for candidate_ext in {"png", "jpg", "jpeg", "webp", "gif"}:
            candidate = AVATAR_DIR / f"{avatar_key}.{candidate_ext}"
            if candidate != target and candidate.exists():
                candidate.unlink()

    target.write_bytes(image_bytes)
    return target


def upsert_avatar_asset(
    player_id: str,
    nickname: str,
    avatar_base64: str,
    original_filename: str | None = None,
    avatar_key: str | None = None,
) -> dict[str, Any]:
    image_bytes = decode_avatar_base64(avatar_base64)
    key = bind_avatar(player_id, nickname, avatar_key=avatar_key)
    avatar_path = store_avatar_image(image_bytes, key, original_filename=original_filename)
    return {
        "player_id": player_id,
        "nickname": nickname,
        "avatar_key": key,
        "avatar_path": str(avatar_path),
        "avatar_map_file": str(AVATAR_MAP_FILE),
    }


def preview_avatar_key(nickname: str, avatar_key: str | None = None) -> str:
    return normalize_avatar_key(nickname=nickname, avatar_key=avatar_key)


def _main() -> int:
    parser = argparse.ArgumentParser(description="OpenBrawl avatar helper")
    subparsers = parser.add_subparsers(dest="command")

    preview = subparsers.add_parser("preview", help="Preview avatar key from nickname")
    preview.add_argument("nickname")
    preview.add_argument("--avatar-key", default=None)

    sync = subparsers.add_parser("sync", help="Bind avatar and store an image file")
    sync.add_argument("--player-id", required=True)
    sync.add_argument("--nickname", required=True)
    sync.add_argument("--input", required=True, help="Path to the source image file")
    sync.add_argument("--avatar-key", default=None)

    args = parser.parse_args()

    if args.command == "preview":
        print(preview_avatar_key(args.nickname, args.avatar_key))
        return 0

    if args.command == "sync":
        source = Path(args.input)
        result = upsert_avatar_asset(
            player_id=args.player_id,
            nickname=args.nickname,
            avatar_base64=base64.b64encode(source.read_bytes()).decode("ascii"),
            original_filename=source.name,
            avatar_key=args.avatar_key,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())