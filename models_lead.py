# models_lead.py — lead de contacto y KYC
from datetime import datetime
from extensions import db

class VoiceLead(db.Model):
    __tablename__ = "voice_leads"
    id          = db.Column(db.Integer, primary_key=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # datos de contacto y contexto
    role        = db.Column(db.String(32), index=True)       # propietario | inquilino | franquiciado | otro
    zone        = db.Column(db.String(120), index=True)      # texto libre (provincia/municipio/area)
    name        = db.Column(db.String(160))
    phone       = db.Column(db.String(64), index=True)
    email       = db.Column(db.String(160))

    # origen de la llamada / captura
    call_sid    = db.Column(db.String(64), index=True)
    from_num    = db.Column(db.String(64))
    to_num      = db.Column(db.String(64))
    source      = db.Column(db.String(32), default="voice")  # voice | web | sms | other

    # KYC / documentos (S3 keys)
    kyc_state   = db.Column(db.String(24), default="pending")   # pending|verified|declined
    doc_id_key  = db.Column(db.String(256))                     # S3 key del doc identidad
    doc_bill_key= db.Column(db.String(256))                     # S3 key factura móvil
    notes       = db.Column(db.Text)

    # ruteo a franquicia
    assigned_to = db.Column(db.String(160))                  # nombre/identificador del responsable
    assigned_email = db.Column(db.String(160))
    assigned_phone = db.Column(db.String(64))
    status      = db.Column(db.String(24), default="new")    # new|in_progress|closed

    def to_dict(self):
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "role": self.role,
            "zone": self.zone,
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "call_sid": self.call_sid,
            "from": self.from_num,
            "to": self.to_num,
            "source": self.source,
            "kyc_state": self.kyc_state,
            "doc_id_key": self.doc_id_key,
            "doc_bill_key": self.doc_bill_key,
            "notes": self.notes,
            "assigned_to": self.assigned_to,
            "assigned_email": self.assigned_email,
            "assigned_phone": self.assigned_phone,
            "status": self.status,
        }
