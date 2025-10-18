import os, time, secrets, requests
from flask import Blueprint, request, jsonify, current_app

bp_push = Blueprint("push", __name__, url_prefix="/api/push")
FCM_SERVER_KEY = os.getenv("FCM_SERVER_KEY","").strip()

# Sustituir por tu modelo SQLAlchemy (aquí dict para demo)
USER_TOKENS = {}  # { user_id: set(tokens) }
PENDING_OTP = {}  # { otp_id: {"user_id","code","exp"} }

@bp_push.post("/register")
def register_token():
    data = request.get_json(force=True) or {}
    user_id   = (data.get("user_id") or "").strip()
    fcm_token = (data.get("fcm_token") or "").strip()
    platform  = (data.get("platform") or "web").strip()
    if not (user_id and fcm_token): return jsonify(ok=False, error="missing"), 400
    USER_TOKENS.setdefault(user_id, set()).add(fcm_token)
    current_app.logger.info("FCM token registrado user=%s platform=%s", user_id, platform)
    return jsonify(ok=True)

def _fcm_send(token: str, title: str, body: str, data=None):
    if not FCM_SERVER_KEY: return {"ok":False, "error":"missing FCM_SERVER_KEY"}
    headers = {"Authorization": f"key={FCM_SERVER_KEY}", "Content-Type": "application/json"}
    payload = {"to": token, "notification": {"title": title, "body": body}, "data": data or {}}
    r = requests.post("https://fcm.googleapis.com/fcm/send", json=payload, headers=headers, timeout=10)
    return {"status": r.status_code, "resp": r.text}

@bp_push.post("/send")
def push_send():
    data = request.get_json(force=True) or {}
    user_id = (data.get("user_id") or "").strip()
    title   = data.get("title") or "SpainRoom"
    body    = data.get("body") or "Mensaje de prueba"
    extra   = data.get("data") or {}
    results = []
    for t in USER_TOKENS.get(user_id, []):
        results.append(_fcm_send(t, title, body, extra))
    return jsonify(ok=True, results=results)

@bp_push.post("/login/request")
def push_login_request():
    data = request.get_json(force=True) or {}
    user_id = (data.get("user_id") or "").strip()
    if not user_id: return jsonify(ok=False, error="missing_user"), 400
    code  = f"{secrets.randbelow(900000)+100000}"
    otp_id = secrets.token_urlsafe(12)
    PENDING_OTP[otp_id] = {"user_id": user_id, "code": code, "exp": time.time()+300}
    for t in USER_TOKENS.get(user_id, []):
        _fcm_send(t, "Código de acceso", f"Tu código es {code}", {"type":"otp","otp_id":otp_id,"code":code})
    return jsonify(ok=True, otp_id=otp_id)

@bp_push.post("/login/verify")
def push_login_verify():
    data = request.get_json(force=True) or {}
    otp_id = data.get("otp_id"); code = data.get("code")
    rec = PENDING_OTP.get(otp_id)
    if not rec or time.time() > rec["exp"]:
        return jsonify(ok=False, error="expired"), 400
    if str(code) != str(rec["code"]):
        return jsonify(ok=False, error="invalid"), 401
    PENDING_OTP.pop(otp_id, None)
    return jsonify(ok=True, token="DEMO_JWT", user={"id": rec["user_id"]})
