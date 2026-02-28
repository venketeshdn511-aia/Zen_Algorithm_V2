import sqlite3
import os
from datetime import datetime

db_path = "tradedeck/tradedeck.db"

def inspect():
    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("--- Feed Heartbeat ---")
    cursor.execute("SELECT * FROM feed_heartbeat")
    rows = cursor.fetchall()
    for row in rows:
        print(row)

    print("\n--- Trading Sessions (Latest) ---")
    cursor.execute("SELECT * FROM trading_sessions ORDER BY date DESC LIMIT 1")
    row = cursor.fetchone()
    print(row)
    
    print("\n--- Strategy States ---")
    cursor.execute("SELECT strategy_name, status, ltp, updated_at FROM strategy_states")
    rows = cursor.fetchall()
    for row in rows:
        print(row)

    conn.close()

if __name__ == "__main__":
    inspect()
