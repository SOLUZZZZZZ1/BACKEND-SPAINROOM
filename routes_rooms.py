
from flask import Blueprint, request, jsonify

bp_rooms = Blueprint("rooms", __name__)

@bp_rooms.get("/api/rooms/published")
def list_published():
    # Devuelve lista vacía si no hay modelo cargado (demo segura)
    try:
        from extensions import db
        from models_rooms import Room
        q = Room.query.filter_by(published=True).order_by(Room.id.desc()).limit(200).all()
        return jsonify([ r.to_dict() for r in q ])
    except Exception:
        return jsonify([])

@bp_rooms.post("/api/rooms/reservations/create")
def create_reservation():
    data = request.get_json(silent=True) or {}
    room_id = data.get("room_id")
    user_id = data.get("user_id")
    nights = data.get("nights")
    start_date = data.get("start_date")
    if not all([room_id, user_id, nights, start_date]):
        return jsonify(ok=False, error="bad_request"), 400
    rid = "R-" + __import__("uuid").uuid4().hex[:8]
    return jsonify(ok=True, reservation_id=rid)
