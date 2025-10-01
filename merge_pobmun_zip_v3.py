# merge_pobmun_zip_v3.py — Une pobmun del INE a municipios.csv (provincia, municipio, poblacion) con heurística robusta
import os, re, sys
import pandas as pd

REORDER = {
    r"(?i)^(.+),\s*la$": r"La \1",
    r"(?i)^(.+),\s*las$": r"Las \1",
    r"(?i)^(.+),\s*el$": r"El \1",
    r"(?i)^(.+),\s*los$": r"Los \1",
}
def reorder_spanish(name: str) -> str:
    s = name.strip()
    for pat, rep in REORDER.items():
        m = re.match(pat, s)
        if m:
            return re.sub(pat, rep, s)
    return s

def province_from_path(root: str, fname: str) -> str:
    parent = os.path.basename(root).strip()
    # Si el padre NO es "pobmun" (o variantes), usamos nombre de carpeta
    if not re.fullmatch(r"pobmun\d*|pobmun|data|ine", parent, flags=re.I):
        return reorder_spanish(parent)
    # Si el padre es pobmun, deduce la provincia del nombre del fichero
    base = os.path.splitext(os.path.basename(fname))[0]
    base = re.sub(r"(?i)^(pobmun|pmun|muni|prov|ine)[ _-]*", "", base)
    base = re.sub(r"^\d+[_ -]*", "", base)
    base = base.replace("_", " ").replace("-", " ").strip()
    base = re.sub(r"\s+", " ", base)
    return reorder_spanish(base) if base else parent

def read_any(path: str):
    try:
        if path.lower().endswith((".xlsx",".xls",".xlsm",".xlsb")):
            # header=None para localizar la fila de cabeceras real
            return pd.read_excel(path, sheet_name=0, header=None)
        else:
            return pd.read_csv(path, header=None, sep=None, engine="python")
    except Exception:
        return None

def find_header_index(df: pd.DataFrame) -> int | None:
    up = df.head(40).astype(str).applymap(lambda x: x.strip().lower())
    for i in range(len(up)):
        rowtxt = " ".join(up.iloc[i].tolist())
        if any(k in rowtxt for k in ("municip", "localidad", "nombre")):
            return i
    return None

def pick_cols(df: pd.DataFrame):
    # intenta por nombres
    muni_candidates = [c for c in df.columns if re.search(r"municip|localidad|nombre", str(c), re.I)]
    muni_col = muni_candidates[0] if muni_candidates else None
    pop_candidates = [c for c in df.columns if re.search(r"total|poblaci|habit|pob\.", str(c), re.I)]
    pop_col = None
    if pop_candidates:
        try:
            pop_col = max(pop_candidates, key=lambda c: pd.to_numeric(df[c], errors="coerce").fillna(0).sum())
        except Exception:
            pop_col = pop_candidates[0]
    if not pop_col:
        # fallback: columna numérica con mayor suma
        best_sum, best_c = -1, None
        for c in df.columns:
            s = pd.to_numeric(df[c], errors="coerce")
            if s.notna().mean() > 0.6:
                sm = s.fillna(0).sum()
                if sm > best_sum:
                    best_sum, best_c = sm, c
        pop_col = best_c
    return muni_col, pop_col

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
            hdr = find_header_index(df0)
            if hdr is not None:
                df = df0.iloc[hdr+1:].copy()
                df.columns = df0.iloc[hdr].astype(str).str.strip().tolist()
            else:
                df = df0.copy()
                df.columns = [f"C{j}" for j in range(1, df.shape[1]+1)]
            muni_col, pop_col = pick_cols(df)
            if not muni_col or not pop_col:
                continue

            provincia = province_from_path(root, fpath)
            sub = df[[muni_col, pop_col]].copy()
            sub.columns = ["municipio","poblacion"]
            sub["provincia"] = provincia
            sub["municipio"] = sub["municipio"].astype(str).str.strip()
            sub["poblacion"] = pd.to_numeric(sub["poblacion"], errors="coerce").fillna(0).astype(int)
            sub = sub[(sub["municipio"]!="") & (sub["poblacion"]>0)]
            rows.append(sub[["provincia","municipio","poblacion"]])

    if not rows:
        return pd.DataFrame(columns=["provincia","municipio","poblacion"])

    out = pd.concat(rows, ignore_index=True)
    out["provincia"] = out["provincia"].astype(str).str.strip()
    out["municipio"] = out["municipio"].astype(str).str.strip()
    out = out[(out["provincia"]!="") & (out["municipio"]!="")]
    out = out.drop_duplicates(subset=["provincia","municipio"], keep="last")
    return out.sort_values(["provincia","municipio"]).reset_index(drop=True)

def main():
    if len(sys.argv) < 3 or sys.argv[1] != "--src":
        print('Uso: python merge_pobmun_zip_v3.py --src "C:\\spainroom\\pobmun" --out "C:\\spainroom\\backend-api\\municipios_commas.csv"')
        sys.exit(1)
    src = sys.argv[2]
    out = sys.argv[4] if (len(sys.argv) >= 5 and sys.argv[3] == "--out") else "municipios_commas.csv"
    df = collect(src)
    df.to_csv(out, index=False, encoding="utf-8")
    print(f"OK -> {out} ({len(df)} municipios)")

if __name__ == "__main__":
    main()
