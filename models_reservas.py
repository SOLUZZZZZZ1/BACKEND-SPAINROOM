# models_reservas.py
from datetime import datetime, date
from extensions import db

class Reserva(db.Model):
    __tablename__ = "reservas"
    id          = db.Column(db.Integer, primary_key=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # vínculo
    room_id     = db.Column(db.Integer, nullable=False, index=True)

    # solicitante (simplificado)
    nombre      = db.Column(db.String(200), nullable=False)
    email       = db.Column(db.String(200))
    telefono    = db.Column(db.String(40))

    # fechas
    start_date  = db.Column(db.Date, nullable=False, index=True)
    end_date    = db.Column(db.Date, nullable=False, index=True)

    # estado
    status      = db.Column(db.String(16), default="pending", index=True)  # pending|approved|cancelled

    # notas / metadata
    notas       = db.Column(db.Text)
    meta_json   = db.Column(db.JSON)

    __table_args__ = (
        db.CheckConstraint("end_date >= start_date", name="ck_reserva_dates"),
    )

    def to_dict(self):
        return dict(
            id=self.id,
            created_at=self.created_at.isoformat(),
            room_id=self.room_id,
            nombre=self.nombre,
            email=self.email,
            telefono=self.telefono,
            start_date=self.start_date.isoformat(),
            end_date=self.end_date.isoformat(),
            status=self.status,
            notas=self.notas,
            meta_json=self.meta_json,
        )

def overlaps(room_id:int, start:date, end:date) -> bool:
    """
    Devuelve True si hay *alguna* reserva activa que se solapa con [start, end].
    Considera estados 'pending' y 'approved' como bloqueantes.
    """
    q = db.session.execute(
        db.select(Reserva).where(
            Reserva.room_id == room_id,
            Reserva.status.in_(("pending", "approved")),
            db.and_(start <= Reserva.end_date, end >= Reserva.start_date)  # solapamiento
        ).limit(1)
    )
    return q.first() is not None
