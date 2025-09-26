# routes_rooms.py
from flask import Blueprint, request, jsonify
from extensions import db
from models_rooms import Room
from models_contracts import Contract, ContractItem
from models_roomleads import RoomLead  # <-- asegúrate de tener este modelo

bp_rooms = Blueprint("rooms", __name__)

# ----------------- Utilidades internas -----------------
def _find_room(room_id_or_code):
    """Devuelve Room por id (numérico) o por code (ROOM-XXX)."""
    room = None
    if str(room_id_or_code).isdigit():
        room = db.session.get(Room, int(room_id_or_code))
    if room is None:
        room = Room.query.filter_by(code=str(room_id_or_code)).first()
    return room

# ----------------- Endpoints públicos -----------------

@bp_rooms.get("/api/rooms/<room_id>")
def get_room(room_id):
    room = _find_room(room_id)
    if not room:
        return jsonify(ok=False, error="not_found"), 404
    return jsonify(room.to_dict())

@bp_rooms.get("/api/rooms/<room_id>/contracts")
def get_room_contracts(room_id):
    """
    Devuelve las líneas de contrato (sub_ref) que incluyen esta habitación.
    """
    room = _find_room(room_id)
    if not room:
        return jsonify([])
    items = ContractItem.query.filter_by(room_id=room.id).all()
    out = []
    for it in items:
        c = it.contract
        out.append({
            "ref": c.ref,
            "sub_ref": it.sub_ref,
            "status": it.status,
            "owner_id": it.owner_id,
            "tenant_id": it.tenant_id,
            "franchisee_id": it.franchisee_id
        })
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
        m2=data.get("m2"),
        precio=data.get("precio"),
        estado=(data.get("estado") or "Libre").strip(),
        notas=(data.get("notas") or None)
    )
    db.session.add(r); db.session.commit()
    return jsonify(ok=True, id=r.id, code=r.code)

@bp_rooms.post("/api/rooms/<room_id>/attach_ref")
def attach_room_to_contract(room_id):
    """
    Vincula una habitación a un contrato existente por ref (SR-XXXXX).
    body: { "ref": "SR-12345" }
    """
    data = request.get_json(force=True)
    ref  = (data.get("ref") or "").strip().upper()
    if not ref:
        return jsonify(ok=False, error="missing_ref"), 400

    room = _find_room(room_id)
    if not room:
        return jsonify(ok=False, error="room_not_found"), 404

    c = Contract.query.filter_by(ref=ref).first()
    if not c:
        return jsonify(ok=False, error="contract_not_found"), 404

    # evita duplicidad: si ya hay una línea para esa room en ese contrato, no duplique
    it = ContractItem.query.filter_by(contract_id=c.id, room_id=room.id).first()
    if not it:
        # genera sub_ref incremental
        idx = (ContractItem.query.filter_by(contract_id=c.id).count() or 0) + 1
        sub_ref = ContractItem.make_sub_ref(c.ref, idx)
        it = ContractItem(contract_id=c.id, sub_ref=sub_ref, room_id=room.id,
                          owner_id=c.owner_id, franchisee_id=c.franchisee_id, status="draft")
        db.session.add(it); db.session.commit()

    return jsonify(ok=True, ref=c.ref, sub_ref=it.sub_ref, room_id=room.id)

@bp_rooms.get("/api/rooms/search")
def search_rooms():
    """
    Busca por ciudad (q). Si no hay stock:
      - devuelve results = [] y message = "Próximamente en {q}"
      - si vienen phone/email, guarda un lead (RoomLead) para esa ciudad.
    """
    q = (request.args.get("q") or "").strip()
    phone = (request.args.get("phone") or "").strip()
    email = (request.args.get("email") or "").strip()
    notes = (request.args.get("notes") or "").strip()

    results = []
    if q:
        results = (Room.query
                   .filter(db.func.lower(Room.ciudad) == db.func.lower(q),
                           Room.published == True)
                   .order_by(Room.id.desc())
                   .all())

    if results:
        out = [r.to_dict() for r in results]
        return jsonify(ok=True, count=len(out), results=out)

    # sin resultados -> guardar lead opcional
    if q and (phone or email):
        lead = RoomLead(city=q, phone=phone or None, email=email or None, notes=notes or None, source="web_search")
        db.session.add(lead); db.session.commit()

    return jsonify(ok=True, count=0, results=[], message=f"Próximamente en {q}" if q else "Indica ciudad")
