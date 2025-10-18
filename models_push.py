# models_push.py
from datetime import datetime
from extensions import db

class PushToken(db.Model):
    __tablename__ = "push_tokens"
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.String(120), index=True, nullable=False)
    token     = db.Column(db.String(300), unique=True, nullable=False)
    platform  = db.Column(db.String(20), default="web")
    created_at= db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
