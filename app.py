# app.py — SpainRoom BACKEND (API) — completo con registros de blueprints
"""
Flask + SQLAlchemy + CORS + logging + health.

Blueprints esperados:
  /api/auth/*        -> routes_auth.bp_auth
  /api/contacto/*    -> routes_contact.bp_contact
  /api/contracts/*   -> routes_contracts.bp_contracts
  /api/rooms/*       -> routes_rooms.bp_rooms
  /api/rooms/*       -> routes_uploads_rooms.bp_upload_rooms
  /api/upload        -> routes_upload_generic.bp_upload_generic
  /api/franchise/*   -> routes_franchise.bp_franchise
  /api/kyc/*         -> routes_kyc.bp_kyc
  /api/payments/*    -> payments.bp_payments
  /sms/*             -> routes_sms.bp_sms
  /api/owner/*       -> routes_owner_cedula.bp_owner
  /api/catastro/*    -> routes_catastro.bp_catastro   (si ENABLE_BP_CATASTRO=1)
Además: endpoints internos:
  /api/legal/requirement
  /api/legal/cedula/check
  /api/admin/cedula/upsert
"""

import os, sys, types, logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from flask import Flask, jsonify, request, current_app
from flask_cors import CORS
from werkzeug.utils import secure_filename
from sqlalchemy import text

# ---------- DB bootstrap ----------
try:
    from extensions import db
except Exception:
    from flask_sqlalchemy import SQLAlchemy
    db = SQLAlchemy()
    mod = types.ModuleType("extensions"); mod.db = db; sys.modules["extensions"] = mod

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = f"sqlite:///{(BASE_DIR / 'spainroom.db').as_posix()}"

# Normalización robusta de URL para Postgres (Render) + SSL
SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", DEFAULT_DB)
_raw_db = os.environ.get("DATABASE_URL")
if _raw_db:
    if _raw_db.startswith("postgres://"):
        _raw_db = _raw_db.replace("postgres://", "postgresql+psycopg2://", 1)
    elif _raw_db.startswith("postgresql://"):
        _raw_db = _raw_db.replace("postgresql://", "postgresql+psycopg2://", 1)
    if "sslmode=" not in _raw_db and "+psycopg2://" in _raw_db:
        _raw_db += ("&" if "?" in _raw_db else "?") + "sslmode=require"
    SQLALCHEMY_DATABASE_URI = _raw_db

ENGINE_OPTIONS = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

JWT_SECRET  = os.environ.get("JWT_SECRET", os.environ.get("SECRET_KEY", "sr-dev-secret"))
JWT_TTL_MIN = int(os.environ.get("JWT_TTL_MIN", "720"))

def create_app(test_config=None):
    app = Flask(__name__, static_folder="public", static_url_path="/")
    try:
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
        Path(app.instance_path, "uploads").mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    app.config.update(
        SQLALCHEMY_DATABASE_URI=SQLALCHEMY_DATABASE_URI,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS=ENGINE_OPTIONS,
        JWT_SECRET=JWT_SECRET,
        JWT_TTL_MIN=JWT_TTL_MIN,
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
    _init_logging(app)

    # --------- Importar modelos/rutas ANTES de create_all ----------
    # (cada import va en try para no romper si falta un módulo)
    def _try_import(msg, fn):
        try:
            return fn()
        except Exception as e:
            app.logger.info(f"{msg} no disponible: {e}")
            return None

    bp_auth            = _try_import("auth",            lambda: __import__("routes_auth", fromlist=["bp_auth"]).bp_auth)
    bp_contact         = _try_import("contact",         lambda: __import__("routes_contact", fromlist=["bp_contact"]).bp_contact)
    bp_contracts       = _try_import("contracts",       lambda: __import__("routes_contracts", fromlist=["bp_contracts"]).bp_contracts)
    bp_rooms           = _try_import("rooms",           lambda: __import__("routes_rooms", fromlist=["bp_rooms"]).bp_rooms)
    bp_upload_rooms    = _try_import("upload_rooms",    lambda: __import__("routes_uploads_rooms", fromlist=["bp_upload_rooms"]).bp_upload_rooms)
    bp_upload_generic  = _try_import("upload_generic",  lambda: __import__("routes_upload_generic", fromlist=["bp_upload_generic"]).bp_upload_generic)
    bp_franchise       = _try_import("franchise",       lambda: __import__("routes_franchise", fromlist=["bp_franchise"]).bp_franchise)
    bp_kyc             = _try_import("kyc",             lambda: __import__("routes_kyc", fromlist=["bp_kyc"]).bp_kyc)
    bp_sms             = _try_import("sms",             lambda: __import__("routes_sms", fromlist=["bp_sms"]).bp_sms)
    bp_payments        = _try_import("payments",        lambda: __import__("payments", fromlist=["bp_payments"]).bp_payments)
    bp_owner           = _try_import("owner",           lambda: __import__("routes_owner_cedula", fromlist=["bp_owner"]).bp_owner)
    bp_upload_autofit  = _try_import("uploads_autofit", lambda: __import__("routes_uploads_rooms_autofit", fromlist=["bp_upload_rooms_autofit"]).bp_upload_rooms_autofit)

    # Catastro sólo si lo activas por ENV
    bp_catastro = None
    if os.environ.get("ENABLE_BP_CATASTRO") in {"1","true","True"}:
        bp_catastro = _try_import("catastro", lambda: __import__("routes_catastro", fromlist=["bp_catastro"]).bp_catastro)

    # create_all
    with app.app_context():
        try:
            # Si tienes modelos independientes, impórtalos aquí para que creen tablas
            __import__("models_auth"), __import__("models_contact"), __import__("models_contracts")
            __import__("models_rooms"), __import__("models_roomleads"), __import__("models_uploads")
            # __import__("models_kyc")  # si existe
        except Exception:
            pass
        db.create_all()
        app.logger.info("DB create_all() OK")

    # --------- Registrar blueprints con sus prefijos ---------
    if bp_auth:           app.register_blueprint(bp_auth,          url_prefix="/api/auth")
    if bp_contact:        app.register_blueprint(bp_contact,       url_prefix="/api/contacto")
    if bp_contracts:      app.register_blueprint(bp_contracts,     url_prefix="/api/contracts")
    if bp_rooms:          app.register_blueprint(bp_rooms,         url_prefix="/api/rooms")
    if bp_upload_rooms:   app.register_blueprint(bp_upload_rooms,  url_prefix="/api/rooms")
    if bp_upload_generic: app.register_blueprint(bp_upload_generic)  # define sus propias rutas
    if bp_franchise:      app.register_blueprint(bp_franchise,     url_prefix="/api/franchise")
    if bp_kyc:            app.register_blueprint(bp_kyc,           url_prefix="/api/kyc")
    if bp_payments:       app.register_blueprint(bp_payments,      url_prefix="/api/payments")
    if bp_sms:            app.register_blueprint(bp_sms,           url_prefix="/sms")
    if bp_owner:          app.register_blueprint(bp_owner,         url_prefix="/api/owner")
    if bp_upload_autofit: app.register_blueprint(bp_upload_autofit)  # si ya expone /api/rooms/*
    if bp_catastro:       app.register_blueprint(bp_catastro,      url_prefix="/api/catastro")

    # --------- CORS global (preflight friendly) ---------
    ALLOWED_ORIGINS = {
        "http://localhost:5176",
        "http://127.0.0.1:5176",
        # "https://tu-frontend.vercel.app",  # añade tu dominio si procede
    }
    @app.after_request
    def add_cors_headers(resp):
        origin = request.headers.get("Origin")
        if origin and (origin in ALLOWED_ORIGINS or origin.endswith(".vercel.app")):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, Authorization, X-Admin-Key, X-Franquiciado, Stripe-Signature"
            )
            resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
        return resp

    # --------- Endpoints internos (LEG/Owner mínimos) ---------
    @app.route("/api/owner/check", methods=["POST", "OPTIONS"])
    def owner_check():
        if request.method == "OPTIONS": return ("", 204)
        body = request.get_json(silent=True) or {}
        import uuid
        return jsonify(ok=True, id="SRV-CHK-" + uuid.uuid4().hex[:8], echo=body)

    @app.route("/api/owner/cedula/upload", methods=["POST", "OPTIONS"])
    def cedula_upload():
        if request.method == "OPTIONS": return ("", 204)
        f = request.files.get("file")
        if not f: return jsonify(ok=False, error="no_file"), 400
        filename = secure_filename(f.filename or "file")
        up = Path(current_app.instance_path) / "uploads"
        up.mkdir(parents=True, exist_ok=True)
        tgt = up / filename
        f.save(tgt)
        return jsonify(ok=True, filename=filename, size=tgt.stat().st_size)

    # Requisito legal por zona (lee legal_requirements)
    @app.route("/api/legal/requirement", methods=["POST", "OPTIONS"])
    def legal_requirement():
        if request.method == "OPTIONS": return ("", 204)
        b = request.get_json(silent=True) or {}
        municipio = (b.get("municipio") or "").strip()
        provincia = (b.get("provincia") or "").strip()
        if not provincia:
            return jsonify(ok=False, error="bad_request", message="Falta 'provincia'"), 400

        sql = text("""
            WITH q AS (
              SELECT cat, doc, org, vig, notas, link
              FROM legal_requirements
              WHERE
                (municipality_key = unaccent(lower(coalesce(:mun,''))) AND province_key = unaccent(lower(:prov)))
                OR (municipality IS NULL AND province_key = unaccent(lower(:prov)))
              ORDER BY (municipality IS NOT NULL) DESC
              LIMIT 1
            )
            SELECT * FROM q
        """)
        row = None
        try:
            with db.engine.connect() as c:
                r = c.execute(sql, {"mun": municipio, "prov": provincia}).mappings().first()
                if r: row = dict(r)
        except Exception as e:
            current_app.logger.warning("legal_requirement DB error: %s", e)

        if not row:
            row = {"cat":"no","doc":"—","org":"—","vig":"—","notas":"Sin datos para provincia/municipio.","link":None}

        return jsonify(ok=True, requirement=row)

    # ¿TIENE cédula en vigor? (lee v_owner_cedulas_last)
    @app.route("/api/legal/cedula/check", methods=["POST", "OPTIONS"])
    def cedula_check():
        if request.method == "OPTIONS": return ("", 204)
        b = request.get_json(silent=True) or {}
        refcat = (b.get("refcat") or "").strip()
        cedula_num = (b.get("cedula_numero") or "").strip()
        if not (refcat or cedula_num):
            return jsonify(ok=False, error="bad_request", message="Pasa refcat o cedula_numero"), 400

        sql = text("""
            SELECT refcat, cedula_numero, estado, expires_at, verified_at, source, notes, created_at
            FROM v_owner_cedulas_last
            WHERE (:num IS NOT NULL AND cedula_numero = :num)
               OR (:ref IS NOT NULL AND refcat = :ref)
            ORDER BY created_at DESC
            LIMIT 1
        """)
        row = None
        try:
            with db.engine.connect() as c:
                r = c.execute(sql, {"num": cedula_num or None, "ref": refcat or None}).mappings().first()
                if r: row = dict(r)
        except Exception as e:
            current_app.logger.warning("
