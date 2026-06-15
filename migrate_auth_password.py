# migrate_auth_password.py
# SpainRoom — añade password_hash a auth_user de forma segura.
#
# Uso local:
#   python migrate_auth_password.py "postgresql://USER:PASS@HOST:5432/DB?sslmode=require"
#
# Uso en Render Shell:
#   python migrate_auth_password.py
#
# Es seguro ejecutarlo varias veces: usa ADD COLUMN IF NOT EXISTS.

import os
import sys

DDL = """
ALTER TABLE auth_user
ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);
"""

CHECK = """
SELECT column_name
FROM information_schema.columns
WHERE table_name = 'auth_user'
ORDER BY ordinal_position;
"""

def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url

def main():
    url = normalize_url(os.getenv("DATABASE_URL") or "")
    if not url and len(sys.argv) > 1:
        url = normalize_url(sys.argv[1])

    if not url:
        raise SystemExit("ERROR: falta DATABASE_URL o URL como argumento")

    # Intenta psycopg2 primero; si no, psycopg v3.
    try:
        import psycopg2
        conn = psycopg2.connect(url)
        driver = "psycopg2"
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(DDL)
                    cur.execute(CHECK)
                    cols = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
    except ModuleNotFoundError:
        try:
            import psycopg
            conn = psycopg.connect(url)
            driver = "psycopg"
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(DDL)
                        cur.execute(CHECK)
                        cols = [r[0] for r in cur.fetchall()]
            finally:
                conn.close()
        except ModuleNotFoundError as e:
            raise SystemExit(f"ERROR: falta driver PostgreSQL: instala psycopg2-binary o psycopg. Detalle: {e}")

    print(f"OK: migración ejecutada con {driver}")
    print("Columnas auth_user:")
    for c in cols:
        print(" -", c)

    if "password_hash" in cols:
        print("OK FINAL: password_hash existe en auth_user")
    else:
        raise SystemExit("ERROR: password_hash no aparece tras la migración")

if __name__ == "__main__":
    main()
