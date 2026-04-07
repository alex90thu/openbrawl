import sqlite3
from scripts.db_helpers import DB_FILE

def unban_fingerprint(fingerprint):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM fingerprint_bans WHERE fingerprint = ?", (fingerprint,))
    conn.commit()
    conn.close()
    print(f"Fingerprint {fingerprint} has been unbanned.")

if __name__ == "__main__":
    fingerprint_to_unban = "68c7ff1c294d9c9684feb58dbdeba276c3dbbcc1eefeaf188e1d7ba521b98e66"
    unban_fingerprint(fingerprint_to_unban)