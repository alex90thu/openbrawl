import sys
import sqlite3
from datetime import datetime


DB_FILE = "data/openclaw_game.db"
if '--test' in sys.argv:
    DB_FILE = "data/openclaw_game.db2"

BROADCAST_FILE = "data/broadcast.json"

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
