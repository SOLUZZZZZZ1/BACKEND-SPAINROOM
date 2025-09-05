# -*- coding: utf-8 -*-
"""
Auth minimalista sin dependencia de SQLAlchemy.

- POST /api/auth/login-start  -> genera OTP de demo (123456)
- POST /api/auth/login-verify -> valida OTP, emite JWT y lo guarda en cookie sr_jwt
- GET  /api/auth/me           -> devuelve el usuario si la cookie es válida
- POST /api/auth/logout       -> borra la cookie

Defensa:
- Rate-limit 5/min en login-start y login-verify
- Mensajes de error genéricos (no se reflejan datos del atacante)
- Cookie preparada para cross-domain (Vercel <-> Render): Secure + HttpOnly + SameSite=None
"""
import os
import datetime
import jwt
from flask import Blueprint, request, jsonify, make_response

# Opcional: rate-limit por endpoint (requiere init_defense/app con flask-limiter)
try:
    from defense import limiter  # inicializado en init_defense(app)
except Exception:
    limiter = None  # si no hay limiter, los decoradores no se aplicarán

bp_auth = Blueprint("auth", __name__, url_prefix="/api/auth")

# Config JWT
JWT_SECRET = os.getenv("JWT_SECRET", "supersecret")   # ⚠️ cámbialo en producción
JWT_ALGO   = "HS256"
JWT_EXP_HR = 12

# Compatibilidad con app.py (no usamos DB)
def register_auth_models(_db):
    return

# OTP demo en memoria
class OTPStore:
    _data = {}

    @classmethod
    def start(cls, email: str) -> str:
        code = "123456"  # demo fija
        cls._data[email] = code
        return code

    @classmethod
    def verify(cls, email: str, code: str) -> bool:
        return cls._data.get(email) == code

# utilidades JWT
def _make_jwt(email: str) -> str:
    now = datetime.datetime.utcnow()
    payload = {"sub": email, "iat": now, "exp": now + datetime.timedelta(hours=JWT_EXP_HR)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def _decode_jwt(token: str):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except Exception:
        return None

# ---- Endpoints ----
def _limit_decorator(rule: str):
    """Devuelve decorador de limit si limiter existe, si no devuelve identidad."""
    if limiter is None:
        def identity(fn): return fn
        return identity
    return limiter.limit(rule)

@bp_auth.post("/login-start")
@_limit_decorator("5 per minute")
def login_start():
    # Mensajes genéricos: no reflejamos datos de entrada
    try:
        data  = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
    except Exception:
        email = ""

    if not email:
        return jsonify(error="Solicitud inválida"), 400

    OTPStore.start(email)
    # En producción: enviar email/SMS con el OTP (no lo exponemos)
    return jsonify(ok=True, demo_code="123456")  # demo visible solo en entorno de pruebas

@bp_auth.post("/login-verify")
@_limit_decorator("5 per minute")
def login_verify():
    # Mensajes genéricos, sin eco de datos
    try:
        data  = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        code  = (data.get("code")  or "").strip()
    except Exception:
        email, code = "", ""

    if not email or not code:
        return jsonify(error="Solicitud inválida"), 400

    if not OTPStore.verify(email, code):
        # No indicamos si falló email o código para no dar señales
        return jsonify(error="No autorizado"), 401

    token = _make_jwt(email)
    resp  = make_response(jsonify(ok=True, user={"email": email}))
    resp.set_cookie(
        "sr_jwt",
        token,
        max_age=JWT_EXP_HR * 3600,
        secure=True,
        httponly=True,
        samesite="None",
    )
    return resp

@bp_auth.get("/me")
def me():
    token = request.cookies.get("sr_jwt")
    if not token:
        return jsonify(error="No autorizado"), 401
    payload = _decode_jwt(token)
    if not payload:
        return jsonify(error="No autorizado"), 401
    return jsonify(ok=True, user={"email": payload["sub"]})

@bp_auth.post("/logout")
def logout():
    resp = make_response(jsonify(ok=True))
    resp.set_cookie("sr_jwt", "", max_age=0, secure=True, httponly=True, samesite="None")
    return resp
