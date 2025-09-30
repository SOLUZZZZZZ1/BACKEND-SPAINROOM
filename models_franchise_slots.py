from datetime import datetime
from extensions import db

class FranchiseSlot(db.Model):
    __tablename__ = "franchise_slots"
    id         = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    provincia  = db.Column(db.String(120), index=True, nullable=False)
    municipio  = db.Column(db.String(180), index=True, nullable=False)
    poblacion  = db.Column(db.Integer, nullable=False, default=0)

    plazas     = db.Column(db.Integer, nullable=False, default=1)
    ocupadas   = db.Column(db.Integer, nullable=False, default=0)
    libres     = db.Column(db.Integer, nullable=False, default=1)
    status     = db.Column(db.String(16), default="free", index=True)  # free|partial|full

    assigned_to= db.Column(db.String(120))  # opcional (id/alias de franquiciado)

    __table_args__ = (
        db.UniqueConstraint("provincia", "municipio", name="uq_frslot_prov_mun"),
    )

    def to_dict(self):
        return dict(
            id=self.id,
            provincia=self.provincia,
            municipio=self.municipio,
            poblacion=int(self.poblacion or 0),
            plazas=int(self.plazas or 0),
            ocupadas=int(self.ocupadas or 0),
            libres=int(self.libres or 0),
            status=self.status,
            assigned_to=self.assigned_to,
        )
