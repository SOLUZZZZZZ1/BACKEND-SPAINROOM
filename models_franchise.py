# models_franchise.py
from datetime import datetime
from extensions import db

class FranchiseApplication(db.Model):
    __tablename__ = "franchise_applications"
    id         = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    nombre     = db.Column(db.String(200), nullable=False)
    telefono   = db.Column(db.String(32))
    email      = db.Column(db.String(200))
    zona       = db.Column(db.String(200))
    mensaje    = db.Column(db.Text)

    status     = db.Column(db.String(32), default="received")  # received|review|approved|rejected
    app_key    = db.Column(db.String(64), unique=True, index=True)  # identificador para vincular docs (subjectId)
    meta_json  = db.Column(db.JSON)

class FranchiseUpload(db.Model):
    __tablename__ = "franchise_uploads"
    id         = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    app_key    = db.Column(db.String(64), index=True, nullable=False)  # FranchiseApplication.app_key
    category   = db.Column(db.String(64), nullable=False)              # dni|plan|titular|otros
    path       = db.Column(db.String(300), nullable=False)             # ruta relativa (instance/uploads/...)
    mime       = db.Column(db.String(80))
    size_bytes = db.Column(db.Integer)
    sha256     = db.Column(db.String(64))
