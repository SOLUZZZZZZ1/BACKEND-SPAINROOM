# routes_sms.py — Inbound SMS Twilio
from datetime import datetime
from flask import Blueprint, request, jsonify, make_response, current_app

bp_sms = Blueprint("sms", __name__)

def _log(msg):
    try:
        current_app.logger.info(f"[SMS] {msg}")
    except Exception:
        pass

@bp_sms.route("/sms/inbound", methods=["POST","GET","OPTIONS"])
def sms_inbound():
    # Preflight CORS (por si pruebas desde navegador)
    if request.method == "OPTIONS":
        resp = make_response("", 200)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "POST,GET,OPTIONS"
        return resp

    # Twilio envía form-encoded
    frm  = request.values.get("From", "")
    to   = request.values.get("To", "")
    body = (request.values.get("Body", "") or "").strip()
    sid  = request.values.get("MessageSid", "")

    _log(f"inbound From={frm} To={to} Body='{body}' Sid={sid}")

    # Si quisieras responder con SMS:
    # twiml = f"<Response><Message>Recibido: {body}</Message></Response>"
    # return make_response(twiml, 200, {"Content-Type":"text/xml"})

    return make_response("", 200)

@bp_sms.route("/sms/fallback", methods=["POST","GET"])
def sms_fallback():
    _log("fallback called")
    return make_response("", 200)

@bp_sms.get("/sms/ping")
def sms_ping():
    return jsonify(ok=True, ts=datetime.utcnow().isoformat())
