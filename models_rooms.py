# models_rooms.py
from datetime import datetime
from app import db

class Room(db.Model):
    __tablename__ = "rooms"
    id         = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    code       = db.Column(db.String(32), unique=True, index=True)  # ROOM-022
    direccion  = db.Column(db.String(240))
    ciudad     = db.Column(db.String(120))
    provincia  = db.Column(db.String(120))
    m2         = db.Column(db.Integer)
    precio     = db.Column(db.Integer)  # EUR/mes
    estado     = db.Column(db.String(32), default="Libre")
    notas      = db.Column(db.Text)

    published  = db.Column(db.Boolean, default=False, nullable=False)
    images_json= db.Column(db.JSON)  # {gallery:[{url,thumb,w,h,sub_ref}], cover:{...}}

    def to_dict(self, with_images=True):
        d = dict(
            id=self.id, code=self.code, direccion=self.direccion, ciudad=self.ciudad,
            provincia=self.provincia, m2=self.m2, precio=self.precio, estado=self.estado,
            notas=self.notas, published=self.published
        )
        if with_images:
            d["images"] = self.images_json or {}
        return d
