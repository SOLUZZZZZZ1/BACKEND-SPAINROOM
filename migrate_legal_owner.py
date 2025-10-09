
# migrate_legal_owner.py — crea/actualiza legal_requirements + owner_checks
# - Usa columnas normalizadas generadas (municipality_norm, province_norm)
# - UNIQUE CONSTRAINT sobre (municipality_norm, province_norm) para ON CONFLICT
# - Inserta filas base y permite añadir más fácilmente
import os, sys, json, datetime

DDL = r"""
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE IF NOT EXISTS legal_requirements (
  id SERIAL PRIMARY KEY,
  municipality TEXT NULL,
  province     TEXT NOT NULL,
  cat          TEXT NOT NULL,
  doc          TEXT,
  org          TEXT,
  vig          TEXT,
  notas        TEXT,
  link         TEXT,
  updated_at   TIMESTAMP DEFAULT NOW(),
  -- columnas normalizadas (generadas) para conflicto único
  municipality_norm TEXT GENERATED ALWAYS AS (unaccent(lower(coalesce(municipality,'')))) STORED,
  province_norm     TEXT GENERATED ALWAYS AS (unaccent(lower(province))) STORED,
  CONSTRAINT uq_legal_req_muni_prov UNIQUE (municipality_norm, province_norm)
);

CREATE TABLE IF NOT EXISTS owner_checks (
  id TEXT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT NOW(),
  nombre TEXT, telefono TEXT, email TEXT,
  direccion TEXT, municipio TEXT, provincia TEXT, cp TEXT,
  refcat TEXT,
  requirement_cat TEXT, requirement_doc TEXT, requirement_org TEXT,
  requirement_vig TEXT, requirement_notas TEXT, requirement_link TEXT,
  doc_url TEXT, doc_hash TEXT
);
"""

BASE_ROWS = [
  # province-level (municipality=None)
  {"municipality": None, "province": "Barcelona",       "cat": "si", "doc": "Cèdula d'habitabilitat", "org": "Generalitat/Ajuntament", "vig": "~15 años", "notas": "Requisito habitual para arrendamiento en Catalunya.", "link": "https://habitatge.gencat.cat/ca/ambits/rehabilitacio/certificats/certificat-habitabilitat/"},
  {"municipality": None, "province": "Valencia",        "cat": "si", "doc": "Licencia 2ª ocupación / Declaración responsable", "org": "Ajuntament/GVA", "vig": "5–10 años", "notas": "Suele exigirse para alquiler.", "link": "https://www.gva.es/va/inicio/procedimientos?id_proc=18592"},
  {"municipality": None, "province": "Islas Baleares",  "cat": "si", "doc": "Cédula de habitabilidad", "org": "GOIB/Ajuntament", "vig": "~10 años", "notas": "Requisito general para alquiler.", "link": "https://www.caib.es/sites/habitatge/ca/cedula_habitabilitat/"},
  # example municipality
  {"municipality": "Sant Llorenç Savall", "province": "Barcelona", "cat": "si", "doc": "Cèdula d'habitabilitat", "org": "Generalitat/Ajuntament", "vig": "~15 años", "notas": "Obligatorio para arrendamiento en Catalunya.", "link": "https://habitatge.gencat.cat/ca/ambits/rehabilitacio/certificats/certificat-habitabilitat/"},
]

UPSERT_SQL = r"""
INSERT INTO legal_requirements (municipality, province, cat, doc, org, vig, notas, link, updated_at)
VALUES (%(municipality)s, %(province)s, %(cat)s, %(doc)s, %(org)s, %(vig)s, %(notas)s, %(link)s, NOW())
ON CONFLICT ON CONSTRAINT uq_legal_req_muni_prov DO UPDATE
SET cat=EXCLUDED.cat, doc=EXCLUDED.doc, org=EXCLUDED.org, vig=EXCLUDED.vig,
    notas=EXCLUDED.notas, link=EXCLUDED.link, updated_at=NOW();
"""

def main():
    url = (os.getenv("DATABASE_URL") or "").strip() or (sys.argv[1].strip() if len(sys.argv) > 1 else "")
    if not url:
        print("❌ Define DATABASE_URL o pásame la URL como argumento.")
        sys.exit(1)
    if "sslmode" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"

    try:
        import psycopg2, psycopg2.extras
    except ImportError:
        import subprocess, sys as _sys
        subprocess.check_call([_sys.executable, "-m", "pip", "install", "-q", "psycopg2-binary"])
        import psycopg2, psycopg2.extras

    con = psycopg2.connect(url)
    con.autocommit = True
    cur = con.cursor()
    try:
        cur.execute(DDL)
        print("✅ DDL aplicado (extensión unaccent, tablas y constraint único).")

        # Permite añadir filas extra vía JSON en argumento 2 (opcional)
        extra_rows = []
        if len(sys.argv) > 2 and sys.argv[2] and os.path.isfile(sys.argv[2]):
            with open(sys.argv[2], "r", encoding="utf-8") as fh:
                extra_rows = json.load(fh)
                assert isinstance(extra_rows, list), "El JSON debe ser una lista de objetos"

        rows = BASE_ROWS + extra_rows
        with con.cursor() as c2:
            psycopg2.extras.execute_batch(c2, UPSERT_SQL, rows, page_size=100)
        print(f"✅ UPSERT completado: {len(rows)} filas")

    finally:
        cur.close()
        con.close()

if __name__ == "__main__":
    main()
