# routes_auth.py
import os, re, hmac, hashlib, time, random, jwt
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, current_app
from app import db
from models_auth import AuthUser, AuthOtp

bp_auth = Blueprint("auth", __name__)

JWT_SECRET  = os.getenv("JWT_SECRET", "sr-dev-jwt")
JWT_TTL_MIN = int(os.getenv("JWT_TTL_MIN", "720"))
OTP_HASH_KEY= (os.getenv("OTP_HASH_KEY") or "sr-otp-key").encode("utf-8")

def norm_phone(p: str) -> str:
    p = re.sub(r"[^\d+]", "", p or "")
    if p.startswith("+"): return p
    if p.startswith("34"): return "+"+p
    if re.fullmatch(r"\d{9,15}", p): return "+34"+p
    return p

def hash_code(code: str) -> str:
    return hmac.new(OTP_HASH_KEY, code.encode("utf-8"), hashlib.sha256).hexdigest()

def make_jwt(user: AuthUser) -> str:
    payload = {
        "sub": user.phone,
        "role": user.role,
        "iat": int(time.time()),
        "exp": int(time.time() + JWT_TTL_MIN*60)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def send_sms(phone: str, text: str) -> bool:
    sid   = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_ = os.getenv("TWILIO_FROM")
    if not (sid and token and from_):
        print(f"[OTP] (SIMULADO) a {phone}: {text}")
        return True
    try:
        from twilio.rest import Client
        Client(sid, token).messages.create(body=text, from_=from_, to=phone)
        return True
    except Exception as e:
        print("[OTP] Twilio error:", e)
        return False

# Rate limit básico (memoria)
_rl = {}
def rate_limit(key: str, per_sec: float) -> bool:
    now = time.time()
    last = _rl.get(key, 0)
    if now - last < per_sec:
        return False
    _rl[key] = now
    return True

@bp_auth.post("/api/auth/request-otp")
def request_otp():
    data = request.get_json(silent=True) or {}
    phone = norm_phone(data.get("phone",""))
    if not re.fullmatch(r"\+\d{9,15}", phone):
        return jsonify(ok=False, error="bad_phone"), 400

    if not rate_limit(f"otp:{phone}", per_sec=5.0):
        return jsonify(ok=False, error="rate_limited"), 429

    code = f"{random.randint(0, 999999):06d}"
    code_h = hash_code(code)

    # Lock básico por demasiados intentos
    otp = AuthOtp.query.filter_by(phone=phone).order_by(AuthOtp.id.desc()).first()
    if otp and otp.locked_until and otp.locked_until > datetime.utcnow():
        return jsonify(ok=False, error="locked"), 429

    # Guarda OTP
    record = AuthOtp(
        phone=phone,
        code_hash=code_h,
        expires_at=datetime.utcnow() + timedelta(minutes=5),
        attempts=0,
        locked_until=None,
    )
    db.session.add(record); db.session.commit()

    ok = send_sms(phone, f"Tu código SpainRoom: {code}")
    if not ok:
        return jsonify(ok=False, error="sms_failed"), 500

    hint = f"***{phone[-2:]}" if len(phone) >= 2 else ""
    return jsonify(ok=True, ttl=300, hint=hint)

@bp_auth.post("/api/auth/verify-otp")
def verify_otp():
    data = request.get_json(silent=True) or {}
    phone = norm_phone(data.get("phone",""))
    code  = str(data.get("code","")).strip()
    if not re.fullmatch(r"\+\d{9,15}", phone) or not re.fullmatch(r"\d{4,8}", code):
        return jsonify(ok=False, error="bad_input"), 400

    otp = AuthOtp.query.filter_by(phone=phone).order_by(AuthOtp.id.desc()).first()
    if not otp:
        return jsonify(ok=False, error="no_code"), 400

    now = datetime.utcnow()
    if otp.locked_until and otp.locked_until > now:
        return jsonify(ok=False, error="locked"), 429
    if otp.expires_at < now:
        return jsonify(ok=False, error="expired"), 400

    if otp.code_hash != hash_code(code):
        otp.attempts += 1
        if otp.attempts >= 5:
            otp.locked_until = now + timedelta(minutes=10)
        db.session.commit()
        return jsonify(ok=False, error="invalid_code"), 400

    # OTP correcto -> emitir JWT y crear usuario si no existe
    user = AuthUser.query.filter_by(phone=phone).first()
    if not user:
        user = AuthUser(phone=phone, role="user")
        db.session.add(user); db.session.commit()

    token = make_jwt(user)
    return jsonify(ok=True, token=token, user={"phone": user.phone, "role": user.role})

@bp_auth.get("/api/auth/me")
def me():
    # Demo: NO valida JWT aún; añade validación si quieres
    return jsonify(ok=True, msg="Añade validación JWT aquí")
