# models_kyc.py
from datetime import datetime, timedelta
from extensions import db

class KycSession(db.Model):
    __tablename__ = "kyc_sessions"
    id            = db.Column(db.Integer, primary_key=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at    = db.Column(db.DateTime, nullable=False)

    phone         = db.Column(db.String(32), index=True, nullable=False)
    token         = db.Column(db.String(64), unique=True, index=True, nullable=False)  # token nuestro

    provider      = db.Column(db.String(32), default="veriff", nullable=False)
    provider_id   = db.Column(db.String(64), index=True)          # veriff session id
    verification_url = db.Column(db.String(400))                  # link de Veriff

    state         = db.Column(db.String(24), default="pending", nullable=False)  # pending|received|verified|declined|expired
    decision      = db.Column(db.String(24))                      # approved/declined (de Veriff)
    reason        = db.Column(db.String(120))                     # motivo si declined

    selfie_path   = db.Column(db.String(300))                     # opcional: si quieres guardar snapshot propio
    meta_json     = db.Column(db.JSON)
