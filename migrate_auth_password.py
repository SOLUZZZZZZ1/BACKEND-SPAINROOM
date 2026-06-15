# migrate_auth_password.py
# SpainRoom — migración segura para añadir password_hash a auth_user
# Uso:
#   python migrate_auth_password.py
# o:
#   python migrate_auth_password.py "postgresql://..."

import os
import sys

DDL = """
ALTER TABLE auth_user
ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);
"""

def main():
    url = (os.getenv("DATABASE_URL") or "").strip()

    if not url and len(sys.argv) > 1:
        url = sys.argv[1].strip()

    if not url:
        raise SystemExit("ERROR: falta DATABASE_URL")

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    try:
        import psycopg2
    except Exception as e:
        raise SystemExit(f"ERROR: falta psycopg2: {e}")

    conn = psycopg2.connect(url)

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
        print("OK: columna password_hash creada o ya existente en auth_user")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
