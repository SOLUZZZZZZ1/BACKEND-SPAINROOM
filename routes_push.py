# routes_push.py — SpainRoom Push (Firebase FCM) — definitivo
import os, time, secrets, requests
from flask import Blueprint, request, jsonify, current_app

bp_push = Blueprint("push", __name__, url_prefix="/api/push")

FCM_SERVER_KEY = os.getenv("FCM_SERVER_KEY", "").strip()
FCM_ENDPOINT   = "https://fcm.googleapis.com/fcm/send"

USER_TOKENS = {}   # { user_id: set([token,...]) }
PENDING_OTP = {}   # { otp_id: {user_id, code, exp} }

def _fcm_send(token: str, title: str, body: str, data=None):
    if not FCM_SERVER_KEY:
        return {"ok": False, "error": "missing_FCM_SERVER_KEY"}
    headers = {"Authorization": f"key={FCM_SERVER_KEY}", "Content-Type": "application/json"}
    payload = {
        "to": token,
        "priority": "high",
        "notification": {"title": title, "body": body},
        "data": data or {}
    }
    r = requests.post(FCM_ENDPOINT, json=payload, headers=headers, timeout=10)
    current_app.logger.info("[PUSH SEND] code=%s resp=%s", r.status_code, r.text[:400])
    return {"ok": (200 <= r.status_code < 300), "status": r.status_code, "resp": r.text}

@bp_push.route("/register", methods=["POST","OPTIONS"])
def register_token():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True) or {}
    token   = (data.get("token") or data.get("fcm_token") or "").strip()
    user_id = (data.get("user_id") or "").strip()
    platform= (data.get("platform") or "web").strip()
    if not token:   return jsonify(ok=False, error="missing_token"), 400
    if not user_id: return jsonify(ok=False, error="missing_user_id"), 400
    USER_TOKENS.setdefault(user_id, set()).add(token)
    current_app.logger.info("[PUSH REGISTER] user=%s platform=%s tokens=%d", user_id, platform, len(USER_TOKENS[user_id]))
    return jsonify(ok=True, tokens=len(USER_TOKENS[user_id]))

@bp_push.route("/send", methods=["POST","OPTIONS"])
def push_send():
    if request.method == "OPTIONS":
        return ("", 204)
    data    = request.get_json(force=True) or {}
    token   = (data.get("token") or "").strip()
    user_id = (data.get("user_id") or "").strip()
    title   = data.get("title") or "SpainRoom"
    body    = data.get("body")  or "Mensaje"
    extra   = data.get("data")  or {}
    results = []
    if token:
        results.append(_fcm_send(token, title, body, extra))
    elif user_id and user_id in USER_TOKENS:
        for t in list(USER_TOKENS[user_id]):
            results.append(_fcm_send(t, title, body, extra))
    else:
        return jsonify(ok=False, error="missing_target"), 400
    return jsonify(ok=True, results=results)

@bp_push.route("/login/request", methods=["POST","OPTIONS"])
def push_login_request():
    if request.method == "OPTIONS":
        return ("", 204)
    data    = request.get_json(force=True) or {}
    user_id = (data.get("user_id") or "").strip()
    if not user_id:
        return jsonify(ok=False, error="missing_user_id"), 400
    code  = f"{secrets.randbelow(900000)+100000}"
    otp_id = secrets.token_urlsafe(12)
    PENDING_OTP[otp_id] = {"user_id": user_id, "code": code, "exp": time.time()+300}
    sent = []
    for t in USER_TOKENS.get(user_id, []):
        sent.append(_fcm_send(t, "Código de acceso", f"Tu código es {code}", {"type":"otp","otp_id":otp_id}))
    return jsonify(ok=True, otp_id=otp_id, results=sent)

@bp_push.route("/login/verify", methods=["POST","OPTIONS"])
def push_login_verify():
    if request.method == "OPTIONS":
        return ("", 204)
    data  = request.get_json(force=True) or {}
    otp_id= data.get("otp_id")
    code  = data.get("code")
    rec   = PENDING_OTP.get(otp_id)
    if not rec or time.time() > rec["exp"]:
        return jsonify(ok=False, error="expired"), 400
    if str(code) != str(rec["code"]):
        return jsonify(ok=False, error="invalid"), 401
    PENDING_OTP.pop(otp_id, None)
    return jsonify(ok=True, token="DEMO_JWT", user={"id": rec["user_id"]})
