
from flask_sqlalchemy import SQLAlchemy

# Importa la instancia 'db' creada en app.py
try:
    from app import db  # type: ignore
except Exception:
    db = SQLAlchemy()

class FranquiciaSlots(db.Model):
    __tablename__ = "franquicia_slots"
    id = db.Column(db.Integer, primary_key=True)
    provincia = db.Column(db.String(120), nullable=False, index=True)
    municipio = db.Column(db.String(180), nullable=False, index=True)
    nivel = db.Column(db.String(20), nullable=False)  # 'municipio' | 'distrito'
    distrito = db.Column(db.String(180), nullable=False, default="")
    poblacion = db.Column(db.Integer, nullable=False, default=0)
    slots = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint("provincia", "municipio", "nivel", "distrito", name="uq_franq_slot"),
    )

class FranquiciaOcupacion(db.Model):
    __tablename__ = "franquicia_ocupacion"
    id = db.Column(db.Integer, primary_key=True)
    provincia = db.Column(db.String(120), nullable=False, index=True)
    municipio = db.Column(db.String(180), nullable=False, index=True)
    nivel = db.Column(db.String(20), nullable=False)
    distrito = db.Column(db.String(180), nullable=False, default="")
    slot_index = db.Column(db.Integer, nullable=False)
    ocupado = db.Column(db.Integer, nullable=False, default=0)  # 0/1
    ocupado_por = db.Column(db.String(180), nullable=True)

    __table_args__ = (
        db.UniqueConstraint("provincia", "municipio", "nivel", "distrito", "slot_index", name="uq_franq_occ"),
    )
