# merge_pobmun_zip_v2.py — Une pobmun del INE a municipios.csv (provincia, municipio, poblacion) con heurística robusta
import os, re, sys
import pandas as pd

def is_text_col(s: pd.Series) -> bool:
    # columna "texto" si >60% de celdas no son numéricas
    s2 = s.dropna().astype(str).str.strip()
    if len(s2) == 0: return False
    nonnum = s2.map(lambda v: not re.fullmatch(r"\d+(\.\d+)?", v))
    return (nonnum.mean() >= 0.6)

def best_text_col(df: pd.DataFrame):
    # intenta por nombres frecuentes primero
    cand_names = [c for c in df.columns if re.search(r"municip", str(c), re.I) or re.search(r"localidad|nombre", str(c), re.I)]
    for c in cand_names:
        return c
    # si no hay cabecera buena, busca la primera columna con texto
    for c in df.columns:
        if is_text_col(df[c]):
            return c
    return None

def best_pop_col(df: pd.DataFrame):
    # 1) por nombres típicos
    cand = [c for c in df.columns if re.search(r"total|poblaci|habit", str(c), re.I)]
    if cand:
        try:
            return max(cand, key=lambda c: pd.to_numeric(df[c], errors="coerce").fillna(0).sum())
        except Exception:
            pass
    # 2) la columna NUMÉRICA con suma más alta
    nums = []
    for c in df.columns:
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().mean() > 0.6:
            nums.append((c, s.fillna(0).sum()))
    if nums:
        nums.sort(key=lambda x: x[1], reverse=True)
        return nums[0][0]
    return None

def guess_prov_from_sheet(df: pd.DataFrame, fallback: str) -> str:
    # busca en las 10 primeras filas una celda tipo "Provincia: Granada"
    head = df.head(10).astype(str).applymap(lambda x: x.strip())
    txt = " ".join(head.values.ravel().tolist())
    m = re.search(r"provincia\s*[:\-]\s*([A-Za-zÁÉÍÓÚÜÑà-ÿ' \-/]+)", txt, flags=re.I)
    if m:
        return m.group(1).strip()
    # limpia fallback (ej. "pobmun98" -> "")
    fb = re.sub(r"^pobmun\d+\s*", "", fallback.strip(), flags=re.I)
    return fb if fb else fallback

def read_any(path: str) -> pd.DataFrame | None:
    try:
        if path.lower().endswith((".xlsx", ".xls", ".xlsm", ".xlsb")):
            return pd.read_excel(path, sheet_name=0, header=None)
        else:
            # CSV sin cabecera fiable: lee sin header
            return pd.read_csv(path, header=None, sep=None, engine="python")
    except Exception:
        return None

def collect(src: str) -> pd.DataFrame:
    rows = []
    for root, _, files in os.walk(src):
        for fn in files:
            if not fn.lower().endswith((".xlsx",".xls",".xlsm",".xlsb",".csv")):
                continue
            fpath = os.path.join(root, fn)
            df0 = read_any(fpath)
            if df0 is None or df0.empty:
                continue

            # intenta localizar fila de cabecera real: busca fila que contenga "Municipio" o similar
            header_idx = None
            for i in range(min(len(df0), 20)):
                row_str = " ".join(df0.iloc[i].astype(str).tolist()).lower()
                if ("municip" in row_str) or ("localidad" in row_str) or ("nombre" in row_str):
                    header_idx = i
                    break
            if header_idx is not None:
                df = df0.iloc[header_idx+1:].copy()
                df.columns = df0.iloc[header_idx].astype(str).str.strip().tolist()
            else:
                df = df0.copy()
                df.columns = [f"C{j}" for j in range(1, df.shape[1]+1)]

            muni_col = best_text_col(df)
            pop_col  = best_pop_col(df)
            if not muni_col or not pop_col:
                continue

            # provincia por hoja/contenido o por carpeta/fichero
            prov_guess = os.path.basename(root)
            prov = guess_prov_from_sheet(df0 if header_idx is None else df0.iloc[:header_idx+1], prov_guess)

            sub = df[[muni_col, pop_col]].copy()
            sub.columns = ["municipio","poblacion"]
            sub["provincia"] = prov

            # limpieza
            sub["municipio"] = sub["municipio"].astype(str).str.strip()
            sub["poblacion"] = pd.to_numeric(sub["poblacion"], errors="coerce").fillna(0).astype(int)
            sub = sub[(sub["municipio"]!="") & (sub["poblacion"]>0)]

            rows.append(sub[["provincia","municipio","poblacion"]])

    if not rows:
        return pd.DataFrame(columns=["provincia","municipio","poblacion"])
    out = pd.concat(rows, ignore_index=True)

    # normaliza y dedup
    out["provincia"] = out["provincia"].astype(str).str.strip()
    out["municipio"] = out["municipio"].astype(str).str.strip()
    out = out[(out["provincia"]!="") & (out["municipio"]!="")]
    out = out.drop_duplicates(subset=["provincia","municipio"], keep="last")
    return out.sort_values(["provincia","municipio"]).reset_index(drop=True)

def main():
    if len(sys.argv) < 3 or sys.argv[1] != "--src":
        print('Uso: python merge_pobmun_zip_v2.py --src "C:\\spainroom\\pobmun" --out "C:\\spainroom\\backend-api\\municipios_commas.csv"')
        sys.exit(1)
    src = sys.argv[2]
    out = sys.argv[4] if (len(sys.argv) >= 5 and sys.argv[3] == "--out") else "municipios_commas.csv"

    df = collect(src)
    df.to_csv(out, index=False, encoding="utf-8")
    print(f"OK -> {out} ({len(df)} municipios)")

if __name__ == "__main__":
    main()
