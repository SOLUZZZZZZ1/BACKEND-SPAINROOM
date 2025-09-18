
CREATE TABLE IF NOT EXISTS franquicia_slots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provincia TEXT NOT NULL,
  municipio TEXT NOT NULL,
  nivel TEXT NOT NULL,        -- 'municipio' | 'distrito'
  distrito TEXT NOT NULL DEFAULT '',
  poblacion INTEGER NOT NULL DEFAULT 0,
  slots INTEGER NOT NULL DEFAULT 0,
  CONSTRAINT uq_franq_slot UNIQUE (provincia, municipio, nivel, distrito)
);

CREATE TABLE IF NOT EXISTS franquicia_ocupacion (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provincia TEXT NOT NULL,
  municipio TEXT NOT NULL,
  nivel TEXT NOT NULL,
  distrito TEXT NOT NULL DEFAULT '',
  slot_index INTEGER NOT NULL,
  ocupado INTEGER NOT NULL DEFAULT 0,
  ocupado_por TEXT NULL,
  CONSTRAINT uq_franq_occ UNIQUE (provincia, municipio, nivel, distrito, slot_index)
);
