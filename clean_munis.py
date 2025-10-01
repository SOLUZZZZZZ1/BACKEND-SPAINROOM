import pandas as pd

SRC = r"C:\spainroom\backend-api\municipios_commas.csv"
OUT = r"C:\spainroom\backend-api\municipios_es_clean.csv"

df = pd.read_csv(SRC, dtype=str)

# columnas en minúscula
df.columns = [c.strip().lower() for c in df.columns]
required = {"provincia", "municipio", "poblacion"}
if not required.issubset(df.columns):
    raise SystemExit(f"CSV inválido. Columnas vistas: {list(df.columns)}. Debe tener: provincia,municipio,poblacion")

# normaliza
df["provincia"] = df["provincia"].astype(str).str.strip()
df["municipio"] = df["municipio"].astype(str).str.strip()
df["poblacion"] = pd.to_numeric(df["poblacion"], errors="coerce").fillna(0).astype(int)

# 1) fuera provincias falsas/vacías (pobmun...)
df = df[(df["provincia"]!="") & (~df["provincia"].str.lower().str.startswith("pobmun"))]

# 2) fuera municipios que son códigos (0010, 0013A…)
df = df[~df["municipio"].str.fullmatch(r"\d+[A-Za-z]?", na=False)]

# 3) fuera entidades no municipio (distritos/secciones/parroquias/pedanías/aldeas/núcleos/caseríos/parajes…)
bad_tokens = ["distrit","sección","seccion","barrio","entidad","parroquia","pedanía","pedania","aldea","núcleo","nucleo","caserío","caserio","paraje"]
bad_regex = "|".join(bad_tokens)
df = df[~df["municipio"].str.lower().str.contains(bad_regex, na=False)]

# 4) fuera los 21 distritos de Madrid y 10 de Barcelona si vinieran
MAD = {"centro","arganzuela","retiro","salamanca","chamartín","chamartin","tetuán","tetuan","chamberí","chamberi","fuencarral-el pardo","moncloa-aravaca","latina","carabanchel","usera","puente de vallecas","moratalaz","ciudad lineal","hortaleza","villaverde","villa de vallecas","vicálvaro","vicalvaro","san blás-canillejas","san blas-canillejas","barajas"}
BCN = {"ciutat vella","eixample","sants-montjuïc","sants-montjuic","les corts","sarrià-sant gervasi","sarria-sant gervasi","gràcia","gracia","horta-guinardó","horta-guinardo","nou barris","sant andreu","sant martí","sant marti"}
df = df[~((df["provincia"].str.lower()=="madrid") & (df["municipio"].str.lower().isin(MAD)))]
df = df[~((df["provincia"].str.lower()=="barcelona") & (df["municipio"].str.lower().isin(BCN)))]

# 5) dedup por (provincia, municipio) quedándote con la mayor población
df = df.sort_values("poblacion").drop_duplicates(subset=["provincia","municipio"], keep="last")

# 6) filtra poblacion>0 y ordena
df = df[df["poblacion"]>0].sort_values(["provincia","municipio"]).reset_index(drop=True)

df.to_csv(OUT, index=False, encoding="utf-8")
print("OK ->", OUT, "filas:", len(df))
