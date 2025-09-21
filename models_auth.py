# models_auth.py
from datetime import datetime, timedelta
from app import db

class AuthUser(db.Model):
    __tablename__ = "auth_users"
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(32), unique=True, index=True, nullable=False)
    role  = db.Column(db.String(32), default="user", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class AuthOtp(db.Model):
    __tablename__ = "auth_otps"
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(32), index=True, nullable=False)
    code_hash = db.Column(db.String(128), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @staticmethod
    def default_ttl():
        return datetime.utcnow() + timedelta(minutes=5)
