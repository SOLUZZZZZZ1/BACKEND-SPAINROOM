from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_
from typing import List, Optional
from . import models, schemas
from .utils import calcular_franquiciados_permitidos, estado_zona, normaliza

# Zonas
def upsert_zona(db: Session, provincia: str, municipio: str, poblacion: int, observaciones: str | None = None):
    provincia_n = normaliza(provincia)
    municipio_n = normaliza(municipio)
    zona = db.execute(
        select(models.Zona).where(
            func.lower(models.Zona.provincia) == provincia_n,
            func.lower(models.Zona.municipio) == municipio_n
        )
    ).scalar_one_or_none()
    permitidos = calcular_franquiciados_permitidos(provincia, municipio, poblacion)
    if zona is None:
        zona = models.Zona(
            provincia=provincia.strip(),
            municipio=municipio.strip(),
            poblacion=poblacion,
            franquiciados_permitidos=permitidos,
            franquiciados_asignados=0,
            estado="Libre",
            observaciones=observaciones
        )
        db.add(zona)
    else:
        zona.poblacion = poblacion
        zona.franquiciados_permitidos = permitidos
        zona.estado = estado_zona(permitidos, zona.franquiciados_asignados)
        if observaciones is not None:
            zona.observaciones = observaciones
    db.flush()
    return zona

def get_zonas(db: Session, provincia: Optional[str], municipio: Optional[str], estado: Optional[str], page: int, size: int):
    q = select(models.Zona)
    if provincia:
        q = q.where(func.lower(models.Zona.provincia).like(f"%{normaliza(provincia)}%"))
    if municipio:
        q = q.where(func.lower(models.Zona.municipio).like(f"%{normaliza(municipio)}%"))
    if estado:
        q = q.where(models.Zona.estado == estado)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar() or 0
    q = q.order_by(models.Zona.provincia, models.Zona.municipio).offset((page-1)*size).limit(size)
    items = db.execute(q).scalars().all()
    return total, items

def recalc_zona_stats(db: Session, zona: models.Zona):
    asignados = db.execute(
        select(func.count(models.Asignacion.id)).where(models.Asignacion.zona_id == zona.id)
    ).scalar() or 0
    zona.franquiciados_asignados = asignados
    zona.estado = estado_zona(zona.franquiciados_permitidos, asignados)
    db.flush()
    return zona

# Franquiciados
def create_franquiciado(db: Session, data: schemas.FranquiciadoCreate):
    franq = models.Franquiciado(**data.model_dump())
    db.add(franq)
    db.flush()
    return franq

def list_franquiciados(db: Session, activo: Optional[bool]=None, provincia: Optional[str]=None):
    q = select(models.Franquiciado)
    if activo is not None:
        q = q.where(models.Franquiciado.activo == activo)
    if provincia:
        q = q.where(func.lower(models.Franquiciado.provincia_base).like(f"%{normaliza(provincia)}%"))
    return db.execute(q.order_by(models.Franquiciado.nombre)).scalars().all()

# Asignaciones
def create_asignacion(db: Session, data: schemas.AsignacionCreate):
    zona = db.get(models.Zona, data.zona_id)
    franq = db.get(models.Franquiciado, data.franquiciado_id)
    if not zona or not franq:
        raise ValueError("Zona o franquiciado no existe")
    asign = models.Asignacion(
        zona_id=zona.id,
        franquiciado_id=franq.id,
        estado=data.estado,
        fecha_asignacion=data.fecha_asignacion,
        observaciones=data.observaciones
    )
    db.add(asign)
    db.flush()
    recalc_zona_stats(db, zona)
    return asign

def delete_asignacion(db: Session, asignacion_id: int):
    asign = db.get(models.Asignacion, asignacion_id)
    if not asign:
        return False
    zona = asign.zona
    db.delete(asign)
    db.flush()
    recalc_zona_stats(db, zona)
    return True

# Leads y ruteo
def route_lead_to_franquiciado(db: Session, provincia: str, municipio: str) -> Optional[int]:
    # encontrar zona
    z = db.execute(
        select(models.Zona).where(
            func.lower(models.Zona.provincia)==normaliza(provincia),
            func.lower(models.Zona.municipio)==normaliza(municipio)
        )
    ).scalar_one_or_none()
    if not z:
        return None
    # franquiciados asignados a la zona
    asigs = db.execute(
        select(models.Asignacion).where(models.Asignacion.zona_id==z.id)
    ).scalars().all()
    if not asigs:
        return None
    # round-robin pobre: elegir el de menos leads en esa provincia+municipio
    least = None
    least_count = None
    for a in asigs:
        cnt = db.execute(
            select(func.count(models.Lead.id)).where(
                models.Lead.franquiciado_id==a.franquiciado_id,
                func.lower(models.Lead.provincia)==normaliza(provincia),
                func.lower(models.Lead.municipio)==normaliza(municipio)
            )
        ).scalar() or 0
        if least is None or cnt < least_count or (cnt == least_count and a.franquiciado_id < least):
            least = a.franquiciado_id
            least_count = cnt
    return least

def create_lead(db: Session, data: schemas.LeadCreate):
    franq_id = route_lead_to_franquiciado(db, data.provincia, data.municipio)
    lead = models.Lead(
        telefono_cliente=data.telefono_cliente,
        provincia=data.provincia.strip(),
        municipio=data.municipio.strip(),
        nota=data.nota,
        franquiciado_id=franq_id,
        estado="Enviado" if franq_id else "Nuevo"
    )
    db.add(lead)
    db.flush()
    return lead

def update_lead(db: Session, lead_id: int, estado: str | None, nota: str | None):
    lead = db.get(models.Lead, lead_id)
    if not lead:
        return None
    if estado:
        lead.estado = estado
    if nota is not None:
        lead.nota = nota
    db.flush()
    return lead
