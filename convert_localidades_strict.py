# convert_localidades_strict.py
# Convierte "Todas las localidades..." (TXT/CSV ; separador) a "provincia,municipio,poblacion"
# Regla: la población es SIEMPRE la 3ª columna DESDE EL FINAL (Total Habitantes),
# siendo las dos últimas Hombres y Mujeres.

import sys, csv

if len(sys.argv) < 3:
    print(r'Uso: python convert_localidades_strict.py "C:\ruta\TU_TXT.txt" "C:\destino\municipios_from_localidades.csv"')
    sys.exit(1)

SRC = sys.argv[1]
OUT = sys.argv[2]

def es_num(x):
    try:
        int(float(str(x).replace(",", ".").strip()))
        return True
    except:
        return False

filas_ok = 0
with open(SRC, "r", encoding="utf-8", errors="ignore") as f, \
     open(OUT, "w", encoding="utf-8", newline="") as g:
    w = csv.writer(g)
    w.writerow(["provincia","municipio","poblacion"])
    r = csv.reader((line.replace("\t",";") for line in f), delimiter=";")
    for raw in r:
        row = [c.strip() for c in raw if c is not None]
        # Requisitos mínimos: al menos 9 columnas (Comunidad;Provincia;Localidad;Lat;Lon;Alt;Total;H;M)
        if len(row) < 9:
            continue
        # Las 3 últimas deben ser números (Total, Hombres, Mujeres) → cogemos la 3ª por el final
        ult3 = row[-3:]
        if not (len(ult3)==3 and all(es_num(x) for x in ult3)):
            continue
        provincia = row[1]           # 2ª columna
        municipio = row[2]           # 3ª columna (Localidad)
        poblacion = int(float(ult3[0].replace(",", ".")))  # 3ª por el final = Total
        if provincia and municipio and poblacion > 0:
            w.writerow([provincia, municipio, poblacion])
            filas_ok += 1

print(f"OK -> {OUT} filas: {filas_ok}")
