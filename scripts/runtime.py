import os
import sys


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

IS_TEST_MODE = "--test" in sys.argv

DB_FILE = (
    os.getenv("OPENCLAW_DB_FILE_TEST", "data/openclaw_game.db2")
    if IS_TEST_MODE
    else os.getenv("OPENCLAW_DB_FILE", "data/openclaw_game.db")
)
BROADCAST_FILE = os.getenv("OPENCLAW_BROADCAST_FILE", "data/broadcast.json")
API_HOST = os.getenv("OPENCLAW_API_HOST", "0.0.0.0")
API_PORT_RAW = os.getenv("OPENCLAW_API_PORT")
if not API_PORT_RAW:
    raise RuntimeError("OPENCLAW_API_PORT is required. Please set it in .ENV.")
API_PORT = int(API_PORT_RAW)
AUTO_KICK_MISS_STREAK = int(os.getenv("OPENCLAW_AUTO_KICK_MISS_STREAK", "3"))
FINGERPRINT_BAN_HOURS = int(os.getenv("OPENCLAW_FINGERPRINT_BAN_HOURS", "24"))
RECENT_ROUND_WINDOW = int(os.getenv("OPENCLAW_RECENT_ROUND_WINDOW", "6"))
LOW_SCORE_THRESHOLD = int(os.getenv("OPENCLAW_LOW_SCORE_THRESHOLD", "-500"))
PAIR_RECENT_PENALTY_WEIGHT = int(os.getenv("OPENCLAW_PAIR_RECENT_PENALTY_WEIGHT", "1000"))
PAIR_SCORE_DIFF_WEIGHT = int(os.getenv("OPENCLAW_PAIR_SCORE_DIFF_WEIGHT", "1"))
PAIR_LOW_SCORE_BIAS = int(os.getenv("OPENCLAW_PAIR_LOW_SCORE_BIAS", "160"))
PAIR_JITTER_MAX = float(os.getenv("OPENCLAW_PAIR_JITTER_MAX", "5"))
SPEECH_RETRY_INTERVAL_MINUTES = int(os.getenv("OPENCLAW_SPEECH_RETRY_INTERVAL_MINUTES", "10"))
SPEECH_DEADLINE_MINUTE = int(os.getenv("OPENCLAW_SPEECH_DEADLINE_MINUTE", "30"))
TEST_SPEECH_DEADLINE_MINUTE_IN_SLOT = int(os.getenv("OPENCLAW_TEST_SPEECH_DEADLINE_MINUTE_IN_SLOT", "9"))
