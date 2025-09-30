from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List

class ZonaBase(BaseModel):
    provincia: str
    municipio: str
    poblacion: int = Field(ge=0)
    observaciones: Optional[str] = None

class ZonaCreate(ZonaBase):
    pass

class ZonaOut(ZonaBase):
    id: int
    franquiciados_permitidos: int
    franquiciados_asignados: int
    estado: str

    class Config:
        from_attributes = True

class FranquiciadoBase(BaseModel):
    nombre: str
    telefono: Optional[str] = None
    email: Optional[EmailStr] = None
    provincia_base: Optional[str] = None
    municipios_cubiertos: Optional[str] = None
    activo: bool = True
    observaciones: Optional[str] = None

class FranquiciadoCreate(FranquiciadoBase):
    pass

class FranquiciadoOut(FranquiciadoBase):
    id: int
    class Config:
        from_attributes = True

class AsignacionCreate(BaseModel):
    zona_id: int
    franquiciado_id: int
    estado: Optional[str] = None
    fecha_asignacion: Optional[str] = None  # YYYY-MM-DD
    observaciones: Optional[str] = None

class AsignacionOut(BaseModel):
    id: int
    zona_id: int
    franquiciado_id: int
    estado: Optional[str] = None
    class Config:
        from_attributes = True

class LeadCreate(BaseModel):
    telefono_cliente: str
    provincia: str
    municipio: str
    nota: Optional[str] = None

class LeadUpdate(BaseModel):
    estado: Optional[str] = None
    nota: Optional[str] = None

class LeadOut(BaseModel):
    id: int
    telefono_cliente: str
    provincia: str
    municipio: str
    nota: Optional[str] = None
    franquiciado_id: Optional[int] = None
    estado: str
    class Config:
        from_attributes = True
