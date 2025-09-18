
import argparse, io, sys, re, json, csv, time
from pathlib import Path
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True, parents=True)

def fetch(url, expect='text'):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.text if expect=='text' else r.content

def save_csv(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")

def normalize_municipios_ine(year: int) -> pd.DataFrame:
    """
    Estrategia:
    1) Descargar relación de municipios y códigos (INE/REL) desde datos.gob.es → CSV o XLSX.
    2) Descargar poblaciones municipales del INE (tablas provinciales). (Plan A: API/datasets abiertos; Plan B: scraping simple).
    3) Unificar y devolver columnas: provincia,cod_prov,municipio,cod_mun,poblacion
    """
    # Plan A: dataset "Relación de municipios y sus códigos por provincias" (datos.gob.es) ofrece CSV consolidado.
    # NOTA: el recurso concreto cambia de UUID; se implementa búsqueda simple en HTML para el primer CSV/XLSX.
    rel_html = fetch("https://datos.gob.es/es/catalogo/ea0010587-relacion-de-municipios-y-sus-codigos-por-provincias")
    csv_links = re.findall(r'href="([^"]+\\.(?:csv|CSV|xls|xlsx))"', rel_html)
    rel_url = None
    for link in csv_links:
        if "codmun" in link or "municip" in link or "relacion" in link.lower():
            rel_url = link
            break
    if not rel_url and csv_links:
        rel_url = csv_links[0]
    if not rel_url:
        raise RuntimeError("No se pudo localizar el CSV/XLSX con la relación de municipios en datos.gob.es")

    # Descargar el fichero de relación
    bin_data = fetch(rel_url, expect='bin')
    if rel_url.lower().endswith(('.xls', '.xlsx')):
        df_rel = pd.read_excel(io.BytesIO(bin_data))
    else:
        df_rel = pd.read_csv(io.BytesIO(bin_data), sep=';', encoding='latin1')
    # Normalizar nombres esperados
    cols = {c.lower(): c for c in df_rel.columns}
    # Intentamos mapear heurísticamente
    prov_col = next((c for c in df_rel.columns if "prov" in c.lower() and "cod" not in c.lower()), None)
    cpro_col = next((c for c in df_rel.columns if re.fullmatch(r".*cod.*prov.*", c.lower())), None)
    mun_col  = next((c for c in df_rel.columns if "muni" in c.lower() and "cod" not in c.lower()), None)
    cmun_col = next((c for c in df_rel.columns if re.fullmatch(r".*cod.*muni.*", c.lower())), None)

    if not all([prov_col, cpro_col, mun_col, cmun_col]):
        # Fallback genérico con nombres comunes
        candidates = {"provincia":"provincia","CPRO":"CPRO","municipio":"municipio","CMUN":"CMUN"}
        for k,v in candidates.items():
            if k in cols:
                candidates[k] = cols[k]
        prov_col = candidates.get("provincia", prov_col)
        cpro_col = candidates.get("CPRO", cpro_col)
        mun_col  = candidates.get("municipio", mun_col)
        cmun_col = candidates.get("CMUN", cmun_col)

    df_rel = df_rel[[prov_col, cpro_col, mun_col, cmun_col]].copy()
    df_rel.columns = ["provincia","cod_prov","municipio","cod_mun"]
    # Normaliza códigos a string
    df_rel["cod_prov"] = df_rel["cod_prov"].astype(str).str.zfill(2)
    df_rel["cod_mun"]  = df_rel["cod_mun"].astype(str).str.zfill(3)

    # Plan municipal población:
    # El INE publica por provincia; aquí planteamos una vía genérica con tabla dinámica (puede requerir ajustes).
    # Para simplificar: intentamos un endpoint genérico (datos.gob.es aglutina recursos por provincia).
    pops = []
    for prov in sorted(df_rel["provincia"].unique()):
        # Buscar recurso por provincia (heurística)
        q = f"https://www.ine.es/dynt3/inebase/index.htm?padre=525"  # índice general provincias
        # En la práctica, se recomienda descargar un CSV consolidado si está disponible en el año actual.
        # Como fallback, establecemos poblacion = NA; el operador puede completar con un script específico de la provincia.
        # (Dejamos hook para completar sin romper el flujo.)
        pass

    # Para no bloquear el flujo, devolvemos DF sin población y dejamos poblacion a -1 para señalizar "pendiente"
    df_rel["poblacion"] = -1
    return df_rel

def normalize_distritos_madrid(year:int) -> pd.DataFrame:
    # Madrid publica xlsx/ods con población por distrito; aquí una heurística al último XLS "Indicadores demográficos"
    # El operador puede fijar URL directa en caso de cambio.
    try:
        xls_bin = fetch("https://www.madrid.es/UnidadesDescentralizadas/UDCEstadistica/NuevoPortal/Estadistica/Distritos/01.Centro/IndicadoresDemograficos.xlsx", expect='bin')
        df = pd.read_excel(io.BytesIO(xls_bin), header=None)
        # Esta parte varía por fichero; como fallback, creamos estructura vacía
        raise Exception("La estructura varía por distrito; se recomienda usar el dataset agregado por ciudad.")
    except Exception:
        return pd.DataFrame(columns=["provincia","ciudad","distrito","cod_distrito","poblacion"])

def normalize_distritos_barcelona(year:int) -> pd.DataFrame:
    # Dataset pad_mdbas: población a 1 de enero por distrito
    url_csv = "https://opendata-ajuntament.barcelona.cat/data/en/dataset/2f6e0561-30f4-44a0-8446-e27442d4754c/resource/fc597601-a291-4811-ad02-c58e32784692/download/2024_pad_mdbas.csv"
    df = pd.read_csv(url_csv)
    # Normaliza
    # El dataset contiene variables: any, codi_districte, nom_districte, poblacio, etc.
    # Ajustamos nombres robustos
    cols = {c.lower(): c for c in df.columns}
    cd = next((c for c in df.columns if "codi_distr" in c.lower()), None)
    nd = next((c for c in df.columns if "nom_distr" in c.lower()), None)
    pop = next((c for c in df.columns if "pobl" in c.lower()), None)
    df = df[[cd, nd, pop]].copy()
    df.columns = ["cod_distrito","distrito","poblacion"]
    df.insert(0, "provincia", "Barcelona")
    df.insert(1, "ciudad", "Barcelona")
    return df

def normalize_distritos_sevilla(year:int) -> pd.DataFrame:
    # Sevilla ofrece tablas en Anuario; esta función deja un stub para consolidación manual si cambia el formato.
    return pd.DataFrame(columns=["provincia","ciudad","distrito","cod_distrito","poblacion"])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    args = ap.parse_args()

    df_mun = normalize_municipios_ine(args.year)
    save_csv(df_mun, OUT/"municipios_es.csv")

    # Distritos
    df_bcn = normalize_distritos_barcelona(args.year)
    save_csv(df_bcn, OUT/"distritos_barcelona.csv")

    df_mad = normalize_distritos_madrid(args.year)
    save_csv(df_mad, OUT/"distritos_madrid.csv")

    df_sev = normalize_distritos_sevilla(args.year)
    save_csv(df_sev, OUT/"distritos_sevilla.csv")

    # Merge distritos (los que estén)
    df_d = pd.concat([df_bcn, df_mad, df_sev], ignore_index=True)
    if not df_d.empty:
        save_csv(df_d, OUT/"distritos_es.csv")

if __name__ == "__main__":
    main()
