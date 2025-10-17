# routes_wa.py — SpainRoom WhatsApp (MessageBird API)
# Nora · SpainRoom 2025-10-17
import os, json, requests
from flask import Blueprint, request, jsonify, current_app

bp_wa = Blueprint("wa", __name__, url_prefix="/api/wa")

# === Configuración desde variables de entorno ===
WA_PROVIDER   = os.getenv("WA_PROVIDER", "messagebird").strip()
WA_API_KEY    = os.getenv("WA_API_KEY", "").strip()
WA_ENDPOINT   = os.getenv("WA_ENDPOINT", "https://conversations.messagebird.com/v1/send").strip()
WA_CHANNEL_ID = os.getenv("WA_CHANNEL_ID", "").strip()
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "spainroom2025").strip()

# === Función genérica de envío ===
def send_template_message(to, template, params=None, lang="es"):
    """Envía un mensaje de plantilla WhatsApp (MessageBird Conversations API)."""
    if not (WA_API_KEY and WA_CHANNEL_ID):
        return {"ok": False, "error": "missing_credentials"}

    headers = {
        "Authorization": f"AccessKey {WA_API_KEY}",
        "Content-Type": "application/json",
    }

    # Contenido del HSM (Highly Structured Message)
    payload = {
        "to": to,
        "from": WA_CHANNEL_ID,
        "type": "hsm",
        "content": {
            "hsm": {
                "templateName": template,
                "language": {"policy": "deterministic", "code": lang},
                "params": [{"default": str(p)} for p in (params or [])],
            }
        },
    }

    try:
        r = requests.post(WA_ENDPOINT, headers=headers, json=payload, timeout=15)
        code = r.status_code
        resp = r.text
        current_app.logger.info(f"[WA SEND] to={to} template={template} code={code} resp={resp}")
        return {"ok": (200 <= code < 300), "status": code, "response": resp}
    except Exception as e:
        current_app.logger.error(f"[WA ERROR] {e}")
        return {"ok": False, "error": str(e)}

# === Endpoint: envío de plantillas ===
@bp_wa.route("/send_template", methods=["POST", "OPTIONS"])
def send_template():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True) or {}
    to = data.get("to")
    template = data.get("template")
    params = data.get("params", [])
    lang = data.get("lang", "es")
    if not (to and template):
        return jsonify(ok=False, error="missing_parameters"), 400
    result = send_template_message(to, template, params, lang)
    return jsonify(result)

# === Webhook (por si usas callbacks desde Bird) ===
@bp_wa.route("/webhook", methods=["POST", "GET"])
def webhook():
    """Webhook receptor para actualizaciones desde Bird."""
    if request.method == "GET":
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if token == WA_VERIFY_TOKEN:
            return challenge or "verified", 200
        return "invalid token", 403

    try:
        payload = request.get_json(force=True)
        current_app.logger.info(f"[WA WEBHOOK] {json.dumps(payload)}")
    except Exception as e:
        current_app.logger.warning(f"[WA WEBHOOK ERROR] {e}")
        return jsonify(ok=False), 400
    return jsonify(ok=True)
