# routes_voice_leads.py — crear y rutear leads + KYC/documents
import os
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func
from extensions import db
from models_lead import VoiceLead

# Usa tu modelo de franquicia existente
try:
    from models_franchise_slots import FranchiseSlot
except Exception:
    FranchiseSlot = None

bp_voice_leads = Blueprint("voice_leads", __name__, url_prefix="/api/voice")

CENTRAL_PHONE = os.getenv("CENTRAL_PHONE", "")
CENTRAL_EMAIL = os.getenv("CENTRAL_EMAIL", "central@spainroom.example")

def _clean(s):
    return (s or "").strip()

def _assign_for_zone(zone: str):
    """Devuelve dict con datos del responsable para la zona (si hay), o central."""
    if not FranchiseSlot or not zone:
        return {"assigned_to": "Central SpainRoom", "assigned_email": CENTRAL_EMAIL, "assigned_phone": CENTRAL_PHONE}

    z = zone.lower()
    # heurística: buscar por substring (provincia o municipio)
    row = (db.session.query(FranchiseSlot)
           .filter((func.lower(FranchiseSlot.provincia).contains(z)) | (func.lower(FranchiseSlot.municipio).contains(z)))
           .order_by(FranchiseSlot.provincia, FranchiseSlot.municipio)
           .first())
    if row and (row.assigned_to or row.status in ("free","partial")):
        return {
            "assigned_to": row.assigned_to or f"Franquicia {row.municipio}, {row.provincia}",
            "assigned_email": getattr(row, "email", None) or CENTRAL_EMAIL,
            "assigned_phone": getattr(row, "phone", None) or CENTRAL_PHONE
        }
    return {"assigned_to": "Central SpainRoom", "assigned_email": CENTRAL_EMAIL, "assigned_phone": CENTRAL_PHONE}

@bp_voice_leads.post("/lead")
def create_lead():
    """Crea lead (voz/web). Body JSON:
    { role, zone, name, phone, email?, call_sid?, from?, to?, source? }
    """
    data = request.get_json(force=True) or {}
    lead = VoiceLead(
        role=_clean(data.get("role")),
        zone=_clean(data.get("zone")),
        name=_clean(data.get("name")),
        phone=_clean(data.get("phone")),
        email=_clean(data.get("email")),
        call_sid=_clean(data.get("call_sid")),
        from_num=_clean(data.get("from")),
        to_num=_clean(data.get("to")),
        source=_clean(data.get("source") or "voice"),
        notes=_clean(data.get("notes")),
    )
    # asignación
    assign = _assign_for_zone(lead.zone)
    lead.assigned_to = assign["assigned_to"]
    lead.assigned_email = assign.get("assigned_email")
    lead.assigned_phone = assign.get("assigned_phone")

    db.session.add(lead)
    db.session.commit()
    current_app.logger.info("VOICE LEAD created id=%s zone=%s assigned=%s", lead.id, lead.zone, lead.assigned_to)
    return jsonify(ok=True, lead=lead.to_dict())

@bp_voice_leads.post("/lead/attach_docs")
def attach_docs():
    """Adjunta S3 keys al lead (doc identidad y factura móvil).
       Body: { lead_id, doc_id_key?, doc_bill_key? }
    """
    data = request.get_json(force=True) or {}
    lead_id = data.get("lead_id")
    lead = db.session.get(VoiceLead, lead_id)
    if not lead:
        return jsonify(ok=False, error="not_found"), 404
    if data.get("doc_id_key"):   lead.doc_id_key = _clean(data["doc_id_key"])
    if data.get("doc_bill_key"): lead.doc_bill_key = _clean(data["doc_bill_key"])
    db.session.commit()
    return jsonify(ok=True, lead=lead.to_dict())

@bp_voice_leads.post("/lead/kyc_state")
def kyc_state():
    """Actualiza estado KYC del lead: { lead_id, kyc_state: pending|verified|declined }"""
    data = request.get_json(force=True) or {}
    lead_id = data.get("lead_id"); state = _clean(data.get("kyc_state"))
    if state not in ("pending","verified","declined"):
        return jsonify(ok=False, error="invalid_state"), 400
    lead = db.session.get(VoiceLead, lead_id)
    if not lead:
        return jsonify(ok=False, error="not_found"), 404
    lead.kyc_state = state
    db.session.commit()
    return jsonify(ok=True, lead=lead.to_dict())
