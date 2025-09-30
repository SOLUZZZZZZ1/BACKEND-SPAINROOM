# services_owner.py — enrutado zona → franquiciado (España completa, seguro)

from typing import Optional, Dict

# ---------- (1) Mapa nacional por defecto ----------
PROVINCIAS = [
    "A Coruña","Álava","Albacete","Alicante","Almería","Asturias","Ávila",
    "Badajoz","Barcelona","Bizkaia","Burgos","Cáceres","Cádiz","Cantabria",
    "Castellón","Ciudad Real","Córdoba","Cuenca","Gipuzkoa","Girona","Granada",
    "Guadalajara","Huelva","Huesca","Illes Balears","Jaén","La Rioja",
    "Las Palmas","León","Lleida","Lugo","Madrid","Málaga","Murcia",
    "Navarra","Ourense","Palencia","Pontevedra","Salamanca","Santa Cruz de Tenerife",
    "Segovia","Sevilla","Soria","Tarragona","Teruel","Toledo","Valencia",
    "Valladolid","Zamora","Zaragoza","Ceuta","Melilla"
]

# Por defecto todas apuntan a "franq-demo"; personaliza según vayas teniendo IDs reales
ZONE_MAP: Dict[str, str] = { p.lower(): "franq-demo" for p in PROVINCIAS }
ZONE_MAP["granada"]   = "franq-granada"
ZONE_MAP["madrid"]    = "franq-madrid"
ZONE_MAP["barcelona"] = "franq-barcelona"

# Contactos opcionales por franquiciado (para avisos)
FRANQUICIADO_CONTACT: Dict[str, Dict[str, Optional[str]]] = {
    "franq-demo":      {"sms": None,            "email": None},
    "franq-granada":   {"sms": "+34666XXXXXX",  "email": "granada@spainroom.es"},
    "franq-madrid":    {"sms": "+34666YYYYYY",  "email": "madrid@spainroom.es"},
    "franq-barcelona": {"sms": "+34666ZZZZZZ",  "email": "barcelona@spainroom.es"},
}

# ---------- (2) Lookup opcional por DB (si existe tu modelo) ----------
# Intentamos cargar un modelo de ocupación si lo tienes en tu proyecto.
# NUNCA importamos "franquicia" directo (para evitar errores de módulo).
DB_LOOKUP_AVAILABLE = False
FranquiciaOcupacion = None
db = None

try:
    from extensions import db as _db  # tu SQLAlchemy compartido
    db = _db
    # Intenta importar un modelo llamado FranquiciaOcupacion si existe en tu código
    try:
        from models_franchise import FranquiciaOcupacion as _FO  # ajusta si tu modelo vive aquí
        FranquiciaOcupacion = _FO
        DB_LOOKUP_AVAILABLE = True
    except Exception:
        DB_LOOKUP_AVAILABLE = False
except Exception:
    DB_LOOKUP_AVAILABLE = False

def _lookup_by_db(provincia: str, municipio: str) -> Optional[str]:
    """
    Si tienes un modelo 'FranquiciaOcupacion' y extensions.db disponible,
    intenta resolver franquiciado primero por municipio y luego por provincia.
    Estructura esperada del modelo (ajústala si difiere):
      - municipio (str)
      - provincia (str)
      - nivel ('municipio' / 'provincia')
      - ocupado (bool)
      - ocupado_por (str)  -> franquiciado_id
    """
    if not (DB_LOOKUP_AVAILABLE and db and FranquiciaOcupacion):
        return None
    try:
        from sqlalchemy import func
        m = (municipio or "").strip().lower()
        p = (provincia or "").strip().lower()
        # 1) por municipio si hay
        if m:
            q = (db.session.query(FranquiciaOcupacion)
                 .filter(func.lower(FranquiciaOcupacion.municipio) == m)
                 .first())
            if q and getattr(q, "ocupado", False) and getattr(q, "ocupado_por", None):
                return q.ocupado_por
        # 2) por provincia
        if p:
            q2 = (db.session.query(FranquiciaOcupacion)
                  .filter(func.lower(FranquiciaOcupacion.provincia) == p)
                  .first())
            if q2 and getattr(q2, "ocupado", False) and getattr(q2, "ocupado_por", None):
                return q2.ocupado_por
    except Exception:
        # silencioso: no romperemos el flujo si falla el lookup de BD
        return None
    return None

# ---------- (3) API pública que consume routes_sms / resto del backend ----------
def route_franchisee(provincia: str | None, municipio: str | None) -> Optional[str]:
    """
    1) Intenta resolver por DB si existe modelo de ocupación.
    2) Si no, usa el mapa nacional de provincias (ZONE_MAP).
    """
    provincia = (provincia or "").strip().lower()
    municipio = (municipio or "").strip().lower()

    # 1) por DB (si está disponible)
    fid = _lookup_by_db(provincia, municipio)
    if fid:
        return fid

    # 2) por mapa de provincias
    if provincia in ZONE_MAP:
        return ZONE_MAP[provincia]

    return None

def contact_for(franquiciado_id: str) -> Optional[Dict[str, Optional[str]]]:
    """Devuelve los contactos (sms/email) para el franquiciado dado, si están definidos."""
    return FRANQUICIADO_CONTACT.get(franquiciado_id)
