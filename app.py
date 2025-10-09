"""
SpainRoom BACKEND (API)
Flask + SQLAlchemy + CORS + logging + health.
Incluye: todos los blueprints, Stripe, Rooms, Owner, Cédula y Catastro.
"""

import os, sys, types, logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from flask import Flask, jsonify, request, current_app
from flask_cors import CORS
from werkzeug.utils import secure_filename
from sqlalchemy import text

# ---------- DB ----------
try:
    from extensions import db
except Exception:
    from flask_sqlalchemy import SQLAlchemy
    db = SQLAlchemy()
    mod = types.ModuleType("extensions")
    mod.db = db
    sys.modules["extensions"] = mod

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = f"sqlite:///{(BASE_DIR / 'spainroom.db').as_posix()}"

SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", DEFAULT_DB)
_raw = os.environ.get("DATABASE_URL")
if _raw:
    if _raw.startswith("postgres://"):
        _raw = _raw.replace("postgres://", "postgresql+psycopg2://", 1)
    elif _raw.startswith("postgresql://"):
        _raw = _raw.replace("postgresql://", "postgresql+psycopg2://", 1)
    if "sslmode=" not in _raw and "+psycopg2://" in _raw:
        _raw += ("&" if "?" in _raw else "?") + "sslmode=require"
    SQLALCHEMY_DATABASE_URI = _raw

ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 300}

# ---------- App ----------
def create_app(test_config=None):
    app = Flask(__name__, static_folder="public", static_url_path="/")
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.instance_path, "uploads").mkdir(parents=True, exist_ok=True)

    app.config.update(
        SQLALCHEMY_DATABASE_URI=SQLALCHEMY_DATABASE_URI,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS=ENGINE_OPTIONS,
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
    _init_logging(app)

    # ---------- Import blueprints ----------
    def _try(name, imp):
        try:
            return imp()
        except Exception as e:
            app.logger.info(f"{name} no disponible: {e}")
            return None

    bp_rooms        = _try("rooms", lambda: __import__("routes_rooms", fromlist=["bp_rooms"]).bp_rooms)
    bp_upload_rooms = _try("upload_rooms", lambda: __import__("routes_uploads_rooms", fromlist=["bp_upload_rooms"]).bp_upload_rooms)
    bp_contact      = _try("contact", lambda: __import__("routes_contact", fromlist=["bp_contact"]).bp_contact)
    bp_contracts    = _try("contracts", lambda: __import__("routes_contracts", fromlist=["bp_contracts"]).bp_contracts)
    bp_franchise    = _try("franchise", lambda: __import__("routes_franchise", fromlist=["bp_franchise"]).bp_franchise)
    bp_auth         = _try("auth", lambda: __import__("routes_auth", fromlist=["bp_auth"]).bp_auth)
    bp_kyc          = _try("kyc", lambda: __import__("routes_kyc", fromlist=["bp_kyc"]).bp_kyc)
    bp_sms          = _try("sms", lambda: __import__("routes_sms", fromlist=["bp_sms"]).bp_sms)
    bp_owner        = _try("owner", lambda: __import__("routes_owner_cedula", fromlist=["bp_owner"]).bp_owner)
    bp_payments     = _try("payments", lambda: __import__("payments", fromlist=["bp_payments"]).bp_payments)
    bp_catastro     = None
    if os.getenv("ENABLE_BP_CATASTRO") in {"1", "true", "True"}:
        bp_catastro = _try("catastro", lambda: __import__("routes_catastro", fromlist=["bp_catastro"]).bp_catastro)

    # ---------- Crear tablas ----------
    with app.app_context():
        db.create_all()

    # ---------- Registrar blueprints ----------
    if bp_rooms:        app.register_blueprint(bp_rooms, url_prefix="/api/rooms")
    if bp_upload_rooms: app.register_blueprint(bp_upload_rooms, url_prefix="/api/rooms")
    if bp_contact:      app.register_blueprint(bp_contact, url_prefix="/api/contacto")
    if bp_contracts:    app.register_blueprint(bp_contracts, url_prefix="/api/contracts")
    if bp_franchise:    app.register_blueprint(bp_franchise, url_prefix="/api/franchise")
    if bp_auth:         app.register_blueprint(bp_auth, url_prefix="/api/auth")
    if bp_kyc:          app.register_blueprint(bp_kyc, url_prefix="/api/kyc")
    if bp_sms:          app.register_blueprint(bp_sms, url_prefix="/sms")
    if bp_owner:        app.register_blueprint(bp_owner, url_prefix="/api/owner")
    if bp_payments:     app.register_blueprint(bp_payments, url_prefix="/api/payments")
    if bp_catastro:     app.register_blueprint(bp_catastro, url_prefix="/api/catastro")

    # ---------- CORS extra ----------
    ALLOWED_ORIGINS = {"http://localhost:5176", "http://127.0.0.1:5176"}
    @app.after_request
    def add_cors(resp):
        origin = request.headers.get("Origin")
        if origin and (origin in ALLOWED_ORIGINS or origin.endswith(".vercel.app")):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Key, Stripe-Signature"
            resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
        return resp

    # ---------- Endpoints internos ----------
    @app.route("/api/legal/requirement", methods=["POST","OPTIONS"])
    def legal_requirement():
        if request.method == "OPTIONS": return ("",204)
        data = request.get_json(silent=True) or {}
        mun = (data.get("municipio") or "").strip()
        prov = (data.get("provincia") or "").strip()
        if not prov:
            return jsonify(ok=False,error="bad_request",message="Falta provincia"),400
        sql = text("""
            SELECT cat,doc,org,vig,notas,link FROM legal_requirements
             WHERE (municipality_key = unaccent(lower(coalesce(:mun,''))) 
                AND province_key = unaccent(lower(:prov)))
                OR (municipality IS NULL AND province_key = unaccent(lower(:prov)))
             ORDER BY (municipality IS NOT NULL) DESC LIMIT 1
        """)
        try:
            with db.engine.connect() as c:
                r = c.execute(sql,{"mun":mun,"prov":prov}).mappings().first()
                if r: return jsonify(ok=True, requirement=dict(r))
        except Exception as e:
            current_app.logger.warning(f"legal_requirement DB error: {e}")
        return jsonify(ok=True, requirement={"cat":"no","doc":"—","org":"—","vig":"—","notas":"Sin datos.","link":None})

    @app.route("/api/legal/cedula/check", methods=["POST","OPTIONS"])
    def cedula_check():
        if request.method == "OPTIONS": return ("",204)
        b = request.get_json(silent=True) or {}
        ref = (b.get("refcat") or "").strip()
        num = (b.get("cedula_numero") or "").strip()
        if not (ref or num):
            return jsonify(ok=False,error="bad_request",message="Falta refcat o cedula_numero"),400
        sql = text("""
            SELECT refcat,cedula_numero,estado,expires_at,verified_at,source,notes,created_at
              FROM v_owner_cedulas_last
             WHERE (:num IS NOT NULL AND cedula_numero=:num)
                OR (:ref IS NOT NULL AND refcat=:ref)
             ORDER BY created_at DESC LIMIT 1
        """)
        try:
            with db.engine.connect() as c:
                r = c.execute(sql,{"num":num or None,"ref":ref or None}).mappings().first()
                if r:
                    d = dict(r)
                    estado = (d.get("estado") or "no_consta").lower()
                    return jsonify(ok=True, has_doc=(estado=="vigente"), status=estado, data=d)
        except Exception as e:
            current_app.logger.warning(f"cedula_check DB error: {e}")
        return jsonify(ok=True, has_doc=False, status="no_consta")

    @app.route("/api/admin/cedula/upsert", methods=["POST","OPTIONS"])
    def cedula_upsert():
        if request.method == "OPTIONS": return ("",204)
        b = request.get_json(silent=True) or {}
        ref = (b.get("refcat") or "").strip() or None
        num = (b.get("cedula_numero") or "").strip() or None
        estado = (b.get("estado") or "pendiente").lower()
        if not (ref or num): 
            return jsonify(ok=False,error="bad_request"),400
        sql = text("""
            INSERT INTO owner_cedulas (refcat,cedula_numero,estado,verified_at,source)
            VALUES (:r,:n,:e,NOW(),'manual')
        """)
        try:
            with db.engine.begin() as c:
                c.execute(sql,{"r":ref,"n":num,"e":estado})
            return jsonify(ok=True)
        except Exception as e:
            current_app.logger.warning(f"cedula_upsert error: {e}")
            return jsonify(ok=False,error="db_error"),500

    @app.get("/health")
    def health(): return jsonify(ok=True,service="spainroom-backend")

    @app.get("/")
    def root(): return jsonify(ok=True,msg="SpainRoom API")

    return app

# ---------- Logging ----------
def _init_logging(app):
    app.logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    sh = logging.StreamHandler(); sh.setFormatter(fmt)
    app.logger.addHandler(sh)
    logs_dir = BASE_DIR / "logs"; logs_dir.mkdir(exist_ok=True)
    fh = RotatingFileHandler(logs_dir / "backend.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    app.logger.addHandler(fh)
    app.logger.info("Logging listo")

# ---------- Runner ----------
def run_dev():
    app = create_app()
    port = int(os.getenv("PORT","5000"))
    debug = os.getenv("FLASK_DEBUG","1") in ("1","true","True")
    app.logger.info(f"Dev http://127.0.0.1:{port} (debug={debug})")
    app.run(host="0.0.0.0", port=port, debug=debug)

if __name__ == "__main__":
    run_dev()
