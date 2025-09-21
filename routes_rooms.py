# routes_rooms.py
from flask import Blueprint, request, jsonify
from app import db
from models_rooms import Room
from models_contracts import Contract, ContractItem

bp_rooms = Blueprint("rooms", __name__)

@bp_rooms.get("/api/rooms/<room_id>")
def get_room(room_id):
    room = None
    if str(room_id).isdigit():
        room = db.session.get(Room, int(room_id))
    if room is None:
        room = Room.query.filter_by(code=str(room_id)).first()
    if not room: return jsonify(ok=False, error="not_found"), 404
    return jsonify(room.to_dict())

@bp_rooms.get("/api/rooms/<room_id>/contracts")
def get_room_contracts(room_id):
    room = None
    if str(room_id).isdigit():
        room = db.session.get(Room, int(room_id))
    if room is None:
        room = Room.query.filter_by(code=str(room_id)).first()
    if not room: return jsonify([])
    items = ContractItem.query.filter_by(room_id=room.id).all()
    out = []
    for it in items:
        c = it.contract
        out.append({"ref": c.ref, "sub_ref": it.sub_ref, "status": it.status,
                    "owner_id": it.owner_id, "tenant_id": it.tenant_id, "franchisee_id": it.franchisee_id})
    return jsonify(out)

@bp_rooms.get("/api/rooms/published")
def list_published():
    q = Room.query.filter_by(published=True).order_by(Room.id.desc()).limit(200).all()
    return jsonify([ r.to_dict() for r in q ])

@bp_rooms.post("/api/rooms")
def create_room():
    data = request.get_json(force=True)
    r = Room(
        code=(data.get("code") or "").strip() or None,
        direccion=(data.get("direccion") or "").strip() or None,
        ciudad=(data.get("ciudad") or "").strip() or None,
        provincia=(data.get("provincia") or "").strip() or None,
        m2=data.get("m2"), precio=data.get("precio"),
        estado=(data.get("estado") or "Libre").strip(),
        notas=(data.get("notas") or None)
    )
    db.session.add(r); db.session.commit()
    return jsonify(ok=True, id=r.id, code=r.code)
