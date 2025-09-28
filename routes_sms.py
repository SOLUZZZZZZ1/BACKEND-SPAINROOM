# routes_sms.py — Webhook SMS (modo seguro sin BD)
import os, re
from flask import Blueprint, request, jsonify, current_app

bp_sms = Blueprint("sms", __name__)

# Validación firma opcional
VALIDATE_SIGNATURE = (os.getenv("VALIDATE_TWILIO_SIGNATURE", "off").lower() == "on")
if VALIDATE_SIGNATURE:
    try:
        from twilio.request_validator import RequestValidator  # type: ignore
    except Exception:
        VALIDATE_SIGNATURE = False

PHONE_RE = re.compile(r"^\+?\d{9,15}$")

def _normalize_phone(v: str | None) -> str:
    s = re.sub(r"[^\d+]", "", v or "")
    if not s: return ""
    if s.startswith("+"): return s
    if s.startswith("34"): return "+" + s
    if re.fullmatch(r"\d{9,15}", s): return "+34" + s
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

@bp_sms.post("/inbound")
def sms_inbound():
    # 1) Firma (si está activada)
    if not _validate_twilio_signature():
        return jsonify(ok=False, error="invalid_signature"), 403

    # 2) Parse y LOG — sin tocar BD
    try:
        frm = _normalize_phone(request.form.get("From", ""))
        to  = _normalize_phone(request.form.get("To", ""))
        body= (request.form.get("Body", "") or "").strip()
        sid = request.form.get("MessageSid", "")
        num_media = int(request.form.get("NumMedia", "0") or 0)
        current_app.logger.info("[SMS IN][NO-DB] from=%s to=%s sid=%s media=%s body=%s", frm, to, sid, num_media, body[:200])
    except Exception as e:
        current_app.logger.warning("[SMS IN] Excepción parseando payload: %s", e)
        return jsonify(ok=True, mode="no-db")

    # 3) Validación básica
    if not frm or not PHONE_RE.fullmatch(frm):
        return jsonify(ok=True, mode="no-db")

    # 4) Siempre 200 (Twilio no reintenta)
    return jsonify(ok=True, mode="no-db")
