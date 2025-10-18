# routes_push.py — SpainRoom Push (Firebase FCM HTTP v1) — definitivo
# Nora · 2025-10-18
import os, time, secrets
from flask import Blueprint, request, jsonify, current_app

# FCM HTTP v1 (OAuth con cuenta de servicio)
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

bp_push = Blueprint("push", __name__, url_prefix="/api/push")

# === Configuración FCM v1 ===
PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "spainroom-9cb27").strip()
SCOPES     = ["https://www.googleapis.com/auth/firebase.messaging"]
CREDS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "serviceAccountKey.json").strip()

# Crea sesión autorizada (OAuth) con credenciales de servicio
CREDS   = service_account.Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
SESSION = AuthorizedSession(CREDS)

# === Almacenes en memoria (sustituir por DB en producción) ===
USER_TOKENS = {}   # { user_id: set([token,...]) }
PENDING_OTP = {}   # { otp_id: {user_id, code, exp} }

def fcm_send_v1(token: str, title: str, body: str, data=None):
    """Envía push usando FCM HTTP v1 (notificación + data)."""
    url = f"https://fcm.googleapis.com/v1/projects/{PROJECT_ID}/messages:send"
    payload = {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "data": data or {}
        }
    }
    r = SESSION.post(url, json=payload, timeout=10)
    current_app.logger.info("[PUSH V1] code=%s resp=%s", r.status_code, r.text[:400])
    return {"ok": (200 <= r.status_code < 300), "status": r.status_code, "resp": r.text}

@bp_push.route("/register", methods=["POST","OPTIONS"])
def register_token():
    """Registra token FCM de un usuario: { user_id, token|fcm_token, platform }"""
    if request.method == "OPTIONS": return ("", 204)
    data = request.get_json(force=True) or {}
    token   = (data.get("token") or data.get("fcm_token") or "").strip()
    user_id = (data.get("user_id") or "").strip()
    platform= (data.get("platform") or "web").strip()
    if not token:   return jsonify(ok=False, error="missing_token"), 400
    if not user_id: return jsonify(ok=False, error="missing_user_id"), 400
    USER_TOKENS.setdefault(user_id, set()).add(token)
    current_app.logger.info("[PUSH REGISTER] user=%s platform=%s tokens=%d",
                            user_id, platform, len(USER_TOKENS[user_id]))
    return jsonify(ok=True, tokens=len(USER_TOKENS[user_id]))

@bp_push.route("/send", methods=["POST","OPTIONS"])
def push_send():
    """
    Envía notificación:
      - a un token: { token, title, body, data? }
      - a todos los tokens de un usuario: { user_id, title, body, data? }
    """
    if request.method == "OPTIONS": return ("", 204)
    data    = request.get_json(force=True) or {}
    token   = (data.get("token") or "").strip()
    user_id = (data.get("user_id") or "").strip()
    title   = data.get("title") or "SpainRoom"
    body    = data.get("body")  or "Mensaje"
    extra   = data.get("data")  or {}

    results = []
    if token:
        results.append(fcm_send_v1(token, title, body, extra))
    elif user_id and user_id in USER_TOKENS:
        for t in list(USER_TOKENS[user_id]):
            results.append(fcm_send_v1(t, title, body, extra))
    else:
        return jsonify(ok=False, error="missing_target"), 400

    return jsonify(ok=True, results=results))

@bp_push.route("/login/request", methods=["POST","OPTIONS"])
def push_login_request():
    """Genera OTP 6 dígitos → envía push a todos los tokens del user: { user_id }"""
    if request.method == "OPTIONS": return ("", 204)
    data    = request.get_json(force=True) or {}
    user_id = (data.get("user_id") or "").strip()
    if not user_id: return jsonify(ok=False, error="missing_user_id"), 400

    code   = f"{secrets.randbelow(900000)+100000}"
    otp_id = secrets.token_urlsafe(12)
    PENDING_OTP[otp_id] = {"user_id": user_id, "code": code, "exp": time.time()+300}

    results = []
    for t in USER_TOKENS.get(user_id, []):
        results.append(fcm_send_v1(t, "Código de acceso", f"Tu código es {code}",
                                   {"type":"otp","otp_id":otp_id}))
    return jsonify(ok=True, otp_id=otp_id, results=results)

@bp_push.route("/login/verify", methods=["POST","OPTIONS"])
def push_login_verify():
    """Verifica OTP: { otp_id, code } → devuelve token demo y user"""
    if request.method == "OPTIONS": return ("", 204)
    data  = request.get_json(force=True) or {}
    otp_id= data.get("otp_id"); code = data.get("code")
    rec   = PENDING_OTP.get(otp_id)
    if not rec or time.time() > rec["exp"]:
        return jsonify(ok=False, error="expired"), 400
    if str(code) != str(rec["code"]):
        return jsonify(ok=False, error="invalid"), 401

    PENDING_OTP.pop(otp_id, None)
    # TODO: emitir tu JWT real aquí
    return jsonify(ok=True, token="DEMO_JWT", user={"id": rec["user_id"]})
