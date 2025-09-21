# models_contact.py
from datetime import datetime
from extensions import db

class ContactMessage(db.Model):
    __tablename__ = "contact_messages"
    id         = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    tipo       = db.Column(db.String(64), nullable=False, index=True)   # oportunidades | tenants | ...
    nombre     = db.Column(db.String(200), nullable=False)
    email      = db.Column(db.String(200), nullable=False, default="")
    telefono   = db.Column(db.String(32))                                # opcional
    zona       = db.Column(db.String(200))                               # opcional (sector/área)
    mensaje    = db.Column(db.Text, nullable=False)
    via        = db.Column(db.String(64), default="web_contact_form")
    meta_json  = db.Column(db.JSON)

    def to_dict(self):
        return {
            "id": self.id, "created_at": self.created_at.isoformat(), "tipo": self.tipo,
            "nombre": self.nombre, "email": self.email, "telefono": self.telefono,
            "zona": self.zona, "mensaje": self.mensaje, "via": self.via, "meta_json": self.meta_json
        }
