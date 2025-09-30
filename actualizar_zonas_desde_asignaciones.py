
import pandas as pd
import sys

def main(zonas_csv, asignaciones_csv, out_csv):
    zonas = pd.read_csv(zonas_csv)
    asig = pd.read_csv(asignaciones_csv) if asignaciones_csv else pd.DataFrame(columns=["provincia","municipio","franquiciado_id"])
    # Normalizar nombres para join robusto
    def norm(s):
        return str(s).strip().lower()
    zonas["_key"] = zonas["provincia"].map(norm) + "||" + zonas["municipio"].map(norm)
    if not asig.empty:
        asig["_key"] = asig["provincia"].map(norm) + "||" + asig["municipio"].map(norm)
        counts = asig.groupby("_key")["franquiciado_id"].nunique().rename("franquiciados_asignados")
    else:
        counts = pd.Series(dtype=int)
    zonas = zonas.merge(counts, on="_key", how="left", suffixes=("","_calc"))
    # Resolver columna final
    zonas["franquiciados_asignados"] = zonas["franquiciados_asignados_calc"].fillna(zonas.get("franquiciados_asignados", 0)).fillna(0).astype(int)
    # Estado automático si está vacío
    def estado_auto(row):
        if pd.notna(row.get("estado")) and str(row["estado"]).strip():
            return row["estado"]
        if row["franquiciados_asignados"] <= 0:
            return "Libre"
        if row["franquiciados_asignados"] >= int(row["franquiciados_permitidos"]):
            return "Ocupado"
        return "Parcial"
    zonas["estado"] = zonas.apply(estado_auto, axis=1)
    zonas.drop(columns=["_key","franquiciados_asignados_calc"], errors="ignore", inplace=True)
    zonas.to_csv(out_csv, index=False, encoding="utf-8")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Uso: python actualizar_zonas_desde_asignaciones.py <zonas.csv> <asignaciones.csv> <salida.csv>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
