# models_uploads.py
from datetime import datetime
from app import db

class Upload(db.Model):
    __tablename__ = "uploads"
    id          = db.Column(db.Integer, primary_key=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    role        = db.Column(db.String(16), nullable=False)   # tenant|owner|franq|contract|room
    subject_id  = db.Column(db.String(128), index=True)      # ref contrato | room.code | user id
    category    = db.Column(db.String(64), nullable=False)   # room_photo|room_sheet|...
    path        = db.Column(db.String(300), nullable=False)  # ruta relativa
    mime        = db.Column(db.String(80))
    size_bytes  = db.Column(db.Integer)
    width       = db.Column(db.Integer)
    height      = db.Column(db.Integer)
    sha256      = db.Column(db.String(64))
