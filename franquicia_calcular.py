
import pandas as pd
import sys

def calcular_franquiciados(provincia: str, municipio: str, poblacion: int) -> int:
    if str(municipio).strip().lower() in ["madrid", "barcelona"]:
        # 1 por cada 20.000 habitantes
        return max(1, (int(poblacion) + 20000 - 1) // 20000)
    # Resto de municipios
    return max(1, (int(poblacion) + 10000 - 1) // 10000)

def main(in_path: str, out_path: str):
    df = pd.read_csv(in_path)
    if "franquiciados_permitidos" not in df.columns:
        df["franquiciados_permitidos"] = None
    df["franquiciados_permitidos"] = df.apply(
        lambda r: calcular_franquiciados(r.get("provincia",""), r.get("municipio",""), r.get("poblacion",0)),
        axis=1
    )
    # Normalizar columnas opcionales
    for col in ["franquiciados_asignados", "estado", "observaciones"]:
        if col not in df.columns:
            df[col] = "" if col != "franquiciados_asignados" else 0
    df.to_csv(out_path, index=False, encoding="utf-8")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python franquicia_calcular.py <entrada.csv> <salida.csv>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
