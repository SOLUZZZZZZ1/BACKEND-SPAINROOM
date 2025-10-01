import os, re, sys, pandas as pd

SRC = r"C:\spainroom\ine_zip"  # carpeta con los excels/csv descomprimidos del ZIP
OUT_RAW = r"C:\spainroom\backend-api\municipios_raw.csv"
OUT = r"C:\spainroom\backend-api\municipios_es_clean.csv"

def read_any(path):
    try:
        if path.lower().endswith((".xlsx",".xls",".xlsm",".xlsb")):
            return pd.read_excel(path, sheet_name=0)
        else:
            try:
                return pd.read_csv(path, sep=None, engine="python")
            except Exception:
                return pd.read_csv(path)
    except Exception:
        return None

def pick_cols(df):
    cols = [str(c).strip() for c in df.columns]
    low  = [c.lower() for c in cols]
    def find_exact(keys):
        for k in keys:
            if k in low: return cols[low.index(k)]
        return None
    def find_contains(keys):
        for i, c in enumerate(low):
            if any(k in c for k in keys): return cols[i]
        return None
    p = find_exact(["provincia"]) or find_contains(["prov"])
    m = find_exact(["municipio"]) or find_contains(["municip","localidad","nombre"])
    h =
