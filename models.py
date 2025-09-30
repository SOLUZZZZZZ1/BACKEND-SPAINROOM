from sqlalchemy import Column, Integer, String, Date, DateTime, Boolean, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import relationship
from .database import Base

class Zona(Base):
    __tablename__ = "zonas"
    id = Column(Integer, primary_key=True, index=True)
    provincia = Column(String, index=True, nullable=False)
    municipio = Column(String, index=True, nullable=False)
    poblacion = Column(Integer, nullable=False, default=0)
    franquiciados_permitidos = Column(Integer, nullable=False, default=1)
    franquiciados_asignados = Column(Integer, nullable=False, default=0)
    estado = Column(String, nullable=False, default="Libre")  # Libre / Parcial / Ocupado
    observaciones = Column(String, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    asignaciones = relationship("Asignacion", back_populates="zona", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("provincia", "municipio", name="uq_zona_prov_mun"),)

class Franquiciado(Base):
    __tablename__ = "franquiciados"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    telefono = Column(String, nullable=True)
    email = Column(String, nullable=True)
    provincia_base = Column(String, nullable=True)
    municipios_cubiertos = Column(String, nullable=True)  # opcional: lista separada por |
    activo = Column(Boolean, nullable=False, default=True)
    fecha_alta = Column(Date, nullable=True)
    observaciones = Column(String, nullable=True)

    asignaciones = relationship("Asignacion", back_populates="franquiciado", cascade="all, delete-orphan")

class Asignacion(Base):
    __tablename__ = "asignaciones"
    id = Column(Integer, primary_key=True, index=True)
    zona_id = Column(Integer, ForeignKey("zonas.id", ondelete="CASCADE"), nullable=False)
    franquiciado_id = Column(Integer, ForeignKey("franquiciados.id", ondelete="CASCADE"), nullable=False)
    estado = Column(String, nullable=True)  # Ocupado / Parcial / Libre (opcional u observacional)
    fecha_asignacion = Column(Date, nullable=True)
    observaciones = Column(String, nullable=True)

    zona = relationship("Zona", back_populates="asignaciones")
    franquiciado = relationship("Franquiciado", back_populates="asignaciones")

    __table_args__ = (UniqueConstraint("zona_id", "franquiciado_id", name="uq_asig_zona_franq"),)

class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    telefono_cliente = Column(String, nullable=False)
    provincia = Column(String, nullable=False)
    municipio = Column(String, nullable=False)
    nota = Column(String, nullable=True)
    franquiciado_id = Column(Integer, ForeignKey("franquiciados.id", ondelete="SET NULL"), nullable=True)
    estado = Column(String, nullable=False, default="Nuevo")  # Nuevo / Enviado / Contactado / Cerrado
    fecha_creacion = Column(DateTime, server_default=func.now())
    fecha_actualizacion = Column(DateTime, server_default=func.now(), onupdate=func.now())
