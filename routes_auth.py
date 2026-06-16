# routes_auth.py — SpainRoom Auth (OTP + Password Link + Password Login)
import os, re, time, random
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, current_app
from werkzeug.security import generate_password_hash, check_password_hash
import jwt

from extensions import db
from models_auth import User, Otp

bp_auth = Blueprint("auth", __name__)

# ---------- Config ----------
JWT_SECRET  = os.getenv("JWT_SECRET", "sr-dev-secret")
JWT_TTL_MIN = int(os.getenv("JWT_TTL_MIN", "720"))
PASSLINK_TTL_MIN = int(os.getenv("PASSLINK_TTL_MIN", "15"))
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "ramon")

PHONE_RE = re.compile(r"^\+?\d{9,15}$")

def normalize_phone(v: str) -> str:
    s = re.sub(r"[^\d+]", "", v or "")
    if not s:
        return ""
    if s.startswith("+"):
        return s
    if s.startswith("34"):
        return "+" + s
    if re.fullmatch(r"\d{9,15}", s):
        return "+34" + s
    return s

def _mask_phone(phone: str) -> str:
    p = phone or ""
    if len(p) <= 5:
        return p
    return p[:4] + "***" + p[-3:]

# ---------- JWT helpers ----------
def make_jwt(user: User):
    payload = {
        "sub": f"user:{user.id}",
        "uid": user.id,
        "role": user.role,
        "name": user.name or "",
        "phone": user.phone or "",
        "email": user.email or "",
        "exp": datetime.utcnow() + timedelta(minutes=JWT_TTL_MIN),
        "iat": datetime.utcnow(),
        "iss": "spainroom",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def _make_passlink_token(phone: str):
    payload = {
        "sub": f"passlink:{phone}",
        "phone": phone,
        "exp": int(time.time()) + PASSLINK_TTL_MIN * 60,
        "iat": int(time.time()),
        "iss": "spainroom",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

# ---------- Twilio SMS ----------
def _twilio_client_or_none():
    try:
        from twilio.rest import Client
    except Exception:
        return None, None, None
    sid = os.getenv("TWILIO_ACCOUNT_SID") or ""
    tok = os.getenv("TWILIO_AUTH_TOKEN") or ""
    frm = os.getenv("TWILIO_PHONE_NUMBER") or ""
    if not (sid and tok and frm):
        return None, None, None
    return Client(sid, tok), frm, sid

def send_sms(phone_to: str, body: str) -> bool:
    try:
        client, from_number, _ = _twilio_client_or_none()
        if not client or not from_number:
            current_app.logger.warning("[SMS] Twilio no configurado; body=%s", body)
            return False
        m = client.messages.create(body=body, from_=from_number, to=phone_to)
        current_app.logger.info("[SMS] enviado sid=%s to=%s", getattr(m, "sid", "?"), _mask_phone(phone_to))
        return True
    except Exception as e:
        current_app.logger.warning("[SMS] fallo enviando: %s", e)
        return False

# ---------- Endpoints ----------
@bp_auth.get("/api/auth/me")
def me():
    return jsonify(ok=True, message="Attach a JWT parser here if needed")

@bp_auth.post("/api/auth/create_user")
def create_user():
    if (request.headers.get("X-Admin-Key") or "") != ADMIN_API_KEY:
        return jsonify(ok=False, error="forbidden"), 403

    data = request.get_json(force=True)
    role = (data.get("role") or "inquilino").strip()
    phone = normalize_phone(data.get("phone") or "")
    email = (data.get("email") or "").strip().lower() or None
    name  = (data.get("name") or "").strip() or None

    if not phone and not email:
        return jsonify(ok=False, error="need_phone_or_email"), 400
    if phone and not PHONE_RE.match(phone):
        return jsonify(ok=False, error="bad_phone"), 400

    u = None
    if phone:
        u = User.query.filter_by(phone=phone).first()
    if not u and email:
        u = User.query.filter_by(email=email).first()

    if not u:
        u = User(phone=phone or None, email=email or None, role=role, name=name)
        db.session.add(u)
        db.session.commit()
    else:
        u.role = role or u.role
        u.name = name or u.name
        if email and not u.email:
            u.email = email
        db.session.commit()

    return jsonify(ok=True, user=u.to_dict())


@bp_auth.get("/api/auth/users")
def list_users():
    """
    Lista usuarios reales de auth_user para Centro de Control.
    Protegido por X-Admin-Key.
    """
    if (request.headers.get("X-Admin-Key") or "") != ADMIN_API_KEY:
        return jsonify(ok=False, error="forbidden"), 403

    q = (request.args.get("q") or "").strip().lower()
    role = (request.args.get("role") or "").strip().lower()

    try:
        limit = min(int(request.args.get("limit") or 100), 500)
    except Exception:
        limit = 100

    query = User.query

    if role:
        query = query.filter(User.role == role)

    rows = query.order_by(User.created_at.desc()).limit(limit).all()

    users = []
    for u in rows:
        d = u.to_dict()
        d["created_at"] = u.created_at.isoformat() if getattr(u, "created_at", None) else None
        d["has_password"] = bool(getattr(u, "password_hash", None))
        users.append(d)

    if q:
        users = [
            u for u in users
            if q in str(u.get("name") or "").lower()
            or q in str(u.get("phone") or "").lower()
            or q in str(u.get("email") or "").lower()
            or q in str(u.get("role") or "").lower()
        ]

    return jsonify(ok=True, users=users)

# ----- OTP por SMS -----
@bp_auth.post("/api/auth/request_otp")
def request_otp():
    data = request.get_json(force=True)
    phone = normalize_phone(data.get("phone") or "")
    email = (data.get("email") or "").strip().lower()
    target = phone or email

    if not target:
        return jsonify(ok=False, error="need_phone_or_email"), 400
    if phone and not PHONE_RE.match(phone):
        return jsonify(ok=False, error="bad_phone"), 400

    try:
        if phone:
            u = User.query.filter_by(phone=phone).first()
        else:
            u = User.query.filter_by(email=email).first()

        if not u:
            return jsonify(
                ok=False,
                error="user_not_found",
                message="Tu cuenta debe ser creada previamente por SpainRoom."
            ), 404
    except Exception as e:
        current_app.logger.exception("[OTP] fallo preparando usuario: %s", e)
        return jsonify(ok=False, error="server_error_user"), 500

    code = None
    try:
        otp = Otp.new(target, ttl_sec=300)
        code = otp.code
        db.session.add(otp)
        db.session.commit()
    except Exception as e:
        current_app.logger.exception("[OTP] fallo creando OTP: %s", e)
        code = f"{random.randint(0, 999999):06d}"
        current_app.logger.warning("[OTP] usando fallback code no persistido")

    sent = False
    try:
        if phone and code:
            sent = send_sms(phone, f"SpainRoom: tu código es {code}. Caduca en 5 min.")
    except Exception as e:
        current_app.logger.warning("[OTP] fallo enviando SMS: %s", e)

    current_app.logger.info("[OTP] solicitado para %s (sent=%s)", _mask_phone(target), sent)
    return jsonify(ok=True, sent=bool(sent))

@bp_auth.post("/api/auth/verify_otp")
def verify_otp():
    data = request.get_json(force=True)
    phone = normalize_phone(data.get("phone") or "")
    email = (data.get("email") or "").strip().lower()
    code  = (data.get("code") or "").strip()
    target = phone or email

    if not target or not code:
        return jsonify(ok=False, error="missing_fields"), 400

    otp = Otp.query.filter_by(target=target, used=False).order_by(Otp.created_at.desc()).first()
    if not otp:
        return jsonify(ok=False, error="otp_not_found"), 404
    if datetime.utcnow() > otp.expires_at:
        return jsonify(ok=False, error="otp_expired"), 400

    otp.tries += 1
    if otp.code != code:
        db.session.commit()
        return jsonify(ok=False, error="otp_mismatch"), 400

    otp.used = True
    db.session.commit()

    if phone:
        u = User.query.filter_by(phone=phone).first()
    else:
        u = User.query.filter_by(email=email).first()

    if not u:
        return jsonify(
            ok=False,
            error="user_not_found",
            message="Tu cuenta debe ser creada previamente por SpainRoom."
        ), 404

    token = make_jwt(u)
    return jsonify(ok=True, token=token, user=u.to_dict())

# ----- Enlace para crear/recuperar contraseña -----
@bp_auth.post("/api/auth/request_password_link")
def request_password_link():
    data = request.get_json(force=True)
    phone = normalize_phone(data.get("phone") or "")

    if not phone:
        return jsonify(ok=False, error="missing_phone"), 400
    if not PHONE_RE.match(phone):
        return jsonify(ok=False, error="bad_phone"), 400

    u = User.query.filter_by(phone=phone).first()
    if not u:
        return jsonify(
            ok=False,
            error="user_not_found",
            message="Tu cuenta debe ser creada previamente por SpainRoom."
        ), 404

    token = _make_passlink_token(phone)
    link = f"{FRONTEND_BASE_URL}/set-password?token={token}"

    ok = False
    try:
        ok = send_sms(phone, f"SpainRoom: crea o recupera tu contraseña aquí: {link}")
    except Exception as e:
        current_app.logger.warning("[AUTH] fallo enviando passlink: %s", e)

    if not ok:
        current_app.logger.warning("[AUTH] Twilio no configurado o fallo; passlink demo -> %s", link)
        return jsonify(ok=True, demo=True, link=link)

    return jsonify(ok=True, sent=True)

@bp_auth.post("/api/auth/set_password")
def set_password():
    data = request.get_json(force=True)
    token = (data.get("token") or "").strip()
    newpass = (data.get("password") or "").strip()

    if not token or not newpass:
        return jsonify(ok=False, error="missing_fields"), 400
    if len(newpass) < 6:
        return jsonify(ok=False, error="password_too_short"), 400

    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "iss"]},
        )
        sub = str(payload.get("sub", ""))
        if not sub.startswith("passlink:"):
            return jsonify(ok=False, error="bad_token"), 400
        phone = normalize_phone(payload.get("phone") or "")
        if not phone:
            return jsonify(ok=False, error="bad_token"), 400
    except Exception as e:
        return jsonify(ok=False, error="invalid_or_expired", message=str(e)), 400

    u = User.query.filter_by(phone=phone).first()
    if not u:
        return jsonify(ok=False, error="user_not_found"), 404

    try:
        u.password_hash = generate_password_hash(newpass)
        db.session.commit()
        current_app.logger.info("[AUTH] password set user_id=%s phone=%s has_hash=%s", u.id, _mask_phone(phone), bool(u.password_hash))
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("[AUTH] set_password failed: %s", e)
        return jsonify(ok=False, error="set_password_failed", message=str(e)), 500

    return jsonify(ok=True, user_id=u.id, phone=phone, has_password=bool(u.password_hash))

# ----- Login con móvil + contraseña -----
@bp_auth.post("/api/auth/login_password")
def login_password():
    data = request.get_json(force=True)
    phone = normalize_phone(data.get("phone") or "")
    pw = (data.get("password") or "").strip()

    if not phone or not pw:
        return jsonify(ok=False, error="missing_fields"), 400

    u = User.query.filter_by(phone=phone).first()

    if not u:
        current_app.logger.info("[AUTH] login no_user phone=%s", _mask_phone(phone))
        return jsonify(ok=False, error="user_not_found"), 404

    has_hash = bool(getattr(u, "password_hash", None))
    current_app.logger.info("[AUTH] login user_id=%s phone=%s has_hash=%s role=%s", u.id, _mask_phone(phone), has_hash, u.role)

    if not has_hash:
        return jsonify(ok=False, error="no_password_set"), 400

    if not check_password_hash(u.password_hash, pw):
        return jsonify(ok=False, error="bad_credentials"), 401

    token = make_jwt(u)
    return jsonify(ok=True, token=token, user=u.to_dict())
