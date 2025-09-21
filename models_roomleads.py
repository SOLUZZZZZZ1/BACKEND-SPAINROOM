from datetime import datetime
from extensions import db
class RoomLead(db.Model):
    __tablename__ = "room_leads"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    city = db.Column(db.String(120), index=True)
    phone = db.Column(db.String(32))
    email = db.Column(db.String(200))
    notes = db.Column(db.Text)
    source = db.Column(db.String(64), default="web_search")
