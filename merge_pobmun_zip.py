# merge_pobmun_zip.py
"""
Une los Excels municipales del INE (pobmun.zip) en un único CSV:
    provincia,municipio,poblacion

Uso (Windows CMD):
  python merge_pobmun_zip.py --src "C:\spainroom\pobmun" --out "C:\spainroom\backend-api\municipios.csv"

Pasos previos para descargar y descomprimir el ZIP oficial del INE:
  curl -L -o C:\spainroom\pobmun.zip https://www.ine.es/pob_xls/pobmun.zip
  powershell -Command "Expand-Archive -Path C:\spainroom\pobmun.zip -DestinationPath C:\spainroom\pobmun -Force"

Notas:
- Detecta columnas por nombre aproximado: municipio ("municipio") y población total ("total", "población", o suma hombres+mujeres).
- Ignora filas sin municipio o con población no numérica.
- Guarda CSV UTF-8 con separador coma.
"""

import argparse, os
import pandas as pd

def guess_col(cols, *keys):
    low = [str(c).strip().lower() for c in cols]
    for key in keys:
        for i, c in enumerate(low):
            if key in c:
                return cols[i]
    return None

def normalize_prov_name(name: str) -> str:
    s = (name or "").strip()
    # Correcciones comunes de nombre de fichero a nombre de provincia
    maps = {
        "coruña": "A Coruña",
        "la coruña": "A Coruña",
        "illes balears": "Illes Balears",
        "palmas, las": "Las Palmas",
        "rioja, la": "La Rioja",
        "valencia/valència": "Valencia",
        "valència/valencia": "Valencia",
    }
    k = s.lower()
    return maps.get(k, s)

def read_any(path):
    # lee primera hoja por defecto
    try:
        return pd.read_excel(path, sheet_name=0)
    except Exception:
        # algunos vienen en csv
        return pd.read_csv(path)

def collect_rows(src_dir: str):
    rows = []
    for root, _, files in os.walk(src_dir):
        for fn in files:
            if fn.lower().endswith((".xlsx",".xls",".csv")):
                fpath = os.path.join(root, fn)
                try:
                    df = read_any(fpath)
                    if df is None or df.empty:
                        continue

                    # detectar columnas
                    mun_col = guess_col(df.columns, "municipio", "localidad")
                    total_col = guess_col(df.columns, "total", "poblac", "habit")
                    h_col = guess_col(df.columns, "hombre")
                    m_col = guess_col(df.columns, "mujer")

                    if not mun_col:
                        continue

                    sub = df[[mun_col]].copy()
                    sub.columns = ["municipio"]

                    if total_col:
                        sub["poblacion"] = pd.to_numeric(df[total_col], errors="coerce").fillna(0).astype(int)
                    elif h_col and m_col:
                        sub["poblacion"] = (
                            pd.to_numeric(df[h_col], errors="coerce").fillna(0).astype(int) +
                            pd.to_numeric(df[m_col], errors="coerce").fillna(0).astype(int)
                        )
                    else:
                        # no hay columna reconocible
                        continue

                    # provincia por nombre de carpeta/fichero
                    prov_guess = os.path.basename(root)
                    if prov_guess.lower() in ("pobmun","ine","data"):
                        prov_guess = os.path.splitext(fn)[0]
                    provincia = normalize_prov_name(prov_guess)

                    sub["provincia"] = provincia
                    sub = sub[["provincia","municipio","poblacion"]]

                    # limpiar
                    sub["municipio"] = sub["municipio"].astype(str).str.strip()
                    sub = sub[sub["municipio"].str.len() > 0]
                    sub = sub[sub["poblacion"] > 0]

                    rows.append(sub)
                except Exception:
                    # continúa si hay un excel raro
                    continue
    if not rows:
        return pd.DataFrame(columns=["provincia","municipio","poblacion"])
    out = pd.concat(rows, ignore_index=True)

    # normalizaciones extra
    out["provincia"] = out["provincia"].astype(str).str.strip()
    out["municipio"] = out["municipio"].astype(str).str.strip()

    # dedup (último gana)
    out = out.drop_duplicates(subset=["provincia","municipio"], keep="last")

    # orden aproximado
    out = out.sort_values(["provincia","municipio"]).reset_index(drop=True)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="Carpeta con los excels del INE descomprimidos (pobmun)")
    ap.add_argument("--out", required=True, help="Ruta de salida CSV")
    args = ap.parse_args()

    df = collect_rows(args.src)
    if df.empty or len(df) < 100:
        print("WARNING: Pocos municipios detectados. Revisa la ruta --src (debe apuntar a la carpeta con excels por provincia).")
    df.to_csv(args.out, index=False, encoding="utf-8")
    print(f"OK -> {args.out} ({len(df)} municipios)")

if __name__ == "__main__":
    main()
