def normaliza(s: str) -> str:
    return (s or "").strip().lower()

def calcular_franquiciados_permitidos(provincia: str, municipio: str, poblacion: int) -> int:
    m = normaliza(municipio)
    if m in {"madrid", "barcelona"}:
        div = 20000
    else:
        div = 10000
    if poblacion <= 0:
        return 1
    return max(1, (int(poblacion) + div - 1) // div)

def estado_zona(permisos: int, asignados: int) -> str:
    if asignados <= 0:
        return "Libre"
    if asignados >= permisos:
        return "Ocupado"
    return "Parcial"
