# routes_wa.py — WhatsApp Business (MessageBird / Meta / 360dialog) para SpainRoom
# Nora · 2025-10-16
import os, json, hmac, hashlib, requests
from flask import Blueprint, request, jsonify, current_app

bp_wa = Blueprint("wa", __name__, url_prefix="/api/wa")
bp_wa_webhook = Blueprint("wa_webhook", __name__)

# === CONFIG ===
WA_PROVIDER     = (os.getenv("WA_PROVIDER") or "messagebird").lower()   # "messagebird" | "meta" | "360dialog"
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "spainroom2025")

# MessageBird (Conversations API)
WA_API_KEY      = os.getenv("WA_API_KEY", "").strip()                   # AccessKey (no inventar LIVE- si no lo trae)
WA_ENDPOINT     = os.getenv("WA_ENDPOINT", "https://conversations.messagebird.com/v1/send").strip()
WA_CHANNEL_ID   = os.getenv("WA_CHANNEL_ID", "").strip()                # Channel ID del conector WA en Bird
WA_SIGNING_KEY  = os.getenv("WA_SIGNING_KEY", "").strip()               # opcional: firma HMAC de webhook

# Meta Cloud API (si algún día lo usas)
WA_PHONE_ID     = os.getenv("WA_PHONE_ID", "").strip()
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN", "").strip()
WA_TEMPLATE_NS  = os.getenv("WA_TEMPLATE_NS", "").strip()               # opcional (360dialog/legacy)

def _log(msg, *args):
    try:
        current_app.logger.info(str(msg), *args)
    except Exception:
        pass

# ========================= ENVÍO DE PLANTILLAS =========================

def _send_template_messagebird(to: str, template: str, params: list, lang="es"):
    """
    Envío por MessageBird Conversations API (WhatsApp HSM template).
    Requiere: WA_API_KEY, WA_CHANNEL_ID, WA_ENDPOINT.
    """
    if not (WA_API_KEY and WA_CHANNEL_ID):
        return 500, "MessageBird not configured (WA_API_KEY/WA_CHANNEL_ID missing)"

    headers = {
        "Authorization": f"AccessKey {WA_API_KEY}",
        "Content-Type": "application/json",
    }
    # HSM (template) — si tu conector no usa namespace, déjalo vacío:
    payload = {
        "to": to,
        "from": WA_CHANNEL_ID,
        "type": "hsm",
        "content": {
            "hsm": {
                "namespace": WA_TEMPLATE_NS or "",
                "templateName": template,                     # p. ej. "login_code"
                "language": { "policy": "deterministic", "code": lang },
                "params": [{"default": str(p)} for p in (params or [])]
            }
        }
    }
    r = requests.post(WA_ENDPOINT, headers=headers, json=payload, timeout=20)
    return r.status_code, r.text

def _send_template_meta(to: str, template: str, params: list, lang="es"):
    if not (WA_PHONE_ID and WA_ACCESS_TOKEN):
        return 500, "Meta not configured (WA_PHONE_ID/WA_ACCESS_TOKEN missing)"
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"}
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": lang},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in (params or [])]
            }]
        }
    }
    r = requests.post(url, headers=headers, json=body, timeout=20)
    return r.status_code, r.text

def _send_template_360dialog(to: str, template: str, params: list, lang="es"):
    if not (WA_API_KEY and WA_ENDPOINT):
        return 500, "360dialog not configured (WA_API_KEY/WA_ENDPOINT missing)"
    headers = {"D360-API-KEY": WA_API_KEY, "Content-Type": "application/json"}
    body = {
        "to": to,
        "type": "template",
        "template": {
            "namespace": WA_TEMPLATE_NS or "",
            "name": template,
            "language": {"policy": "deterministic", "code": lang},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in (params or [])]
            }]
        }
    }
    r = requests.post(WA_ENDPOINT, headers=headers, json=body, timeout=20)
    return r.status_code, r.text

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

    if WA_PROVIDER == "messagebird":
        code, resp = _send_template_messagebird(to, template, params, lang)
    elif WA_PROVIDER == "meta":
        code, resp = _send_template_meta(to, template, params, lang)
    else:  # 360dialog
        code, resp = _send_template_360dialog(to, template, params, lang)

    _log("[WA SEND] provider=%s to=%s template=%s code=%s", WA_PROVIDER, to, template, code)
    return jsonify(ok=(200 <= code < 300), status=code, response=resp)

# ========================= WEBHOOK =========================

def _verify_messagebird_signature(signing_key: str, raw_body: bytes, timestamp: str, signature: str) -> bool:
    """Firma HMAC (opcional) para webhooks Conversations (MessageBird)."""
    try:
        message = timestamp.encode() + raw_body
        mac = hmac.new(signing_key.encode(), msg=message, digestmod=hashlib.sha256).hexdigest()
        return hmac.compare_digest(mac, signature)
    except Exception:
        return False

@bp_wa_webhook.route("/webhooks/wa", methods=["GET","POST"])
def webhooks_wa():
    # Verificación tipo Meta (si algún día usas Cloud API directo)
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == WA_VERIFY_TOKEN:
            return challenge or "", 200
        return "forbidden", 403

    # POST → payload entrante (MessageBird o Meta)
    raw = request.get_data(cache=False, as_text=False)
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        data = {}

    # Firma HMAC (opcional) para MessageBird Conversations
    if WA_PROVIDER == "messagebird" and WA_SIGNING_KEY:
        ts  = request.headers.get("MessageBird-Request-Timestamp", "")
        sig = request.headers.get("MessageBird-Signature", "")
        if not _verify_messagebird_signature(WA_SIGNING_KEY, raw, ts, sig):
            return jsonify(ok=False, error="invalid_signature"), 403

    _log("[WA WEBHOOK] provider=%s payload=%s", WA_PROVIDER, json.dumps(data)[:1000])
    # Aquí puedes crear lead, responder, etc…
    return jsonify(ok=True)
