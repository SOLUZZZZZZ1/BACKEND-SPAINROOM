# app.py — SpainRoom BACKEND principal: blueprints + proxy pagos + LEG/Owner
import os, sys, types, logging, requests
from logging.handlers import RotatingFileHandler
from pathlib import Path
from flask import Flask, jsonify, request, current_app, Response
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

ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 300}
PAY_PROXY_BASE = os.getenv("PAY_PROXY_BASE", "https://spainroom-backend-1.onrender.com").rstrip("/")

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

    # ---------- Importar blueprints (con protección) ----------
    def _try(name, fn):
        try:
            return fn()
        except Exception as e:
            app.logger.info(f"{name} no disponible: {e}")
            return None

    # Rooms: tus decoradores YA incluyen /api/rooms/... => SIN url_prefix
    bp_rooms = _try("rooms", lambda: __import__("routes_rooms", fromlist=["bp_rooms"]).bp_rooms)
    # Owner
    bp_owner = _try("owner", lambda: __import__("routes_owner_cedula", fromlist=["bp_owner"]).bp_owner)
    # Contact / Contracts / Franchise / Auth / KYC / SMS / Uploads (si existen)
    bp_contact         = _try("contact",         lambda: __import__("routes_contact", fromlist=["bp_contact"]).bp_contact)
    bp_contracts       = _try("contracts",       lambda: __import__("routes_contracts", fromlist=["bp_contracts"]).bp_contracts)
    bp_franchise       = _try("franchise",       lambda: __import__("routes_franchise", fromlist=["bp_franchise"]).bp_franchise)
    bp_auth            = _try("auth",            lambda: __import__("routes_auth", fromlist=["bp_auth"]).bp_auth)
    bp_kyc             = _try("kyc",             lambda: __import__("routes_kyc", fromlist=["bp_kyc"]).bp_kyc)
    bp_sms             = _try("sms",             lambda: __import__("routes_sms", fromlist=["bp_sms"]).bp_sms)
    bp_upload_rooms    = _try("upload_rooms",    lambda: __import__("routes_uploads_rooms", fromlist=["bp_upload_rooms"]).bp_upload_rooms)
    bp_upload_autofit  = _try("uploads_autofit", lambda: __import__("routes_uploads_rooms_autofit", fromlist=["bp_upload_rooms_autofit"]).bp_upload_rooms_autofit)
    bp_upload_generic  = _try("upload_generic",  lambda: __import__("routes_upload_generic", fromlist=["bp_upload_generic"]).bp_upload_generic)

    # Catastro opcional por ENV (si tienes ese módulo)
    bp_catastro = None
    if os.getenv("ENABLE_BP_CATASTRO") in {"1","true","True"}:
        bp_catastro = _try("catastro", lambda: __import__("routes_catastro", fromlist=["bp_catastro"]).bp_catastro)

    # ---------- Registrar blueprints ----------
    if bp_rooms:           app.register_blueprint(bp_rooms)             # SIN prefijo extra
    if bp_upload_rooms:    app.register_blueprint(bp_upload_rooms)
    if bp_upload_autofit:  app.register_blueprint(bp_upload_autofit)
    if bp_upload_generic:  app.register_blueprint(bp_upload_generic)

    if bp_contact:         app.register_blueprint(bp_contact,    url_prefix="/api/contacto")
    if bp_contracts:       app.register_blueprint(bp_contracts,  url_prefix="/api/contracts")
    if bp_franchise:       app.register_blueprint(bp_franchise,  url_prefix="/api/franchise")
    if bp_auth:            app.register_blueprint(bp_auth,       url_prefix="/api/auth")
    if bp_kyc:             app.register_blueprint(bp_kyc,        url_prefix="/api/kyc")
    if bp_sms:             app.register_blueprint(bp_sms,        url_prefix="/sms")
    if bp_owner:           app.register_blueprint(bp_owner,      url_prefix="/api/owner")
    if bp_catastro:        app.register_blueprint(bp_catastro,   url_prefix="/api/catastro")

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

    # ---------- LEGAL mínimos ----------
    @app.route("/api/legal/requirement", methods=["POST","OPTIONS"])
    def legal_requirement():
        if request.method == "OPTIONS": return ("",204)
        data = request.get_json(silent=True) or {}
        mun = (data.get("municipio") or "").strip()
        prov = (data.get("provincia") or "").strip()
        if not prov: return jsonify(ok=False,error="bad_request",message="Falta provincia"),400
        sql = text("""
            SELECT cat,doc,org,vig,notas,link FROM legal_requirements
             WHERE (municipality_key = unaccent(lower(coalesce(:mun,''))) AND province_key = unaccent(lower(:prov)))
                OR (municipality IS NULL AND province_key = unaccent(lower(:prov)))
             ORDER BY (municipality IS NOT NULL) DESC
             LIMIT 1
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
                    d = dict(r); estado = (d.get("estado") or "no_consta").lower()
                    return jsonify(ok=True, has_doc=(estado=="vigente"), status=estado, data=d)
        except Exception as e:
            current_app.logger.warning(f"cedula_check DB error: {e}")
        return jsonify(ok=True, has_doc=False, status="no_consta")

    # ---------- PROXY pagos (Stripe) ----------
    #  A) Ruta compatible con tu front actual
    @app.route("/create-checkout-session", methods=["POST","OPTIONS"])
    def proxy_create_checkout_session_root():
        if request.method == "OPTIONS": return ("",204)
        try:
            r = requests.post(
                f"{PAY_PROXY_BASE}/create-checkout-session",
                json=(request.get_json(silent=True) or {}),
                headers={"Content-Type":"application/json"},
                timeout=12
            )
            return Response(response=r.content, status=r.status_code,
                            headers={"Content-Type": r.headers.get("Content-Type","application/json")})
        except Exception as e:
            current_app.logger.warning(f"proxy payments error: {e}")
            return jsonify(ok=False, error="proxy_error"), 502

    #  B) Ruta “estándar” por si en algún sitio llamas a /api/payments/...
    @app.route("/api/payments/create-checkout-session", methods=["POST","OPTIONS"])
    def proxy_create_checkout_session_api():
        if request.method == "OPTIONS": return ("",204)
        try:
            r = requests.post(
                f"{PAY_PROXY_BASE}/create-checkout-session",
                json=(request.get_json(silent=True) or {}),
                headers={"Content-Type":"application/json"},
                timeout=12
            )
            return Response(response=r.content, status=r.status_code,
                            headers={"Content-Type": r.headers.get("Content-Type","application/json")})
        except Exception as e:
            current_app.logger.warning(f"proxy payments error: {e}")
            return jsonify(ok=False, error="proxy_error"), 502

    # ---------- Owner mínimos ----------
    @app.route("/api/owner/check", methods=["POST","OPTIONS"])
    def owner_check():
        if request.method == "OPTIONS": return ("",204)
        body = request.get_json(silent=True) or {}
        import uuid
        return jsonify(ok=True, id="SRV-CHK-" + uuid.uuid4().hex[:8], echo=body)

    @app.route("/api/owner/cedula/upload", methods=["POST","OPTIONS"])
    def cedula_upload():
        if request.method == "OPTIONS": return ("",204)
        f = request.files.get("file")
        if not f: return jsonify(ok=False, error="no_file"), 400
        filename = secure_filename(f.filename or "file")
        up = Path(current_app.instance_path) / "uploads"; up.mkdir(parents=True, exist_ok=True)
        tgt = up / filename; f.save(tgt)
        return jsonify(ok=True, filename=filename, size=tgt.stat().st_size)

    # ---------- Salud / raíz ----------
    @app.get("/health")
    def health(): return jsonify(ok=True, service="spainroom-backend")

    @app.get("/")
    def root(): return jsonify(ok=True, msg="SpainRoom API")

    return app

def _init_logging(app):
    app.logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    sh = logging.StreamHandler(); sh.setFormatter(fmt); app.logger.addHandler(sh)
    logs_dir = BASE_DIR / "logs"; logs_dir.mkdir(exist_ok=True)
    fh = RotatingFileHandler(logs_dir / "backend.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt); app.logger.addHandler(fh)
    app.logger.info("Logging listo")

def run_dev():
    app = create_app()
    port = int(os.getenv("PORT","5000")); debug = os.getenv("FLASK_DEBUG","1") in ("1","true","True")
    app.logger.info(f"Dev http://127.0.0.1:{port} (debug={debug})")
    app.run(host="0.0.0.0", port=port, debug=debug)

if __name__ == "__main__":
    run_dev()
