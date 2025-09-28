# routes_sms.py — Webhook de SMS entrantes (Twilio) con validación de firma
import os
from flask import Blueprint, request, jsonify, current_app
from twilio.request_validator import RequestValidator

bp_sms = Blueprint("sms", __name__)

def _is_valid_twilio_request(req) -> bool:
    """
    Valida la firma 'X-Twilio-Signature' del webhook.
    Requiere TWILIO_AUTH_TOKEN en variables de entorno del API.
    """
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not token:
        current_app.logger.warning("[SMS IN] Falta TWILIO_AUTH_TOKEN para validar firma")
        return False

    tw_sig = req.headers.get("X-Twilio-Signature", "")
    if not tw_sig:
        current_app.logger.warning("[SMS IN] Falta cabecera X-Twilio-Signature")
        return False

    # URL pública tal como la recibe Twilio (Render usa SSL válido)
    url = request.url
    params = request.form.to_dict(flat=True)

    try:
        validator = RequestValidator(token)
        ok = validator.validate(url, params, tw_sig)
        if not ok:
            current_app.logger.warning("[SMS IN] Firma inválida: %s", tw_sig)
        return ok
    except Exception as e:
        current_app.logger.warning("[SMS IN] Error validando firma: %s", e)
        return False

@bp_sms.post("/inbound")
def sms_inbound():
    # Si quieres relajar validación temporalmente, comenta el bloque siguiente:
    if not _is_valid_twilio_request(request):
        return jsonify(ok=False, error="invalid_signature"), 403

    frm = request.form.get("From", "")
    to  = request.form.get("To", "")
    body= request.form.get("Body", "")
    sid = request.form.get("MessageSid", "")
    num = int(request.form.get("NumMedia", "0") or 0)

    current_app.logger.info("[SMS IN] from=%s to=%s sid=%s body=%s media=%s",
                            frm, to, sid, (body or "")[:160], num)

    # TODO: si quieres, crea un lead automático aquí:
    # from extensions import db
    # from models_leads import Lead
    # db.session.add(Lead(kind="tenant", source="sms",
    #     provincia=None, municipio=None, nombre=None, telefono=frm, notes=body))
    # db.session.commit()

    return jsonify(ok=True)
