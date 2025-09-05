# migrate_add_cedula_columns.py
import sqlite3, os, sys
DB_PATH = "rooms.db"
print(f"Usando base de datos: {DB_PATH}")
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

def column_exists(table, col):
    cur.execute(f"PRAGMA table_info({table})")
    return col in [r[1] for r in cur.fetchall()]

def add_col(table, col, decl):
    sql = f"ALTER TABLE {table} ADD COLUMN {col} {decl};"
    print(" ->", sql); cur.execute(sql)

cols = [
    ("cedula_status","TEXT"),
    ("cedula_ref","TEXT"),
    ("cedula_expiry","DATE"),
    ("cedula_locked","INTEGER DEFAULT 0"),
    ("cedula_verification","TEXT DEFAULT 'MISSING'"),
    ("cedula_doc_url","TEXT"),
    ("cedula_doc_hash","TEXT"),
    ("cedula_issuer","TEXT"),
    ("cedula_issue_date","DATE"),
    ("cedula_last_check","DATETIME"),
    ("cedula_reason","TEXT"),
]

print("Comprobando columnas en 'rooms'...")
for name, decl in cols:
    if not column_exists("rooms", name):
        add_col("rooms", name, decl)
    else:
        print(f"   ok: {name} existe")

print("Comprobando tabla 'audit_events'...")
cur.execute("""
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY,
    ts DATETIME,
    actor_role TEXT,
    actor_id TEXT,
    ip TEXT,
    ua TEXT,
    action TEXT,
    room_id INTEGER,
    details TEXT
);
""")
print("   ok: audit_events existe")

conn.commit(); conn.close()
print("Migración completada ✅")
