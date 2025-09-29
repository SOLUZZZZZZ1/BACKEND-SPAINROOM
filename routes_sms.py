# routes_sms.py — Webhook de SMS (Twilio) + creación de Lead en BD (robusto)
import os
import re
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import exc as sa_exc
from extensions import db

# --------- Modelos ----------
try:
    from models_leads import Lead
except Exception:
    Lead = None  # tolerante: si no está, no insertamos y no rompemos el webhook

# Enrutado opcional a franquiciado
try:
    from services_owner import route_franchisee as guess_franquiciado  # type: ignore
except Exception:
    def guess_franquiciado(provincia, municipio):
        return None

# --------- Firma Twilio opcional ----------
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
    if not s:
        return ""
    if s.startswith("+"):
        return s
    if s.startswith("34"):
        return "+" + s
    if re.fullmatch(r"\d{9,15}", s):
        return "+34" + s
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
    """Crea la tabla leads si falta (evita 500 en despliegues limpios)."""
    if Lead is None:
        return
    try:
        Lead.__table__.create(bind=db.engine, checkfirst=True)
    except sa_exc.SQLAlchemyError as e:
        current_app.logger.warning("[SMS IN] No se pudo asegurar tabla leads: %s", e)


def _infer_kind(text: str) -> str:
    t = (text or "").lower()
    if any(w in t for w in ("franquicia", "franquiciado", "franchise")):
        return "franchise"
    if any(w in t for w in ("propietario", "dueño", "dueno", "propiedad", "mi piso")):
        return "owner"
    if any(w in t for w in ("inquilino", "alquiler", "alquilar", "habitación", "habitacion", "room", "hab")):
        return "tenant"
    return "tenant"


@bp_sms.post("/inbound")
def sms_inbound():
    # 1) Firma (si activada)
    if not _validate_twilio_signature():
        return jsonify(ok=False, error="invalid_signature"), 403

    # 2) Parse seguro
    try:
        frm = _normalize_phone(request.form.get("From", ""))
        to  = _normalize_phone(request.form.get("To", ""))
        body = (request.form.get("Body", "") or "").strip()
        sid  = request.form.get("MessageSid", "")
        num_media = int(request.form.get("NumMedia", "0") or 0)
        current_app.logger.info(
            "[SMS IN] from=%s to=%s sid=%s media=%s body=%s",
            frm, to, sid, num_media, body[:200]
        )
    except Exception as e:
        current_app.logger.warning("[SMS IN] Excepción parseando payload: %s", e)
        return jsonify(ok=True)  # Twilio no reintenta

    # 3) Validación básica del remitente
    if not frm or not PHONE_RE.fullmatch(frm):
        return jsonify(ok=True)

    # 4) Autocrear tabla e insertar Lead (nunca 500: rollback y 200)
    try:
        if Lead is not None:
            _ensure_leads_table()
            kind = _infer_kind(body)
            provincia = None
            municipio = None
            assigned_to = guess_franquiciado(provincia, municipio)

            # nombre = teléfono para cumplir esquemas con NOT NULL en 'nombre'
            lead = Lead(
                kind=kind,
                source="sms",
                provincia=provincia,
                municipio=municipio,
                nombre=frm,                 # ← clave: evita NOT NULL
                telefono=frm,
                email=None,
                assigned_to=assigned_to,
                status="assigned" if assigned_to else "new",
                notes=body or None,
                meta_json={"twilio": {"to": to, "sid": sid, "num_media": num_media}},
            )
            db.session.add(lead)
            db.session.commit()
            return jsonify(ok=True, lead={
                "id": lead.id,
                "kind": lead.kind,
                "telefono": lead.telefono,
                "assigned_to": lead.assigned_to,
                "status": lead.status
            })
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.warning("[SMS IN] No se pudo crear Lead: %s", e)

    # 5) Siempre 200 (para que Twilio no reintente)
    return jsonify(ok=True)
