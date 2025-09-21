# models_auth.py
from datetime import datetime, timedelta
import random
from extensions import db

def gen_code(n=6): return "".join(str(random.randint(0,9)) for _ in range(n))

class User(db.Model):
    __tablename__ = "auth_user"
    id         = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    phone      = db.Column(db.String(32), unique=True, index=True)
    email      = db.Column(db.String(200), unique=True, index=True)
    role       = db.Column(db.String(32), default="inquilino")  # admin|franquiciado|propietario|inquilino
    name       = db.Column(db.String(200))

    def to_dict(self):
        return {"id":self.id,"phone":self.phone,"email":self.email,"role":self.role,"name":self.name}

class Otp(db.Model):
    __tablename__ = "auth_otp"
    id         = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    target     = db.Column(db.String(200), index=True)     # phone o email
    code       = db.Column(db.String(8), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    tries      = db.Column(db.Integer, default=0)
    used       = db.Column(db.Boolean, default=False)
    meta       = db.Column(db.JSON)

    @staticmethod
    def new(target:str, ttl_sec:int=300):
        code = gen_code(6)
        return Otp(target=target, code=code, expires_at=datetime.utcnow()+timedelta(seconds=ttl_sec))
