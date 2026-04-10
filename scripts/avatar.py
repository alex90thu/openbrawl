from __future__ import annotations

import argparse
import base64
import binascii
import io
import json
import os
import re
from pathlib import Path
from typing import Any

from PIL import Image

try:
    from pypinyin import lazy_pinyin
except Exception:  # pragma: no cover - fallback for environments without pypinyin
    lazy_pinyin = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent
AVATAR_DIR = PROJECT_ROOT / "assets" / "avatar"
AVATAR_MAP_FILE = PROJECT_ROOT / "data" / "avatar_map.json"
GENERIC_AVATAR_KEYS = {"avatar", "avatar_", "avatar_avatar", "default", "unknown", "none", "null"}


WEBP_QUALITY = int(os.getenv("OPENCLAW_AVATAR_WEBP_QUALITY", "82"))


def _default_avatar_map() -> dict[str, dict[str, str]]:
    return {"players": {}, "nicknames": {}, "files": {}, "nickname_files": {}}


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
    files = raw.get("files") if isinstance(raw.get("files"), dict) else {}
    nickname_files = raw.get("nickname_files") if isinstance(raw.get("nickname_files"), dict) else {}
    return {
        "players": {str(key): str(value) for key, value in players.items()},
        "nicknames": {str(key): str(value) for key, value in nicknames.items()},
        "files": {str(key): str(value) for key, value in files.items()},
        "nickname_files": {str(key): str(value) for key, value in nickname_files.items()},
    }


def save_avatar_map(avatar_map: dict[str, dict[str, str]]) -> None:
    AVATAR_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "players": dict(sorted((avatar_map.get("players") or {}).items())),
        "nicknames": dict(sorted((avatar_map.get("nicknames") or {}).items())),
        "files": dict(sorted((avatar_map.get("files") or {}).items())),
        "nickname_files": dict(sorted((avatar_map.get("nickname_files") or {}).items())),
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


def _normalize_avatar_key_input(avatar_key: str | None) -> str | None:
    raw = (avatar_key or "").strip()
    if not raw:
        return None
    if raw.lower() in GENERIC_AVATAR_KEYS:
        return None
    normalized = normalize_avatar_key(avatar_key=raw)
    if normalized.lower() in GENERIC_AVATAR_KEYS:
        return None
    return normalized


def _allocate_unique_avatar_key(
    base_key: str,
    avatar_map: dict[str, dict[str, str]],
    player_id: str | None = None,
) -> str:
    players_map = avatar_map.get("players") or {}
    player_id = (player_id or "").strip()

    # Same player keeps its existing mapping.
    existing_for_player = players_map.get(player_id) if player_id else None
    if existing_for_player:
        return str(existing_for_player)

    used_keys = {str(v) for v in players_map.values() if str(v).strip()}
    if base_key not in used_keys:
        return base_key

    index = 2
    while True:
        candidate = f"{base_key}{index}"
        if candidate not in used_keys:
            return candidate
        index += 1


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
    avatar_map = load_avatar_map()
    provided = _normalize_avatar_key_input(avatar_key)
    base_key = provided or nickname_to_avatar_key(nickname)
    key = _allocate_unique_avatar_key(base_key, avatar_map, player_id=player_id)
    avatar_map.setdefault("players", {})[str(player_id)] = key
    avatar_map.setdefault("nicknames", {})[str(nickname)] = key
    save_avatar_map(avatar_map)
    return key


def sync_avatar_nickname_change(player_id: str, previous_nickname: str, new_nickname: str, avatar_key: str | None = None) -> str:
    key = resolve_avatar_key(player_id=player_id, nickname=previous_nickname)
    if avatar_key:
        provided = _normalize_avatar_key_input(avatar_key)
        if provided:
            avatar_map = load_avatar_map()
            key = _allocate_unique_avatar_key(provided, avatar_map, player_id=player_id)

    avatar_map = load_avatar_map()
    avatar_map.setdefault("players", {})[str(player_id)] = key
    nicknames = avatar_map.setdefault("nicknames", {})
    if previous_nickname and previous_nickname in nicknames and nicknames[previous_nickname] == key:
        del nicknames[previous_nickname]
    nicknames[str(new_nickname)] = key

    file_name = (avatar_map.get("files") or {}).get(str(player_id))
    nickname_files = avatar_map.setdefault("nickname_files", {})
    if previous_nickname and previous_nickname in nickname_files:
        del nickname_files[previous_nickname]
    if file_name:
        nickname_files[str(new_nickname)] = str(file_name)

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


def convert_image_to_webp(image_bytes: bytes) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as img:
        # Keep alpha channel when present, otherwise use RGB for better compression.
        if img.mode in {"RGBA", "LA"} or (img.mode == "P" and "transparency" in img.info):
            processed = img.convert("RGBA")
        else:
            processed = img.convert("RGB")

        output = io.BytesIO()
        processed.save(output, format="WEBP", quality=WEBP_QUALITY, method=6)
        return output.getvalue()


def store_avatar_image(
    image_bytes: bytes,
    avatar_key: str,
    original_filename: str | None = None,
    replace_existing: bool = True,
) -> Path:
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    _ = _guess_extension_from_filename(original_filename) or _guess_extension_from_bytes(image_bytes)
    image_bytes = convert_image_to_webp(image_bytes)
    ext = "webp"
    avatar_key = normalize_avatar_key(avatar_key=avatar_key)
    target = AVATAR_DIR / f"{avatar_key}.{ext}"

    if replace_existing:
        for candidate_ext in {"png", "jpg", "jpeg", "webp", "gif"}:
            candidate = AVATAR_DIR / f"{avatar_key}.{candidate_ext}"
            if candidate != target and candidate.exists():
                candidate.unlink()

    target.write_bytes(image_bytes)
    return target


def bind_avatar_file(player_id: str, nickname: str, avatar_path: Path) -> None:
    avatar_map = load_avatar_map()
    file_name = avatar_path.name
    avatar_map.setdefault("files", {})[str(player_id)] = file_name
    avatar_map.setdefault("nickname_files", {})[str(nickname)] = file_name
    save_avatar_map(avatar_map)


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
    bind_avatar_file(player_id, nickname, avatar_path)
    return {
        "player_id": player_id,
        "nickname": nickname,
        "avatar_key": key,
        "avatar_path": str(avatar_path),
        "avatar_map_file": str(AVATAR_MAP_FILE),
    }


def _find_avatar_file_by_key(avatar_key: str) -> Path | None:
    key = normalize_avatar_key(avatar_key=avatar_key)
    for ext in ["webp", "png", "jpg", "jpeg", "gif"]:
        candidate = AVATAR_DIR / f"{key}.{ext}"
        if candidate.exists():
            return candidate
    return None


def migrate_avatars_to_webp() -> dict[str, Any]:
    avatar_map = load_avatar_map()
    converted = 0
    skipped = 0
    failed = 0

    players = avatar_map.get("players") or {}
    nicknames = avatar_map.get("nicknames") or {}

    for player_id, avatar_key in players.items():
        source = _find_avatar_file_by_key(avatar_key)
        if source is None:
            skipped += 1
            continue

        if source.suffix.lower() == ".webp":
            avatar_map.setdefault("files", {})[str(player_id)] = source.name
            skipped += 1
            continue

        try:
            webp_bytes = convert_image_to_webp(source.read_bytes())
            target = AVATAR_DIR / f"{normalize_avatar_key(avatar_key=avatar_key)}.webp"
            target.write_bytes(webp_bytes)
            if source != target and source.exists():
                source.unlink()
            avatar_map.setdefault("files", {})[str(player_id)] = target.name
            converted += 1
        except Exception:
            failed += 1

    # Rebuild nickname -> file mapping from nickname -> avatar_key.
    nickname_files = avatar_map.setdefault("nickname_files", {})
    nickname_files.clear()
    for nickname, avatar_key in nicknames.items():
        found = _find_avatar_file_by_key(avatar_key)
        if found:
            nickname_files[str(nickname)] = found.name

    save_avatar_map(avatar_map)
    return {
        "converted": converted,
        "skipped": skipped,
        "failed": failed,
        "avatar_map_file": str(AVATAR_MAP_FILE),
    }


def preview_avatar_key(nickname: str, avatar_key: str | None = None) -> str:
    avatar_map = load_avatar_map()
    provided = _normalize_avatar_key_input(avatar_key)
    base_key = provided or nickname_to_avatar_key(nickname)
    return _allocate_unique_avatar_key(base_key, avatar_map, player_id=None)


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

    subparsers.add_parser("migrate-webp", help="Convert existing avatar assets to webp and update avatar map file links")

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

    if args.command == "migrate-webp":
        result = migrate_avatars_to_webp()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())