# migrate_cedula_tables.py — crea/actualiza tablas para saber si TIENE cédula en vigor
# - Reutiliza legal_requirements (con claves normalizadas municipality_key/province_key)
# - Crea owner_cedulas (histórico de verificaciones) y vista v_owner_cedulas_last (último estado por refcat)

import os, sys

DDL = r"""
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Requisitos legales (si no existiera ya con sus keys)
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

-- Cédulas verificadas (base de verdad interna)
CREATE TABLE IF NOT EXISTS owner_cedulas (
  id BIGSERIAL PRIMARY KEY,
  refcat TEXT,                         -- referencia catastral (20 chars)
  cedula_numero TEXT,                  -- nº de cédula si se conoce
  estado TEXT CHECK (estado IN ('vigente','caducada','no_consta','pendiente')) NOT NULL DEFAULT 'pendiente',
  expires_at DATE NULL,                -- fecha de caducidad si se conoce
  verified_at TIMESTAMP NULL,          -- cuándo se verificó
  source TEXT,                         -- 'upload' | 'oficial' | 'manual'
  notes TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);

-- Índices útiles
CREATE INDEX IF NOT EXISTS idx_owner_cedulas_refcat ON owner_cedulas(refcat);
CREATE INDEX IF NOT EXISTS idx_owner_cedulas_cedula_numero ON owner_cedulas(cedula_numero);

-- Último estado por referencia catastral
DROP VIEW IF EXISTS v_owner_cedulas_last;
CREATE VIEW v_owner_cedulas_last AS
SELECT DISTINCT ON (refcat)
  refcat, cedula_numero, estado, expires_at, verified_at, source, notes, created_at
FROM owner_cedulas
WHERE refcat IS NOT NULL
ORDER BY refcat, created_at DESC;
"""

def main():
    url = (os.getenv("DATABASE_URL") or "").strip() or (sys.argv[1].strip() if len(sys_
