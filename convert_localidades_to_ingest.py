# convert_localidades_to_ingest.py
# Uso:
#   python convert_localidades_to_ingest.py "C:\ruta\TU_TXT.txt" "C:\spainroom\backend-api\municipios_from_localidades.csv"
# El TXT tiene líneas ; separadas: Comunidad;Provincia;Localidad;Lat;Lon;Alt;Total;Hombres;Mujeres
# Este script filtra solo Provincia, Localidad y Total (renombrado a poblacion) y limpia filas raras.

import sys, pandas as pd

if len(sys.argv) < 3:
    print(r'Uso: python convert_localidades_to_ingest.py "C:\ruta\TU_TXT.txt" "C:\destino\municipios_from_localidades.csv"')
    sys.exit(1)

SRC = sys.argv[1]
OUT = sys.argv[2]

cols = ["comunidad","provincia","municipio","lat","lon","altitud","poblacion","hombres","mujeres"]

# Leemos como texto separado por ;, ignorando líneas malas
df = pd.read_csv(SRC, sep=";", header=None, names=cols, engine="python", on_bad_lines="skip", dtype=str)

# Limpieza básica
for c in ["provincia","municipio","poblacion"]:
    if c not in df.columns: 
        print("Falta columna:", c); sys.exit(1)
df["provincia"] = df["provincia"].astype(str).str.strip()
df["municipio"] = df["municipio"].astype(str).str.strip()

# Poblacion a numérico
df["poblacion"] = pd.to_numeric(df["poblacion"], errors="coerce").fillna(0).astype(int)

# Quitamos filas vacías o población <= 0
df = df[(df["provincia"]!="") & (df["municipio"]!="") & (df["poblacion"]>0)]

# Deduplicamos por (provincia, municipio): nos quedamos con la mayor población
df = (df.sort_values("poblacion")
        .drop_duplicates(subset=["provincia","municipio"], keep="last"))

# Exportamos con solo las columnas que pide tu backend
df[["provincia","municipio","poblacion"]].to_csv(OUT, index=False, encoding="utf-8")
print("OK ->", OUT, "filas:", len(df))
