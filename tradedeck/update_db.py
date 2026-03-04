import sqlite3

def upgrade_db():
    conn = sqlite3.connect('tradedeck_local.db')
    cursor = conn.cursor()
    columns_to_add = [
        ("thought_process", "TEXT"),
        ("stop_loss", "REAL"),
        ("target_price", "REAL")
    ]
    
    # Check existing columns
    cursor.execute("PRAGMA table_info(strategy_states);")
    existing_columns = [col[1] for col in cursor.fetchall()]
    
    for col_name, col_type in columns_to_add:
        if col_name not in existing_columns:
            print(f"Adding column {col_name} to strategy_states...")
            cursor.execute(f"ALTER TABLE strategy_states ADD COLUMN {col_name} {col_type};")
        else:
            print(f"Column {col_name} already exists.")
            
    conn.commit()
    conn.close()
    print("Database upgrade complete.")

if __name__ == '__main__':
    upgrade_db()
