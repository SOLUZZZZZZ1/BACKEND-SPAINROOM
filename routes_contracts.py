# routes_contracts.py
from flask import Blueprint, request, jsonify
from extensions import db
from models_contracts import Contract, ContractItem
from models_rooms import Room

bp_contracts = Blueprint("contracts", __name__)

@bp_contracts.post("/api/contracts/create")
def create_contract():
    """
    Crea contrato (draft) y sus líneas con sub_ref SR-XXXXX-01..N
    body: { owner_id, tenant_id, franchisee_id?, rooms:[{id}], splits?{owner,franq}, meta_json? }
    """
    data = request.get_json(force=True)
    owner_id  = (data.get("owner_id") or "").strip()
    tenant_id = (data.get("tenant_id") or "").strip()
    franchisee_id = (data.get("franchisee_id") or "").strip() or None
    rooms_in  = data.get("rooms") or []
    splits_in = data.get("splits") or {}

    if not owner_id or not tenant_id or not rooms_in:
        return jsonify(ok=False, error="missing_fields"), 400

    ref = Contract.new_ref()
    c = Contract(ref=ref, owner_id=owner_id, tenant_id=tenant_id, franchisee_id=franchisee_id,
                 status="draft", meta_json=data.get("meta_json"))
    db.session.add(c); db.session.flush()

    base_owner_split = float(splits_in.get("owner", 0.80))
    base_franq_split = float(splits_in.get("franq", 0.20))

    idx = 0
    for r in rooms_in:
        rid = r.get("id")
        if rid is None:
            continue
        room = db.session.get(Room, int(rid))
        if not room:
            continue
        idx += 1
        sub_ref = ContractItem.make_sub_ref(ref, idx)
        db.session.add(ContractItem(
            contract_id=c.id, sub_ref=sub_ref, room_id=room.id,
            owner_id=owner_id, franchisee_id=franchisee_id,
            status="draft", split_owner=base_owner_split, split_franchisee=base_franq_split
        ))

    db.session.commit()
    return jsonify(ok=True, ref=ref, id=c.id, items=idx)

@bp_contracts.post("/api/contracts/mark_signed")
def mark_signed():
    """ Marca contrato como firmado; habilita subidas por franquiciado. """
    data = request.get_json(force=True)
    ref = (data.get("ref") or "").strip().upper()
    if not ref:
        return jsonify(ok=False, error="missing_ref"), 400
    c = Contract.query.filter_by(ref=ref).first()
    if not c:
        return jsonify(ok=False, error="not_found"), 404
    c.status = "signed"
    for it in c.items:
        if it.status == "draft":
            it.status = "ready"
    db.session.commit()
    return jsonify(ok=True, ref=c.ref, status=c.status)

@bp_contracts.get("/api/contracts/<ref>")
def get_contract(ref):
    c = Contract.query.filter_by(ref=ref.upper()).first()
    if not c:
        return jsonify(ok=False, error="not_found"), 404
    return jsonify(ok=True, contract={
        "ref": c.ref, "status": c.status,
        "owner_id": c.owner_id, "tenant_id": c.tenant_id, "franchisee_id": c.franchisee_id,
        "items": [
            {"sub_ref": it.sub_ref, "room_id": it.room_id, "status": it.status,
             "owner_id": it.owner_id, "franchisee_id": it.franchisee_id,
             "split_owner": it.split_owner, "split_franchisee": it.split_franchisee}
            for it in c.items
        ],
        "meta": c.meta_json
    })
