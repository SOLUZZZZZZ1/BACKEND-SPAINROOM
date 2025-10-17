# routes_wa.py — SpainRoom WhatsApp (MessageBird Conversations API) — definitivo
# Nora · 2025-10-17
import os, json, requests, hmac, hashlib
from flask import Blueprint, request, jsonify, current_app

bp_wa = Blueprint("wa", __name__, url_prefix="/api/wa")
bp_wa_webhook = Blueprint("wa_webhook", __name__)  # <— webhook separado, path absoluto /webhooks/wa

# === ENV ===
WA_PROVIDER     = (os.getenv("WA_PROVIDER") or "messagebird").strip().lower()
WA_API_KEY      = (os.getenv("WA_API_KEY") or "").strip()       # AccessKey (API), NO SCIM
WA_ENDPOINT     = (os.getenv("WA_ENDPOINT") or "https://conversations.messagebird.com/v1/send").strip()
WA_CHANNEL_ID   = (os.getenv("WA_CHANNEL_ID") or "").strip()    # UUID del conector WhatsApp (Channel ID)
WA_VERIFY_TOKEN = (os.getenv("WA_VERIFY_TOKEN") or "spainroom2025").strip()
WA_SIGNING_KEY  = (os.getenv("WA_SIGNING_KEY") or "").strip()   # opcional (firma HMAC del webhook)

def _log(msg, *args):
    try: current_app.logger.info(str(msg), *args)
    except Exception: pass

# ========= Envío de plantillas =========
@bp_wa.route("/send_template", methods=["POST","OPTIONS"])
def send_template():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True) or {}
    to       = (data.get("to") or "").strip()
    template = (data.get("template") or "").strip()
    params   = data.get("params") or []
    lang     = (data.get("lang") or "es").strip()
    if not (to and template):
        return jsonify(ok=False, error="missing_parameters"), 400
    if not (WA_API_KEY and WA_CHANNEL_ID):
        return jsonify(ok=False, error="messagebird_not_configured"), 500

    headers = {"Authorization": f"AccessKey {WA_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "to": to,
        "from": WA_CHANNEL_ID,
        "type": "hsm",
        "content": {
            "hsm": {
                "namespace": "",  # la mayoría de canales WA en Bird no lo requieren
                "templateName": template,           # ej: "login_code"
                "language": {"policy":"deterministic","code": lang},
                "params": [{"default": str(p)} for p in params]
            }
        }
    }
    r = requests.post(WA_ENDPOINT, headers=headers, json=payload, timeout=20)
    _log("[WA SEND] to=%s template=%s code=%s resp=%s", to, template, r.status_code, r.text[:500])
    return jsonify(ok=(200 <= r.status_code < 300), status=r.status_code, response=r.text)

# ========= Webhook (MessageBird -> SpainRoom) =========
def _verify_bird_signature(signing_key: str, raw_body: bytes, timestamp: str, signature: str) -> bool:
    try:
        if not (signing_key and timestamp and signature): return False
        message = timestamp.encode() + raw_body
        mac = hmac.new(signing_key.encode(), msg=message, digestmod=hashlib.sha256).hexdigest()
        return hmac.compare_digest(mac, signature)
    except Exception:
        return False

@bp_wa_webhook.route("/webhooks/wa", methods=["POST","GET","OPTIONS"])
def wa_webhook():
    if request.method == "OPTIONS":
        return ("", 204)
    # GET (compat Meta) — verificación simple
    if request.method == "GET":
        token = request.args.get("hub.verify_token"); challenge = request.args.get("hub.challenge")
        return (challenge or "forbidden", 200 if token == WA_VERIFY_TOKEN else 403)

    # POST — eventos Conversations (message.created, conversation.created, etc.)
    raw = request.get_data(cache=False, as_text=False)
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        payload = {}

    if WA_SIGNING_KEY:
        ts  = request.headers.get("MessageBird-Request-Timestamp", "")
        sig = request.headers.get("MessageBird-Signature", "")
        if not _verify_bird_signature(WA_SIGNING_KEY, raw, ts, sig):
            return jsonify(ok=False, error="invalid_signature"), 403

    _log("[WA WEBHOOK] %s", json.dumps(payload)[:1000])
    # TODO: aquí procesas los eventos si quieres
    return jsonify(ok=True)
