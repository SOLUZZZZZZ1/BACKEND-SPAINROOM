
# convert_ine_to_municipios.py
"""
Convierte un Excel/CSV del INE a `municipios.csv` con columnas:
    provincia,municipio,poblacion

Uso (Windows CMD):
  python convert_ine_to_municipios.py --in "C:\descargas\INE_municipios.xlsx" --out "C:\spainroom\backend-api\municipios.csv" --sheet "Hoja1"
  python convert_ine_to_municipios.py --in "C:\descargas\INE_municipios.csv"  --out "C:\spainroom\backend-api\municipios.csv"

Detecta columnas por nombre aproximado:
  - provincia  → contiene: "provincia", "prov."
  - municipio  → contiene: "municipio", "muni"
  - poblacion  → contiene: "población", "habit", "pob."

Guarda CSV en UTF-8 (coma). Filtra filas sin población (>0) y limpia espacios.
"""

import argparse
import pandas as pd

def guess_col(cols, *keys):
    low = [str(c).lower() for c in cols]
    for key in keys:
        for i, c in enumerate(low):
            if key in c:
                return cols[i]
    return None

def load_any(path, sheet=None):
    pathl = str(path).lower()
    if pathl.endswith((".xlsx",".xls")):
        return pd.read_excel(path, sheet_name=sheet or 0)
    return pd.read_csv(path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, help="Ruta del Excel/CSV del INE")
    ap.add_argument("--out", dest="out", required=True, help="Ruta de salida municipios.csv")
    ap.add_argument("--sheet", dest="sheet", default=None, help="Nombre o índice de hoja (Excel)")
    args = ap.parse_args()

    df = load_any(args.inp, args.sheet)

    prov_col = guess_col(df.columns, "provincia", "prov.")
    mun_col  = guess_col(df.columns, "municipio", "muni")
    pob_col  = guess_col(df.columns, "poblaci", "habit", "pob.")

    if not (prov_col and mun_col and pob_col):
        raise SystemExit(f"No encuentro columnas. Vistas: {list(df.columns)}")

    out = df[[prov_col, mun_col, pob_col]].copy()
    out.columns = ["provincia","municipio","poblacion"]

    out["provincia"] = out["provincia"].astype(str).str.strip()
    out["municipio"] = out["municipio"].astype(str).str.strip()

    # to int, coerce errors -> 0
    out["poblacion"] = pd.to_numeric(out["poblacion"], errors="coerce").fillna(0).astype(int)

    # filtra >0
    out = out[out["poblacion"] > 0]

    # quita duplicados exactos (último gana)
    out = out.drop_duplicates(subset=["provincia","municipio"], keep="last")

    out.to_csv(args.out, index=False, encoding="utf-8")
    print(f"OK -> {args.out} ({len(out)} filas)")

if __name__ == "__main__":
    main()
