# routes_wa.py — WhatsApp Business (MessageBird Conversations) para SpainRoom
# Nora · 2025-10-16
#
# Rutas:
#   POST /api/wa/send_template  → envía plantilla (p.ej. "login_code" con {{1}})
#   GET|POST /webhooks/wa       → verificación (GET, Meta) y eventos entrantes (POST, MessageBird)
#
# ENV requeridas (Render -> backend-API):
#   WA_PROVIDER=messagebird
#   WA_API_KEY=<AccessKey de MessageBird>               # tal cual te la dieron
#   WA_ENDPOINT=https://conversations.messagebird.com/v1/send
#   WA_CHANNEL_ID=<Channel ID del conector WA en Bird>  # ver Channels->WhatsApp->tu canal
#   WA_VERIFY_TOKEN=spainroom2025                       # para GET de verificación (si usas Meta)
#   WA_SIGNING_KEY=<Signing key del webhook de Bird>    # opcional pero recomendado
#
# Nota: no poner "LIVE-" si tu clave no lo trae. COPIA EXACTA.

import os, json, hmac, hashlib, requests
from flask import Blueprint, request, jsonify, current_app

bp_wa = Blueprint("wa", __name__, url_prefix="/api/wa")
bp_wa_webhook = Blueprint("wa_webhook", __name__)

WA_PROVIDER     = (os.getenv("WA_PROVIDER") or "messagebird").lower()
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "spainroom2025")
WA_API_KEY      = os.getenv("WA_API_KEY", "").strip()
WA_ENDPOINT     = os.getenv("WA_ENDPOINT", "https://conversations.messagebird.com/v1/send").strip()
WA_CHANNEL_ID   = os.getenv("WA_CHANNEL_ID", "").strip()
WA_SIGNING_KEY  = os.getenv("WA_SIGNING_KEY", "").strip()

def _log(msg, *args):
    try:
        current_app.logger.info(str(msg), *args)
    except Exception:
        pass

# ====================== ENVÍO DE PLANTILLAS (MessageBird) ======================

@bp_wa.route("/send_template", methods=["POST"])
def send_template():
    """
    Body JSON:
      { "to":"+34616...","template":"login_code","params":["123456"], "lang":"es" }
    """
    data = request.get_json(force=True) or {}
    to       = (data.get("to") or "").strip()
    template = (data.get("template") or "").strip()
    params   = data.get("params") or []
    lang     = (data.get("lang") or "es").strip()

    if not (to and template):
        return jsonify(ok=False, error="missing_to_or_template"), 400
    if not (WA_API_KEY and WA_CHANNEL_ID):
        return jsonify(ok=False, error="messagebird_not_configured"), 500

    headers = {
        "Authorization": f"AccessKey {WA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": to,
        "from": WA_CHANNEL_ID,
        "type": "hsm",
        "content": {
            "hsm": {
                "namespace": "",                             # si tu conector no requiere namespace, vacío
                "templateName": template,                    # ej: "login_code"
                "language": {"policy": "deterministic", "code": lang},
                "params": [{"default": str(p)} for p in (params or [])]
            }
        }
    }
    r = requests.post(WA_ENDPOINT, headers=headers, json=payload, timeout=20)
    _log("[WA SEND] to=%s template=%s code=%s resp=%s", to, template, r.status_code, r.text[:300])
    return jsonify(ok=(200 <= r.status_code < 300), status=r.status_code, response=r.text)

# ====================== WEBHOOK (MessageBird) ======================

def _verify_messagebird_signature(signing_key: str, raw_body: bytes, timestamp: str, signature: str) -> bool:
    """
    Firma HMAC de Conversations (MessageBird):
    message = <timestamp>.encode() + raw_body
    signature = HMAC_SHA256(signing_key, message).hexdigest()
    """
    try:
        if not (signing_key and timestamp and signature):
            return False
        message = timestamp.encode() + raw_body
        mac = hmac.new(signing_key.encode(), msg=message, digestmod=hashlib.sha256).hexdigest()
        return hmac.compare_digest(mac, signature)
    except Exception:
        return False

@bp_wa_webhook.route("/webhooks/wa", methods=["GET","POST"])
def webhooks_wa():
    # Verificación GET (Cloud API Meta; aquí por compatibilidad)
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == WA_VERIFY_TOKEN:
            return challenge or "", 200
        return "forbidden", 403

    # POST → Conversaciones (MessageBird) / posibles formatos
    raw = request.get_data(cache=False, as_text=False)
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        data = {}

    # Validar firma si tenemos WA_SIGNING_KEY
    if WA_SIGNING_KEY:
        ts  = request.headers.get("MessageBird-Request-Timestamp", "")
        sig = request.headers.get("MessageBird-Signature", "")
        if not _verify_messagebird_signature(WA_SIGNING_KEY, raw, ts, sig):
            return jsonify(ok=False, error="invalid_signature"), 403

    # LOG y TODO: aquí parseas eventos según schema de Bird
    # Ejemplos de eventos:
    # - conversation.created
    # - message.created (inbound/outbound)
    _log("[WA WEBHOOK] payload=%s", json.dumps(data)[:1000])

    # Si quieres detectar respuestas a OTP:
    # msg = (data.get("message") or {}).get("content") or {}
    # txt = (msg.get("text") or {}).get("text")
    # if txt and "123456" in txt: ...  # validar código, etc.

    return jsonify(ok=True)
