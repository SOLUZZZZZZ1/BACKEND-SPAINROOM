"""
convert_ine_to_municipios.py
Convierte un Excel/CSV del INE a `municipios.csv` con columnas:
    provincia,municipio,poblacion
"""
import argparse
import pandas as pd

def _guess_col(cols, *keys):
    low = [str(c).strip().lower() for c in cols]
    for key in keys:
        for i, c in enumerate(low):
            if key in c:
                return cols[i]
    return None

def _load_any(path, sheet=None):
    pathl = str(path).lower()
    if pathl.endswith((".xlsx",".xls")):
        return pd.read_excel(path, sheet_name=sheet or 0)
    return pd.read_csv(path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", required=True)
    ap.add_argument("--sheet", dest="sheet", default=None)
    args = ap.parse_args()

    df = _load_any(args.inp, args.sheet)

    prov_col = _guess_col(df.columns, "provincia", "prov.")
    mun_col  = _guess_col(df.columns, "municipio", "muni")
    pob_col  = _guess_col(df.columns, "poblaci", "habit", "pob.", "total")

    if not (prov_col and mun_col and pob_col):
        raise SystemExit(f"No encuentro columnas esperadas. Columnas vistas: {list(df.columns)}")

    out = df[[prov_col, mun_col, pob_col]].copy()
    out.columns = ["provincia","municipio","poblacion"]

    out["provincia"] = out["provincia"].astype(str).str.strip()
    out["municipio"] = out["municipio"].astype(str).str.strip()
    out["poblacion"] = pd.to_numeric(out["poblacion"], errors="coerce").fillna(0).astype(int)

    out = out[out["poblacion"] > 0]
    out = out.drop_duplicates(subset=["provincia","municipio"], keep="last")
    out.to_csv(args.out, index=False, encoding="utf-8")
    print(f"OK -> {args.out} ({len(out)} filas)")

if __name__ == "__main__":
    main()
