import sys
import sqlite3
from datetime import datetime
import os


def load_local_env():
    for env_path in (".ENV", ".env"):
        if not os.path.exists(env_path):
            continue
        try:
            with open(env_path, "r", encoding="utf-8") as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except Exception:
            pass


load_local_env()

DB_FILE = "data/openclaw_game.db"
if '--test' in sys.argv:
    DB_FILE = "data/openclaw_game.db2"

BROADCAST_FILE = os.getenv("OPENCLAW_BROADCAST_FILE", "data/broadcast.json")

def save_broadcast(msg_type, content):
    import json
    data = {
        "type": msg_type,
        "content": content,
        "timestamp": datetime.now().isoformat()
    }
    with open(BROADCAST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"广播包已写入 {BROADCAST_FILE}，内容：{data}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python3 broadcast.py <type> <content>")
        sys.exit(1)
    msg_type = sys.argv[1]
    content = sys.argv[2]
    save_broadcast(msg_type, content)
