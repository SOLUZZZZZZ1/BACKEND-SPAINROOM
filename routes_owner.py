# routes_owner.py
from flask import Blueprint, request, jsonify
from app import db
from models_owner import OwnerCheck
from services_owner import route_franchisee

bp_owner = Blueprint("owner", __name__)

def require_admin_key():
    key = request.headers.get("X-Admin-Key","")
    return key == "ramon"

@bp_owner.post("/api/owner/check")
def api_owner_check():
    if not require_admin_key():
        return jsonify(ok=False, error="forbidden"), 403

    data = request.get_json(force=True, silent=True) or {}
    nombre   = (data.get("nombre") or "").strip()
    telefono = (data.get("telefono") or "").strip()
    via      = (data.get("via") or "").strip()          # numero|catastro|direccion
    status   = (data.get("status") or "").strip()       # valida|caducada|no_encontrada|error
    provincia= (data.get("provincia") or "").strip()
    municipio= (data.get("municipio") or "").strip()

    if not nombre or not telefono:
        return jsonify(ok=False, error="missing_contact"), 400

    # routing por zona:
    franchisee_id = route_franchisee(provincia, municipio)

    oc = OwnerCheck(
        nombre=nombre, telefono=telefono, via=via, status=status,
        numero=data.get("numero"), refcat=data.get("refcat"),
        direccion=data.get("direccion"), cp=data.get("cp"),
        municipio=municipio, provincia=provincia,
        raw=data, franchisee_id=franchisee_id
    )
    db.session.add(oc); db.session.commit()

    # Notificación (placeholder): aquí puedes enviar email/webhook a Admin y al franquiciado
    # send_email_admin(oc) / send_email_franchisee(franchisee_id, oc) etc.

    return jsonify(ok=True, id=oc.id, franchisee_id=franchisee_id)
