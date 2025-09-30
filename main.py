from fastapi import FastAPI, Depends, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import Optional
import csv, io
from .database import Base, engine, SessionLocal
from . import models, schemas, crud
from .utils import calcular_franquiciados_permitidos

import os
CREATE_ALL = os.getenv('SPAINROOM_CREATE_ALL','false').lower() in {'1','true','yes'}
if CREATE_ALL:
    Base.metadata.create_all(bind=engine)

app = FastAPI(title="SpainRoom Franquicias API", version="1.1.0")

# CORS abierto (ajusta en producción)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/health")
def health():
    return {"status": "ok"}

# Zonas
@app.get("/zonas")
def list_zonas(
    provincia: Optional[str] = None,
    municipio: Optional[str] = None,
    estado: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    total, items = crud.get_zonas(db, provincia, municipio, estado, page, size)
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [schemas.ZonaOut.model_validate(i).model_dump() for i in items]
    }

@app.post("/zonas", response_model=schemas.ZonaOut)
def create_zona(zona: schemas.ZonaCreate, db: Session = Depends(get_db)):
    z = crud.upsert_zona(db, zona.provincia, zona.municipio, zona.poblacion, zona.observaciones)
    db.commit()
    return schemas.ZonaOut.model_validate(z)

@app.post("/zonas/import")
def import_zonas(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Sube un CSV")
    content = file.file.read().decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(content))
    # normalizar cabeceras a minúsculas
    reader.fieldnames = [h.lower() for h in (reader.fieldnames or [])]
    required = {"provincia", "municipio", "poblacion"}
    if not required.issubset(set(reader.fieldnames)):
        raise HTTPException(status_code=400, detail="CSV debe contener: provincia, municipio, poblacion")
    count = 0
    for row in reader:
        provincia = row.get("provincia","").strip()
        municipio = row.get("municipio","").strip()
        poblacion = int(row.get("poblacion", 0) or 0)
        if not provincia or not municipio:
            continue
        crud.upsert_zona(db, provincia, municipio, poblacion)
        count += 1
    db.commit()
    return {"imported": count}

# Franquiciados
@app.post("/franquiciados", response_model=schemas.FranquiciadoOut)
def create_franquiciado(data: schemas.FranquiciadoCreate, db: Session = Depends(get_db)):
    franq = crud.create_franquiciado(db, data)
    db.commit()
    return schemas.FranquiciadoOut.model_validate(franq)

@app.get("/franquiciados", response_model=list[schemas.FranquiciadoOut])
def list_franquiciados(activo: Optional[bool] = None, provincia: Optional[str] = None, db: Session = Depends(get_db)):
    items = crud.list_franquiciados(db, activo, provincia)
    return [schemas.FranquiciadoOut.model_validate(i) for i in items]

@app.post("/franquiciados/import")
def import_franquiciados(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Sube un CSV")
    content = file.file.read().decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(content))
    reader.fieldnames = [h.lower() for h in (reader.fieldnames or [])]
    required = {"nombre"}
    if not required.issubset(set(reader.fieldnames)):
        raise HTTPException(status_code=400, detail="CSV debe contener al menos: nombre")
    count = 0
    for row in reader:
        data = schemas.FranquiciadoCreate(
            nombre=row.get("nombre","").strip(),
            telefono=row.get("telefono"),
            email=row.get("email"),
            provincia_base=row.get("provincia_base"),
            municipios_cubiertos=row.get("municipios_cubiertos"),
            activo=(str(row.get("activo","true")).strip().lower() != "false"),
            observaciones=row.get("observaciones")
        )
        if not data.nombre:
            continue
        crud.create_franquiciado(db, data)
        count += 1
    db.commit()
    return {"imported": count}

# Asignaciones
@app.post("/asignaciones", response_model=schemas.AsignacionOut)
def create_asignacion(data: schemas.AsignacionCreate, db: Session = Depends(get_db)):
    try:
        asig = crud.create_asignacion(db, data)
        db.commit()
        return schemas.AsignacionOut.model_validate(asig)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/asignaciones/{asignacion_id}")
def delete_asignacion(asignacion_id: int, db: Session = Depends(get_db)):
    ok = crud.delete_asignacion(db, asignacion_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")
    db.commit()
    return {"deleted": True}

@app.post("/asignaciones/import")
def import_asignaciones(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Sube un CSV")
    content = file.file.read().decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(content))
    reader.fieldnames = [h.lower() for h in (reader.fieldnames or [])]
    required = {"provincia", "municipio", "franquiciado_id"}
    if not required.issubset(set(reader.fieldnames)):
        raise HTTPException(status_code=400, detail="CSV debe contener: provincia, municipio, franquiciado_id")
    created = 0
    for row in reader:
        provincia = (row.get("provincia") or "").strip()
        municipio = (row.get("municipio") or "").strip()
        if not provincia or not municipio:
            continue
        # asegurar zona (si no existe, crear con población 0 -> permisos 1 por defecto)
        zona = crud.upsert_zona(db, provincia, municipio, int(row.get("poblacion", 0) or 0))
        data = schemas.AsignacionCreate(
            zona_id=zona.id,
            franquiciado_id=int(row.get("franquiciado_id")),
            estado=row.get("estado"),
            fecha_asignacion=row.get("fecha_asignacion"),
            observaciones=row.get("observaciones")
        )
        crud.create_asignacion(db, data)
        created += 1
    db.commit()
    return {"imported": created}

# Leads
@app.post("/leads", response_model=schemas.LeadOut)
def create_lead(data: schemas.LeadCreate, db: Session = Depends(get_db)):
    lead = crud.create_lead(db, data)
    db.commit()
    return schemas.LeadOut.model_validate(lead)

@app.patch("/leads/{lead_id}", response_model=schemas.LeadOut)
def update_lead(lead_id: int, data: schemas.LeadUpdate, db: Session = Depends(get_db)):
    lead = crud.update_lead(db, lead_id, data.estado, data.nota)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead no encontrado")
    db.commit()
    return schemas.LeadOut.model_validate(lead)
