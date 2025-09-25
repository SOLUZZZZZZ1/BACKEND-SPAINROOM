
# models_remesas.py
from datetime import datetime
from extensions import db

class Remesa(db.Model):
    __tablename__ = "remesas"
    id            = db.Column(db.Integer, primary_key=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Identificación SpainRoom / RIA
    user_id       = db.Column(db.Integer, index=True, nullable=False)     # ID de usuario SpainRoom
    request_id    = db.Column(db.String(64), unique=True, index=True)     # correlación con widget RIA (UUID/base64)
    status        = db.Column(db.String(16), index=True, default="created")  # created|pending|completed|failed

    # Datos de la operación (mínimos, principio de minimización)
    amount        = db.Column(db.Float, nullable=False)
    currency_from = db.Column(db.String(6), default="EUR")
    currency_to   = db.Column(db.String(6), default="EUR")
    country_dest  = db.Column(db.String(2))  # ISO-3166-1 alpha-2 (p.ej. CO, PE, EC, MA, RO)

    # Datos opcionales (receptor, nota, etc.)
    receiver_name = db.Column(db.String(160))
    meta_json     = db.Column(db.JSON)

    __table_args__ = (
        db.Index("ix_remesas_user_status", "user_id", "status"),
    )

    def to_dict(self):
        return dict(
            id=self.id,
            created_at=self.created_at.isoformat(),
            updated_at=self.updated_at.isoformat(),
            user_id=self.user_id,
            request_id=self.request_id,
            status=self.status,
            amount=self.amount,
            currency_from=self.currency_from,
            currency_to=self.currency_to,
            country_dest=self.country_dest,
            receiver_name=self.receiver_name,
            meta_json=self.meta_json,
        )
