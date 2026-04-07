import sqlite3
from datetime import datetime
import json

DB_FILE = "data/openclaw_game.db"

# Define the new rule for "Repeater"
NEW_ACTION_PATTERN = "CCCCCCCC|DDDDDDDD"


def fix_repeater_achievement():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Fetch all players who have the "Repeater" achievement
    cursor.execute(
        """
        SELECT player_id, details_json
        FROM player_achievements
        WHERE achievement_key = 'repeater'
        """
    )
    repeater_achievements = cursor.fetchall()

    for player_id, details_json in repeater_achievements:
        details = json.loads(details_json)
        action_sequence = details.get("pattern", "")

        # Check if the action sequence still matches the new rule
        if not (action_sequence == "CCCCCCCC" or action_sequence == "DDDDDDDD"):
            print(f"Removing invalid 'Repeater' achievement for player {player_id}")

            # Remove the invalid achievement
            cursor.execute(
                "DELETE FROM player_achievements WHERE player_id = ? AND achievement_key = 'repeater'",
                (player_id,),
            )

            # Optionally, adjust the player's score
            cursor.execute(
                "UPDATE players SET total_score = total_score - ? WHERE player_id = ?",
                (-10, player_id),
            )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    fix_repeater_achievement()