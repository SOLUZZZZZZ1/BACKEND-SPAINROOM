# models_owner.py
from datetime import datetime
from app import db

class OwnerCheck(db.Model):
    __tablename__ = "owner_checks"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    nombre = db.Column(db.String(200), nullable=False)
    telefono = db.Column(db.String(40), nullable=False)
    via = db.Column(db.String(32), nullable=False)  # numero|catastro|direccion
    numero = db.Column(db.String(80))
    refcat = db.Column(db.String(80))
    direccion = db.Column(db.String(240))
    cp = db.Column(db.String(12))
    municipio = db.Column(db.String(120))
    provincia = db.Column(db.String(120))
    status = db.Column(db.String(32))  # valida|caducada|no_encontrada|error
    raw = db.Column(db.JSON)           # payload/respuesta que quieras guardar
    franchisee_id = db.Column(db.String(64))  # routing
