# routes_sms.py — Webhook de SMS (Twilio) + asignación Granada + notificación
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

# Notificadores
from services import notify_lead_webhook, send_sms

# Validación firma Twilio (opcional)
VALIDATE_SIGNATURE = (os.getenv("VALIDATE_TWILIO_SIGNATURE", "off").lower() == "on")
if VALIDATE_SIGNATURE:
    try:
        from twilio.request_validator import RequestValidator  # type: ignore
    except Exception:
        VALIDATE_SIGNATURE = False

bp_sms = Blueprint("sms", __name__)

PHONE_RE = re.compile(r"^\+?\d{9,15}$")

def _normalize_phone(v: str | None) -> str:
    s = re.sub(r"[^\d+]", "", v or "")
    if not s: return ""
    if s.startswith("+"): return s
    if s.startswith("34"): return "+"+s
    if re.fullmatch(r"\d{9,15}", s): return "+34"+s
    return s

def _validate_twilio_signature() -> bool:
    if not VALIDATE_SIGNATURE:
        return True
    try:
        token = os.getenv("TWILIO_AUTH_TOKEN", "")
        if not token:
            current_app.logger.warning("[SMS IN] Falta TWILIO_AUTH_TOKEN para validar firma.")
            return False
        tw_sig = request.headers.get("X-Twilio-Signature", "")
        if not tw_sig:
            current_app.logger.warning("[SMS IN] Falta cabecera X-Twilio-Signature.")
            return False
        validator = RequestValidator(token)  # type: ignore
        ok = validator.validate(request.url, request.form.to_dict(flat=True), tw_sig)
        if not ok:
            current_app.logger.warning("[SMS IN] Firma inválida: %s", tw_sig)
        return ok
    except Exception as e:
        current_app.logger.warning("[SMS IN] Error validando firma: %s", e)
        return False

def _ensure_leads_table():
    if Lead is None: return
    try:
        Lead.__table__.create(bind=db.engine, checkfirst=True)
    except sa_exc.SQLAlchemyError as e:
        current_app.logger.warning("[SMS IN] No se pudo asegurar tabla leads: %s", e)

def _infer_kind(text: str) -> str:
    t = (text or "").lower()
    if any(w in t for w in ("franquicia","franquiciado","franchise")): return "franchise"
    if any(w in t for w in ("propietario","dueño","dueno","propiedad")): return "owner"
    return "tenant"

def _extract_zone(text: str) -> tuple[str|None, str|None]:
    """
    Heurística muy simple: si el cuerpo menciona 'granada', marcamos provincia=granada.
    Puedes ampliar con más palabras o un diccionario de municipios→provincia.
    """
    t = (text or "").lower()
    if "granada" in t:
        return ("granada", None)
    return (None, None)

@bp_sms.post("/inbound")
def sms_inbound():
    # 1) Firma (si activada)
    if not _validate_twilio_signature():
        return jsonify(ok=False, error="invalid_signature"), 403

    # 2) Parse
    try:
        frm = _normalize_phone(request.form.get("From", ""))
        to  = _normalize_phone(request.form.get("To", ""))
        body= (request.form.get("Body", "") or "").strip()
        sid = request.form.get("MessageSid", "")
        num_media = int(request.form.get("NumMedia", "0") or 0)
        current_app.logger.info("[SMS IN] from=%s to=%s sid=%s media=%s body=%s",
                                frm, to, sid, num_media, body[:200])
    except Exception as e:
        current_app.logger.warning("[SMS IN] Excepción parseando payload: %s", e)
        return jsonify(ok=True)

    if not frm or not PHONE_RE.fullmatch(frm):
        return jsonify(ok=True)

    # 3) Inferir zona y franquiciado
    provincia, municipio = _extract_zone(body)
    franquiciado_id = route_franchisee(provincia, municipio)

    # 4) Guardar lead (nombre=teléfono para esquemas con NOT NULL)
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
                nombre=frm,                     # evita NOT NULL
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
        try: db.session.rollback()
        except Exception: pass
        current_app.logger.warning("[SMS IN] No se pudo crear Lead: %s", e)

    # 5) Notificar webhook externo (no rompe si falla)
    try:
        if lead_dict:
            notify_lead_webhook(lead_dict)
    except Exception as e:
        current_app.logger.warning("[LEAD] Webhook fallo: %s", e)

    # 6) (Opcional) SMS de aviso al franquiciado
    try:
        if franquiciado_id and lead_dict:
            c = contact_for(franquiciado_id)
            if c and c.get("sms"):
                txt = f"Nuevo lead SMS ({lead_dict['telefono']}): {lead_dict['notes'] or ''}"
                send_sms(c["sms"], txt[:140])
    except Exception as e:
        current_app.logger.warning("[LEAD] Aviso SMS a franquiciado fallo: %s", e)

    return jsonify(ok=True, lead=lead_dict or None)
