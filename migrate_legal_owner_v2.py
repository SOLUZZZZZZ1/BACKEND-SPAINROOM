# migrate_legal_owner_v2.py — corrige "generation expression is not immutable"
# Crea columnas normales municipality_key/province_key + UNIQUE CONSTRAINT
# Inserta/actualiza filas base con UPSERT calculando las keys via unaccent(lower(...))

import os, sys

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
  municipality_key TEXT,
  province_key     TEXT
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='legal_requirements' AND column_name='municipality_key'
  ) THEN
    ALTER TABLE legal_requirements ADD COLUMN municipality_key TEXT;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='legal_requirements' AND column_name='province_key'
  ) THEN
    ALTER TABLE legal_requirements ADD COLUMN province_key TEXT;
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname='uq_legal_req_muni_prov'
  ) THEN
    ALTER TABLE legal_requirements
    ADD CONSTRAINT uq_legal_req_muni_prov UNIQUE (municipality_key, province_key);
  END IF;
END $$;

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

UPSERT_SQL = r"""
INSERT INTO legal_requirements (
  municipality, province, cat, doc, org, vig, notas, link, updated_at,
  municipality_key, province_key
)
VALUES (
  %(municipality)s, %(province)s, %(cat)s, %(doc)s, %(org)s, %(vig)s, %(notas)s, %(link)s, NOW(),
  unaccent(lower(coalesce(%(municipality)s,''))), unaccent(lower(%(province)s))
)
ON CONFLICT ON CONSTRAINT uq_legal_req_muni_prov DO UPDATE
SET cat=EXCLUDED.cat, doc=EXCLUDED.doc, org=EXCLUDED.org, vig=EXCLUDED.vig,
    notas=EXCLUDED.notas, link=EXCLUDED.link, updated_at=NOW();
"""

BASE_ROWS = [
  # Catalunya (provinciales)
  {"municipality": None, "province": "Barcelona",  "cat": "si", "doc": "Cèdula d'habitabilitat", "org": "Generalitat / Ajuntament", "vig": "~15 años", "notas": "Requisito habitual para arrendamiento en Catalunya.", "link": "https://habitatge.gencat.cat/ca/ambits/rehabilitacio/certificats/certificat-habitabilitat/"},
  {"municipality": None, "province": "Girona",     "cat": "si", "doc": "Cèdula d'habitabilitat", "org": "Generalitat / Ajuntament", "vig": "~15 años", "notas": "Requisito habitual para arrendamiento en Catalunya.", "link": "https://habitatge.gencat.cat/"},
  {"municipality": None, "province": "Lleida",     "cat": "si", "doc": "Cèdula d'habitabilitat", "org": "Generalitat / Ajuntament", "vig": "~15 años", "notas": "Requisito habitual para arrendamiento en Catalunya.", "link": "https://habitatge.gencat.cat/"},
  {"municipality": None, "province": "Tarragona",  "cat": "si", "doc": "Cèdula d'habitabilitat", "org": "Generalitat / Ajuntament", "vig": "~15 años", "notas": "Requisito habitual para arrendamiento en Catalunya.", "link": "https://habitatge.gencat.cat/"},
  # Baleares
  {"municipality": None, "province": "Islas Baleares", "cat": "si", "doc": "Cédula de habitabilidad", "org": "GOIB / Ajuntament", "vig": "~10 años", "notas": "Requisito general para alquiler.", "link": "https://www.caib.es/sites/habitatge/ca/cedula_habitabilitat/"},
  # C. Valenciana
  {"municipality": None, "province": "Valencia", "cat": "si", "doc": "Licencia 2ª ocupación / Declaración responsable", "org": "Ajuntament / GVA", "vig": "5–10 años", "notas": "Suele exigirse para alquiler.", "link": "https://www.gva.es/va/inicio/procedimientos?id_proc=18592"},
]

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

  con = psycopg2.connect(url); con.autocommit = True
  with con.cursor() as cur:
    cur.execute(DDL)
    print("✅ DDL OK (extensión, tablas, UNIQUE).")
    psycopg2.extras.execute_batch(cur, UPSERT_SQL, BASE_ROWS, page_size=50)
    print(f"✅ UPSERT base OK: {len(BASE_ROWS)} filas.")
  con.close()

if __name__ == "__main__":
  main()
