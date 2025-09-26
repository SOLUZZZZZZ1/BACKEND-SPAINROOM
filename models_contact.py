# models_contact.py (fixed)
from datetime import datetime
from extensions import db  # FIX: use shared SQLAlchemy instance from extensions

class ContactMessage(db.Model):
    __tablename__ = "contact_messages"
    id         = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # clasificación del contacto (oportunidades / tenants / general…)
    tipo       = db.Column(db.String(64), nullable=False, index=True)

    # datos de quien escribe
    nombre     = db.Column(db.String(200), nullable=False)
    email      = db.Column(db.String(200), nullable=False)
    telefono   = db.Column(db.String(32))            # opcional

    # campos libres
    mensaje    = db.Column(db.Text, nullable=False)
    zona       = db.Column(db.String(200))           # opcional (sector/área)
    via        = db.Column(db.String(64), default="web_contact_form")

    # meta adicional (JSON)
    meta_json  = db.Column(db.JSON)

    def to_dict(self):
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "tipo": self.tipo,
            "nombre": self.nombre,
            "email": self.email,
            "telefono": self.telefono,
            "mensaje": self.mensaje,
            "zona": self.zona,
            "via": self.via,
            "meta_json": self.meta_json,
        }
