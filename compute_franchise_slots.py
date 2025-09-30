# compute_franchise_slots.py
import sys, math
import pandas as pd
from pathlib import Path

def norm(s):
    return (str(s or "")).strip()

def is_big_capital(muni, prov):
    m = norm(muni).lower()
    p = norm(prov).lower()
    return (m == "madrid" and p == "madrid") or (m == "barcelona" and p == "barcelona")

def slots_for(pop, muni, prov):
    if pop is None or pop <= 0:
        return 0
    if is_big_capital(muni, prov):
        return max(1, math.ceil(pop / 20000))
    return max(1, math.ceil(pop / 10000))

def main(in_csv):
    df = pd.read_csv(in_csv)
    # Nombre de columnas flexible
    cols = {c.lower(): c for c in df.columns}
    col_prov = cols.get("
