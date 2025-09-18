
import os
from flask import Blueprint, jsonify, request
from .services import (
    summary_totals, query_slots, get_group_occupancy,
    rebuild_from_csv, ocupar_slot, liberar_slot
)

bp_franquicia = Blueprint("franquicia", __name__)

def _admin_only():
    api_key = os.getenv("ADMIN_API_KEY", "")
    if api_key and request.headers.get("X-Admin-Key") != api_key:
        return False
    return True

@bp_franquicia.before_request
def _guard():
    if os.getenv("BACKEND_FEATURE_FRANQ_PLAZAS", "off").lower() != "on":
        return jsonify(error="feature_off"), 404
    if not _admin_only():
        return jsonify(error="forbidden"), 403

@bp_franquicia.get("/summary")
def get_summary():
    return jsonify(summary_totals())

@bp_franquicia.get("/slots")
def list_slots():
    provincia = request.args.get("provincia") or None
    estado = (request.args.get("estado") or "todas").lower()
    q = request.args.get("q") or None
    return jsonify(query_slots(provincia=provincia, estado=estado, q=q))

@bp_franquicia.get("/slots/<int:slot_group_id>/ocupacion")
def slot_group_occupancy(slot_group_id: int):
    return jsonify(get_group_occupancy(slot_group_id))

@bp_franquicia.post("/slots/ocupar")
def ocupar():
    data = request.get_json(force=True) or {}
    r = ocupar_slot(
        provincia=data.get("provincia",""),
        municipio=data.get("municipio",""),
        nivel=data.get("nivel","municipio"),
        distrito=data.get("distrito","") or "",
        slot_index=int(data.get("slot_index", 0)),
        ocupado_por=data.get("ocupado_por","") or "admin",
    )
    code = 200 if r.get("ok") else 400
    return jsonify(r), code

@bp_franquicia.post("/slots/liberar")
def liberar():
    data = request.get_json(force=True) or {}
    r = liberar_slot(
        provincia=data.get("provincia",""),
        municipio=data.get("municipio",""),
        nivel=data.get("nivel","municipio"),
        distrito=data.get("distrito","") or "",
        slot_index=int(data.get("slot_index", 0)),
    )
    code = 200 if r.get("ok") else 400
    return jsonify(r), code

@bp_franquicia.post("/etl/rebuild")
def etl_rebuild():
    preserve = (request.args.get("preserve","true").lower() != "false")
    try:
        r = rebuild_from_csv(preserve_occupations=preserve)
        return jsonify(r)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400
