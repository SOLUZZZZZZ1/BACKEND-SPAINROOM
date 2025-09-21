# routes_contracts.py
from flask import Blueprint, request, jsonify
from app import db
from models_contracts import Contract
from models_rooms import Room

bp_contracts = Blueprint("contracts", __name__)

@bp_contracts.post("/api/contracts/create")
def create_contract():
    data = request.get_json(force=True)
    owner_id  = (data.get("owner_id") or "").strip()
    tenant_id = (data.get("tenant_id") or "").strip()
    franchisee_id = (data.get("franchisee_id") or "").strip()
    rooms_in  = data.get("rooms") or []   # [{id, direccion}, ...]

    if not owner_id or not tenant_id or not rooms_in:
        return jsonify(ok=False, error="missing_fields"), 400

    ref = Contract.new_ref()
    c = Contract(ref=ref, owner_id=owner_id, tenant_id=tenant_id, franchisee_id=franchisee_id or None, status="draft", meta_json=data.get("meta_json"))
    db.session.add(c)

    for r in rooms_in:
        rid = r.get("id")
        if rid is None: continue
        room = db.session.get(Room, int(rid))
        if room:
            c.rooms.append(room)

    db.session.commit()
    return jsonify(ok=True, ref=ref, id=c.id)

@bp_contracts.post("/api/contracts/mark_signed")
def mark_signed():
    data = request.get_json(force=True)
    ref = (data.get("ref") or "").strip().upper()
    if not ref: return jsonify(ok=False, error="missing_ref"), 400
    c = Contract.query.filter_by(ref=ref).first()
    if not c: return jsonify(ok=False, error="not_found"), 404
    c.status = "signed"
    db.session.commit()
    return jsonify(ok=True, ref=c.ref, status=c.status)
