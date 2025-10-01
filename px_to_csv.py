# px_to_csv_merge.py — Convierte múltiples .px a "provincia,municipio,poblacion"
import os, re, sys, pandas as pd
from pyaxis import pyaxis  # pip install pyaxis

def provincia_desde_ruta(root: str, fname: str) -> str:
    prov = os.path.basename(root).strip()
    if re.fullmatch(r"(?i)(pobmun\\d*|pobmun|px|data|datos|ine)", prov):
        base = os.path.splitext(os.path.basename(fname))[0]
        base = re.sub(r"(?i)^(px|pobmun|pmun|muni|prov|ine)[ _-]*", "", base)
        base = re.sub(r"^\\d+[_ -]*", "", base).replace("_"," ").replace("-"," ").strip()
        prov = base or prov
    for pat, rep in [(r"(?i)^(.+),\\s*la$", r"La \\1"),(r"(?i)^(.+),\\s*las$", r"Las \\1"),
                     (r"(?i)^(.+),\\s*el$", r"El \\1"),(r"(?i)^(.+),\\s*los$", r"Los \\1")]:
        prov = re.sub(pat, rep, prov)
    return prov.strip()

def parse_px(path: str) -> pd.DataFrame:
    px = None
    for enc in ("latin-1","cp1252","utf-8"):
        try:
            px = pyaxis.parse(path, encoding=enc); break
        except Exception: pass
    if px is None:
        raise RuntimeError(f"No pude leer {path}")
    df = px["DATA"]  # categorías + 'value'
    muni_col = next((c for c in df.columns if re.search(r"municip", str(c), re.I)), None)
    if not muni_col:
        raise RuntimeError(f"Sin columna Municipio en {path}")
    anio_col = next((c for c in df.columns if re.search(r"(?i)a[ñn]o|anio|year", str(c))), None)
    if anio_col:
        try:
            df["_anio_num"] = pd.to_numeric(df[anio_col], errors="coerce")
            df = df[df["_anio_num"] == df["_anio_num"].max()]
        except Exception:
            df = df.sort_values(by=[anio_col]).groupby(muni_col, as_index=False).tail(1)
    sub = df[[muni_col, "value"]].copy()
    sub.columns = ["municipio","poblacion"]
    sub["municipio"] = sub["municipio"].astype(str).str.strip()
    sub["poblacion"] = pd.to_numeric(sub["poblacion"], errors="coerce").fillna(0).astype(int)
    sub = sub[(sub["municipio"]!="") & (sub["poblacion"]>0)]
    sub["provincia"] = provincia_desde_ruta(os.path.dirname(path), path)
    return sub[["provincia","municipio","poblacion"]]

def main():
    if len(sys.argv)<3 or sys.argv[1]!="--src":
        print('Uso: python px_to_csv_merge.py --src "C:\\ruta\\carpeta_px" --out "C:\\spainroom\\backend-api\\px_merge.csv"'); sys.exit(1)
    src = sys.argv[2]
    out = sys.argv[4] if len(sys.argv)>=5 and sys.argv[3]=="--out" else r"C:\spainroom\backend-api\municipios_commas.csv"
    frames=[]
    for root,_,files in os.walk(src):
        for fn in files:
            if fn.lower().endswith(".px"):
                path=os.path.join(root,fn)
                try: frames.append(parse_px(path))
                except Exception: pass
    if not frames: 
        raise SystemExit(f"No encontré .px convertibles en {src}")
    raw = pd.concat(frames, ignore_index=True)
    # limpieza básica a municipios reales
    raw = raw[(raw["provincia"]!="") & (~raw["provincia"].str.match(r"(?i)^pobmun(\\d*)?$"))]
    raw = raw[~raw["municipio"].str.fullmatch(r"\\d+[A-Za-z]?", na=False)]
    BAD = r"(?i)(distrit|secci[oó]n|barrio|entidad|parroquia|pedan[ií]a|aldea|n[uú]cleo|caser[ií]o|paraje)"
    raw = raw[~raw["municipio"].str.contains(BAD, na=False)]
    # dedup por mayor población
    raw = raw.sort_values("poblacion").drop_duplicates(subset=["provincia","municipio"], keep="last")
    raw = raw[raw["poblacion"]>0].sort_values(["provincia","municipio"]).reset_index(drop=True)
    raw.to_csv(out, index=False, encoding="utf-8")
    print(f"OK -> {out} filas: {len(raw)}")

if __name__ == "__main__":
    main()
