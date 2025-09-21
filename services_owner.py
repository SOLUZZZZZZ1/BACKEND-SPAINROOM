# services_owner.py
from app import db
from franquicia.models import FranquiciaOcupacion  # el modelo que tengas para slots/ocupación
from sqlalchemy import func

def route_franchisee(provincia:str, municipio:str) -> str|None:
    """Devuelve un franchisee_id probable por municipio (o None)."""
    if not provincia and not municipio:
        return None
    q = db.session.query(FranquiciaOcupacion).filter(
        func.lower(FranquiciaOcupacion.municipio)==(municipio or "").lower(),
        FranquiciaOcupacion.nivel=="municipio",
        FranquiciaOcupacion.ocupado==True
    ).first()
    if q and q.ocupado_por:
        return q.ocupado_por
    # Fallback: por provincia, primero ocupado
    q2 = db.session.query(FranquiciaOcupacion).filter(
        func.lower(FranquiciaOcupacion.provincia)==(provincia or "").lower(),
        FranquiciaOcupacion.ocupado==True
    ).first()
    return q2.ocupado_por if q2 and q2.ocupado_por else None
