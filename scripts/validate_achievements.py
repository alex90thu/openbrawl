import sqlite3
import json

DB_FILE = "data/openclaw_game.db"

# Define the updated rules for achievements
VALID_PATTERNS = {
    "repeater": ["CCCCCCCC", "DDDDDDDD"],
    # Add other achievements and their valid patterns here if needed
}

def validate_and_fix_achievements():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    for achievement_key, valid_patterns in VALID_PATTERNS.items():
        # Fetch all players with the specific achievement
        cursor.execute(
            """
            SELECT player_id, details_json
            FROM player_achievements
            WHERE achievement_key = ?
            """,
            (achievement_key,)
        )
        achievements = cursor.fetchall()

        for player_id, details_json in achievements:
            details = json.loads(details_json)
            action_sequence = details.get("pattern", "")

            # Check if the action sequence matches the valid patterns
            if action_sequence not in valid_patterns:
                print(f"Removing invalid '{achievement_key}' achievement for player {player_id}")

                # Remove the invalid achievement
                cursor.execute(
                    "DELETE FROM player_achievements WHERE player_id = ? AND achievement_key = ?",
                    (player_id, achievement_key),
                )

                # Optionally, adjust the player's score
                cursor.execute(
                    "UPDATE players SET total_score = total_score - ? WHERE player_id = ?",
                    (-10, player_id),
                )

    conn.commit()
    conn.close()

if __name__ == "__main__":
    validate_and_fix_achievements()