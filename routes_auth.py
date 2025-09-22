# routes_auth.py
import os, re
from datetime import datetime, timedelta
import jwt
from flask import Blueprint, request, jsonify
from extensions import db
from models_auth import User, Otp  # <— nombres reales de tu modelo

bp_auth = Blueprint("auth", __name__)

JWT_SECRET  = os.getenv("JWT_SECRET", "sr-dev-secret")
JWT_TTL_MIN = int(os.getenv("JWT_TTL_MIN", "720"))
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "ramon")
PHONE_RE = re.compile(r"^\+?\d{9,15}$")

def make_jwt(user: User):
  payload = {
    "sub": f"user:{user.id}", "uid": user.id,
    "role": user.role, "name": user.name or "",
    "phone": user.phone or "", "email": user.email or "",
    "exp": datetime.utcnow() + timedelta(minutes=JWT_TTL_MIN),
    "iat": datetime.utcnow(), "iss": "spainroom"
  }
  return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

@bp_auth.post("/api/auth/create_user")
def create_user():
  if (request.headers.get("X-Admin-Key") or "") != ADMIN_API_KEY:
    return jsonify(ok=False, error="forbidden"), 403
  data = request.get_json(force=True)
  role = (data.get("role") or "inquilino").strip()
  phone = (data.get("phone") or "").strip()
  email = (data.get("email") or "").strip().lower() or None
  name  = (data.get("name") or "").strip() or None
  if not phone and not email: return jsonify(ok=False, error="need_phone_or_email"), 400
  if phone and not PHONE_RE.match(phone): return jsonify(ok=False, error="bad_phone"), 400

  u = None
  if phone: u = User.query.filter_by(phone=phone).first()
  if not u and email: u = User.query.filter_by(email=email).first()
  if not u:
    u = User(phone=phone or None, email=email or None, role=role, name=name)
    db.session.add(u); db.session.commit()
  else:
    u.role = role or u.role; u.name = name or u.name; db.session.commit()
  return jsonify(ok=True, user=u.to_dict())

@bp_auth.post("/api/auth/request_otp")
def request_otp():
  data = request.get_json(force=True)
  phone = (data.get("phone") or "").strip()
  email = (data.get("email") or "").strip().lower()
  target = phone or email
  if not target: return jsonify(ok=False, error="need_phone_or_email"), 400
  if phone and not PHONE_RE.match(phone): return jsonify(ok=False, error="bad_phone"), 400

  if phone:
    u = User.query.filter_by(phone=phone).first()
    if not u: u = User(phone=phone, role="inquilino"); db.session.add(u); db.session.commit()
  else:
    u = User.query.filter_by(email=email).first()
    if not u: u = User(email=email, role="inquilino"); db.session.add(u); db.session.commit()

  otp = Otp.new(target, ttl_sec=300)
  db.session.add(otp); db.session.commit()
  # Envío real de SMS/email: integrar proveedor. Aquí no devolvemos el code por seguridad.
  return jsonify(ok=True, sent=True)

@bp_auth.post("/api/auth/verify_otp")
def verify_otp():
  data = request.get_json(force=True)
  phone = (data.get("phone") or "").strip()
  email = (data.get("email") or "").strip().lower()
  code  = (data.get("code") or "").strip()
  target= phone or email
  if not target or not code: return jsonify(ok=False, error="missing_fields"), 400

  otp = (Otp.query.filter_by(target=target, used=False).order_by(Otp.created_at.desc()).first())
  if not otp: return jsonify(ok=False, error="otp_not_found"), 404
  if datetime.utcnow() > otp.expires_at: return jsonify(ok=False, error="otp_expired"), 400
  otp.tries += 1
  if otp.code != code:
    db.session.commit()
    return jsonify(ok=False, error="otp_mismatch"), 400

  otp.used = True; db.session.commit()

  if phone:
    u = User.query.filter_by(phone=phone).first()
  else:
    u = User.query.filter_by(email=email).first()
  if not u:
    u = User(phone=phone or None, email=email or None, role="inquilino")
    db.session.add(u); db.session.commit()

  token = make_jwt(u)
  return jsonify(ok=True, token=token, user=u.to_dict())
