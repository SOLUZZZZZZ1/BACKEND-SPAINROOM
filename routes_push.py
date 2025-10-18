# routes_push.py — SpainRoom Push Notifications (Firebase FCM)
# Nora · SpainRoom 2025-10-18
import os, json, requests
from flask import Blueprint, request, jsonify, current_app

bp_push = Blueprint("push", __name__, url_prefix="/api/push")

# Clave del servidor FCM (Firebase Cloud Messaging)
FCM_SERVER_KEY = os.getenv("FCM_SERVER_KEY", "").strip()
FCM_ENDPOINT = "https://fcm.googleapis.com/fcm/send"

def send_push(token, title, body):
    """Envía una notificación push al dispositivo especificado."""
    if not FCM_SERVER_KEY:
        return {"ok": False, "error": "missing_FCM_server_key"}

    headers = {
        "Authorization": f"key={FCM_SERVER_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "to": token,
        "notification": {
            "title": title,
            "body": body,
        },
        "priority": "high",
    }

    try:
        r = requests.post(FCM_ENDPOINT, headers=headers, json=payload, timeout=10)
        current_app.logger.info(f"[PUSH SEND] code={r.status_code} resp={r.text}")
        return {"ok": (200 <= r.status_code < 300), "status": r.status_code, "response": r.text}
    except Exception as e:
        current_app.logger.error(f"[PUSH ERROR] {e}")
        return {"ok": False, "error": str(e)}

# === Endpoint para registrar tokens ===
@bp_push.route("/register", methods=["POST", "OPTIONS"])
def register_token():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True)
    token = data.get("token")
    user_id = data.get("user_id", "")
    if not token:
        return jsonify(ok=False, error="missing_token"), 400

    # (aquí se podría guardar el token en DB si queremos)
    current_app.logger.info(f"[PUSH REGISTER] user={user_id} token={token}")
    return jsonify(ok=True, message="token registrado")

# === Endpoint para enviar notificaciones ===
@bp_push.route("/send", methods=["POST", "OPTIONS"])
def push_send():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True)
    token = data.get("token")
    title = data.get("title", "SpainRoom")
    body = data.get("body", "")
    result = send_push(token, title, body)
    return jsonify(result)
