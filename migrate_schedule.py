#!/usr/bin/env python3
"""
One-time migration: add `date` column to schedule_items, copy location → date, and drop location.
"""
import sqlite3
from pathlib import Path

db_path = Path(__file__).resolve().parent / "olympiad.db"
print(f"Connecting to: {db_path}")

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check current columns
    cursor.execute("PRAGMA table_info('schedule_items')")
    cols = {r[1]: r[2] for r in cursor.fetchall()}  # name -> type mapping
    print(f"Current columns: {list(cols.keys())}")
    
    # Add `date` column if missing
    if 'date' not in cols:
        print("Adding `date` column...")
        cursor.execute("ALTER TABLE schedule_items ADD COLUMN date TEXT DEFAULT ''")
        conn.commit()
        print("✓ Added `date` column")
    else:
        print("✓ `date` column already exists")
    
    # Copy location → date if location exists
    if 'location' in cols:
        print("Copying `location` → `date`...")
        cursor.execute("UPDATE schedule_items SET date = location WHERE date = '' AND location IS NOT NULL AND location != ''")
        affected = cursor.rowcount
        conn.commit()
        print(f"✓ Copied {affected} rows from `location` to `date`")
        
        # Remove old location column by recreating table without it
        print("Removing old `location` column...")
        cursor.execute("""
            CREATE TABLE schedule_items_new (
                id INTEGER PRIMARY KEY,
                game_id INTEGER,
                title TEXT,
                time TEXT,
                date TEXT DEFAULT '',
                description TEXT
            )
        """)
        cursor.execute("""
            INSERT INTO schedule_items_new (id, game_id, title, time, date, description)
            SELECT id, game_id, title, time, date, description FROM schedule_items
        """)
        cursor.execute("DROP TABLE schedule_items")
        cursor.execute("ALTER TABLE schedule_items_new RENAME TO schedule_items")
        conn.commit()
        print("✓ Removed `location` column and recreated table")
    else:
        print("✓ `location` column not found (already removed)")
    
    # Verify final schema
    cursor.execute("PRAGMA table_info('schedule_items')")
    final_cols = [r[1] for r in cursor.fetchall()]
    print(f"Final columns: {final_cols}")
    
    # Show sample data
    cursor.execute("SELECT COUNT(*) FROM schedule_items")
    count = cursor.fetchone()[0]
    print(f"\n✓ Migration complete! Total schedule items: {count}")
    
    if count > 0:
        cursor.execute("SELECT id, game_id, title, date, time, description FROM schedule_items LIMIT 3")
        print("\nSample data:")
        for row in cursor.fetchall():
            print(f"  {row}")
    
    conn.close()
    print("\n✓ Database migrated successfully!")

except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()
