# routes_twilio.py — Voice IVR básico + SMS helper (Twilio)
# Nora · 2025-10-12
import os
from flask import Blueprint, request, make_response, jsonify, current_app

bp_twilio = Blueprint("twilio", __name__)

TWILIO_ACCOUNT_SID = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
TWILIO_AUTH_TOKEN  = (os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
TWILIO_NUMBER      = (os.getenv("TWILIO_NUMBER") or "").strip()

def _send_sms(to: str, body: str) -> bool:
    # Si no hay credenciales, modo demo (log) sin romper
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_NUMBER):
        try: current_app.logger.info("[SMS demo] to=%s body=%s", to, body)
        except Exception: pass
        return False
    try:
        from twilio.rest import Client
        c = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        c.messages.create(from_=TWILIO_NUMBER, to=to, body=body)
        return True
    except Exception as e:
        try: current_app.logger.warning("[Twilio] SMS error: %s", e)
        except Exception: pass
        return False

@bp_twilio.route("/twilio/voice/inbound", methods=["POST"])
def voice_inbound():
    # Twilio envía From; respondemos TwiML + SMS con enlace
    from_num = request.form.get("From","")
    try:
        if from_num:
            _send_sms(from_num, "Gracias por llamar a SpainRoom. Visita https://spainroom.vercel.app para reservar o subir documentación. ¡Te ayudamos!")
    except Exception:
        pass
    xml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<Response>\n  <Say voice=\"alice\" language=\"es-ES\">Gracias por llamar a SpainRoom. Te enviamos un mensaje con el enlace directo para continuar.</Say>\n  <Pause length=\"1\"/>\n  <Hangup/>\n</Response>\n"
    resp = make_response(xml, 200); resp.headers["Content-Type"] = "application/xml"
    return resp

@bp_twilio.route("/twilio/sms/send", methods=["POST"])
def sms_send():
    data = request.get_json(silent=True) or {}
    to = (data.get("to") or "").strip()
    body = (data.get("body") or "").strip()
    if not (to and body):
        return jsonify(ok=False, error="bad_request"), 400
    ok = _send_sms(to, body)
    return jsonify(ok=True, sent=bool(ok))
