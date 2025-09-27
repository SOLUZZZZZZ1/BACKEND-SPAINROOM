# routes_dev_sms.py — Endpoint de prueba para enviar SMS con Twilio
# Uso:
#   POST /api/dev/sms_test
#   body: { "phone": "+34683634299", "body": "Hola desde SpainRoom" }

import os
from flask import Blueprint, request, jsonify
# Reutilizamos utilidades ya definidas en routes_auth
from routes_auth import send_sms, normalize_phone

bp_dev_sms = Blueprint("devsms", __name__)

@bp_dev_sms.post("/api/dev/sms_test")
def sms_test():
    data = request.get_json(force=True) or {}
    phone = normalize_phone(data.get("phone") or "")
    body  = (data.get("body") or "Prueba SMS SpainRoom").strip()
    if not phone:
        return jsonify(ok=False, error="missing_phone", message="Incluye el teléfono en formato +34..."), 400

    ok = send_sms(phone, body)
    # ok == True → Twilio aceptó el mensaje (verás el SID en los logs del API)
    # ok == False → Twilio no configurado o error al enviar (revisa TWILIO_* en env)
    return jsonify(ok=bool(ok)))
