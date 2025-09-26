# models_leads.py
from datetime import datetime
from extensions import db

class Lead(db.Model):
    __tablename__ = "leads"
    id            = db.Column(db.Integer, primary_key=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    kind          = db.Column(db.String(16), nullable=False)        # owner|tenant|franchise
    source        = db.Column(db.String(32), default="voice")       # voice|web|manual
    provincia     = db.Column(db.String(120), index=True)
    municipio     = db.Column(db.String(180), index=True)
    nombre        = db.Column(db.String(200), nullable=False)
    telefono      = db.Column(db.String(40), nullable=False)
    email         = db.Column(db.String(200))
    assigned_to   = db.Column(db.String(120), index=True)
    status        = db.Column(db.String(16), default="new", index=True)  # new|assigned|done|invalid
    notes         = db.Column(db.Text)
    meta_json     = db.Column(db.JSON)

    def to_dict(self):
        return dict(
            id=self.id,
            created_at=self.created_at.isoformat(),
            updated_at=self.updated_at.isoformat(),
            kind=self.kind,
            source=self.source,
            provincia=self.provincia,
            municipio=self.municipio,
            nombre=self.nombre,
            telefono=self.telefono,
            email=self.email,
            assigned_to=self.assigned_to,
            status=self.status,
            notes=self.notes,
            meta_json=self.meta_json,
        )
