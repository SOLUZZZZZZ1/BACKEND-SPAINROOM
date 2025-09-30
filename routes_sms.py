# routes_sms.py — Webhook de SMS (Twilio) + asignación Granada + notificación (FIX sin dependencia de "services" con import relativo)
import os, re
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import exc as sa_exc
from extensions import db

# Modelos
try:
    from models_leads import Lead
except Exception:
    Lead = None

# Enrutado a franquiciado y contactos
from services_owner import route_franchisee, contact_for

# Notificador webhook (FIX): evitamos "from .services import ..."
def notify_lead_webhook(payload: dict) -> bool:
    url = os.getenv("LEAD_WEBHOOK_URL", "").strip()
    if not url:
        return False
    try:
        import requests  # ya está instalado
        resp = requests.post(url, json=payload, timeout=5)
        return resp.status_code < 400
    except Exception as e:
        try:
            current_app.logger.warning("[LEAD] Webhook fallo: %s", e)
        except Exception:
            pass
        return False

# SMS sender reutilizando routes_auth (si existe); fallback a log
try:
    from routes_auth import send_sms as _send_sms
    def send_sms(to: str, body: str) -> bool:
        return _send_sms(to, body)
except Exception:
    def send_sms(to: str, body: str) -> bool:
        try:
            current_app.logger.info("[SMS] (dummy) to=%s body=%s", to, body)
        except Exception:
            pass
        return False

bp_sms = Blueprint("sms", __name__)

PHONE_RE = re.compile(r"^\+?\d{9,15}$")

def _normalize_phone(v: str | None) -> str:
    s = re.sub(r"[^\d+]", "", v or "")
    if not s: return ""
    if s.startswith("+"): return s
    if s.startswith("34"): return "+"+s
    if re.fullmatch(r"\d{9,15}", s): return "+34"+s
    return s

def _ensure_leads_table():
    if Lead is None: return
    try:
        Lead.__table__.create(bind=db.engine, checkfirst=True)
    except sa_exc.SQLAlchemyError as e:
        try:
            current_app.logger.warning("[SMS IN] No se pudo asegurar tabla leads: %s", e)
        except Exception:
            pass

def _infer_kind(text: str) -> str:
    t = (text or "").lower()
    if any(w in t for w in ("franquicia","franquiciado","franchise")): return "franchise"
    if any(w in t for w in ("propietario","dueño","dueno","propiedad")): return "owner"
    return "tenant"

def _extract_zone(text: str):
    t = (text or "").lower()
    if "granada" in t:
        return ("granada", None)
    return (None, None)

@bp_sms.post("/inbound")
def sms_inbound():
    try:
        frm = _normalize_phone(request.form.get("From", ""))
        to  = _normalize_phone(request.form.get("To", ""))
        body= (request.form.get("Body", "") or "").strip()
        sid = request.form.get("MessageSid", "")
        num_media = int(request.form.get("NumMedia", "0") or 0)
        current_app.logger.info("[SMS IN] from=%s to=%s sid=%s media=%s body=%s",
                                frm, to, sid, num_media, body[:200])
    except Exception as e:
        try:
            current_app.logger.warning("[SMS IN] Excepción parseando payload: %s", e)
        except Exception:
            pass
        return jsonify(ok=True)

    if not frm or not PHONE_RE.fullmatch(frm):
        return jsonify(ok=True)

    provincia, municipio = _extract_zone(body)
    franquiciado_id = route_franchisee(provincia, municipio)

    lead_dict = None
    try:
        if Lead is not None:
            _ensure_leads_table()
            kind = _infer_kind(body)
            lead = Lead(
                kind=kind,
                source="sms",
                provincia=provincia,
                municipio=municipio,
                nombre=frm,
                telefono=frm,
                email=None,
                assigned_to=franquiciado_id,
                status="assigned" if franquiciado_id else "new",
                notes=body or None,
                meta_json={"twilio": {"to": to, "sid": sid, "num_media": num_media}},
            )
            db.session.add(lead)
            db.session.commit()
            lead_dict = {
                "id": lead.id,
                "kind": lead.kind,
                "source": lead.source,
                "telefono": lead.telefono,
                "email": lead.email,
                "nombre": lead.nombre,
                "status": lead.status,
                "provincia": lead.provincia,
                "municipio": lead.municipio,
                "notes": lead.notes,
                "created_at": lead.created_at.isoformat(),
                "assigned_to": lead.assigned_to,
            }
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        try:
            current_app.logger.warning("[SMS IN] No se pudo crear Lead: %s", e)
        except Exception:
            pass

    try:
        if lead_dict:
            notify_lead_webhook(lead_dict)
    except Exception as e:
        try:
            current_app.logger.warning("[LEAD] Webhook fallo: %s", e)
        except Exception:
            pass

    try:
        if franquiciado_id and lead_dict:
            c = contact_for(franquiciado_id)
            if c and c.get("sms"):
                txt = f"Nuevo lead SMS ({lead_dict['telefono']}): {lead_dict['notes'] or ''}"
                send_sms(c["sms"], txt[:140])
    except Exception as e:
        try:
            current_app.logger.warning("[LEAD] Aviso SMS a franquiciado fallo: %s", e)
        except Exception:
            pass

    return jsonify(ok=True, lead=lead_dict or None)
