# migrate_requirements_pg.py — crea tabla legal_requirements y mete filas base provincia
import os, sys
SQL = """
CREATE TABLE IF NOT EXISTS legal_requirements (
  id SERIAL PRIMARY KEY,
  municipality TEXT NULL,
  province     TEXT NOT NULL,
  cat          TEXT NOT NULL,   -- 'si' | 'depende' | 'no'
  doc          TEXT,
  org          TEXT,
  vig          TEXT,
  notas        TEXT,
  link         TEXT,
  updated_at   TIMESTAMP DEFAULT NOW()
);
-- Base provincias “obligatorio”
INSERT INTO legal_requirements (municipality, province, cat, doc, org, vig, notas, link)
SELECT NULL,'Barcelona','si','Cèdula d\\'habitabilitat','Generalitat/Ajuntament','~15 años','Requisito habitual para arrendamiento.','https://habitatge.gencat.cat/ca/ambits/rehabilitacio/certificats/certificat-habitabilitat/'
WHERE NOT EXISTS (SELECT 1 FROM legal_requirements WHERE municipality IS NULL AND lower(province)=lower('Barcelona'));

INSERT INTO legal_requirements (municipality, province, cat, doc, org, vig, notas, link)
SELECT NULL,'Valencia','si','Licencia 2ª ocupación / DR','Ajuntament/GVA','5–10 años','Suele exigirse para alquiler.','https://www.gva.es/va/inicio/procedimientos?id_proc=18592'
WHERE NOT EXISTS (SELECT 1 FROM legal_requirements WHERE municipality IS NULL AND lower(province)=lower('Valencia'));

INSERT INTO legal_requirements (municipality, province, cat, doc, org, vig, notas, link)
SELECT NULL,'Islas Baleares','si','Cédula de habitabilidad','GOIB/Ajuntament','~10 años','Requisito general para alquiler.','https://www.caib.es/sites/habitatge/ca/cedula_habitabilitat/'
WHERE NOT EXISTS (SELECT 1 FROM legal_requirements WHERE municipality IS NULL AND lower(province)=lower('Islas Baleares'));
"""
def main():
    url = os.getenv("DATABASE_URL", "").strip() or (len(sys.argv)>1 and sys.argv[1].strip())
    if not url:
        print("Define DATABASE_URL o pásame la URL como argumento.")
        sys.exit(1)
    if "sslmode" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    try:
        import psycopg2
    except ImportError:
        os.system(f"{sys.executable} -m pip install -q psycopg2-binary")
        import psycopg2
    con = psycopg2.connect(url); con.autocommit=True
    cur = con.cursor()
    try:
        cur.execute(SQL); print("✅ legal_requirements creada/actualizada.")
    finally:
        cur.close(); con.close()
if __name__ == "__main__":
    main()
