# app_backend_api_GO_LIVE.py — App completa con blueprints registrados
# Nora · 2025-10-12
import os, sys, types, logging, requests
from logging.handlers import RotatingFileHandler
from pathlib import Path
from flask import Flask, jsonify, request, current_app, Response
from flask_cors import CORS

try:
    from extensions import db
except Exception:
    from flask_sqlalchemy import SQLAlchemy
    db = SQLAlchemy()
    mod = types.ModuleType("extensions"); mod.db = db; sys.modules["extensions"] = mod

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = f"sqlite:///{(BASE_DIR / 'spainroom.db').as_posix()}"

SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", DEFAULT_DB)
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

    def _try(name, fn):
        try: return fn()
        except Exception as e:
            app.logger.info(f"{name} no disponible: {e}")
            return None

    # Blueprints
    bp_rooms            = _try("rooms",            lambda: __import__("routes_rooms", fromlist=["bp_rooms"]).bp_rooms)
    bp_owner            = _try("owner",            lambda: __import__("routes_owner_cedula", fromlist=["bp_owner"]).bp_owner)
    bp_contact          = _try("contact",          lambda: __import__("routes_contact", fromlist=["bp_contact"]).bp_contact)
    bp_upload_generic   = _try("upload_generic",   lambda: __import__("routes_upload_generic", fromlist=["bp_upload_generic"]).bp_upload_generic)
    bp_upload_rooms     = _try("upload_rooms",     lambda: __import__("routes_uploads_rooms", fromlist=["bp_upload_rooms"]).bp_upload_rooms)
    bp_upload_autofit   = _try("upload_rooms_auto",lambda: __import__("routes_uploads_rooms_autofit", fromlist=["bp_upload_rooms_autofit"]).bp_upload_rooms_autofit)
    bp_auth             = _try("auth",             lambda: __import__("routes_auth", fromlist=["bp_auth"]).bp_auth)
    bp_kyc              = _try("kyc",              lambda: __import__("routes_kyc", fromlist=["bp_kyc"]).bp_kyc)
    bp_veriff           = _try("veriff",           lambda: __import__("routes_veriff", fromlist=["bp_veriff"]).bp_veriff)
    bp_twilio           = _try("twilio",           lambda: __import__("routes_twilio", fromlist=["bp_twilio"]).bp_twilio)
    bp_sms              = _try("sms",              lambda: __import__("routes_sms", fromlist=["bp_sms"]).bp_sms)
    bp_admin_franq      = _try("admin_franchise",  lambda: __import__("routes_admin_franchise", fromlist=["bp_admin_franchise"]).bp_admin_franchise)

    # Registro
    if bp_rooms:           app.register_blueprint(bp_rooms)
    if bp_owner:           app.register_blueprint(bp_owner,      url_prefix="/api/owner")
    if bp_contact:         app.register_blueprint(bp_contact)                            # ya publica /api/contacto/*
    if bp_upload_generic:  app.register_blueprint(bp_upload_generic)
    if bp_upload_rooms:    app.register_blueprint(bp_upload_rooms)
    if bp_upload_autofit:  app.register_blueprint(bp_upload_autofit)
    if bp_auth:            app.register_blueprint(bp_auth)
    if bp_kyc:             app.register_blueprint(bp_kyc)
    if bp_veriff:          app.register_blueprint(bp_veriff)
    if bp_twilio:          app.register_blueprint(bp_twilio)
    if bp_sms:             app.register_blueprint(bp_sms,        url_prefix="/sms")
    if bp_admin_franchise: app.register_blueprint(bp_admin_franchise)

    # Proxy pagos
    @app.route("/create-checkout-session", methods=["POST","OPTIONS"])
    def proxy_checkout_root():
        if request.method == "OPTIONS": return ("",204)
        try:
            r = requests.post(f"{PAY_PROXY_BASE}/create-checkout-session",
                json=(request.get_json(silent=True) or {}), headers={"Content-Type":"application/json"}, timeout=12)
            return Response(response=r.content, status=r.status_code, headers={"Content-Type": r.headers.get("Content-Type","application/json")})
        except Exception as e:
            current_app.logger.warning(f"proxy payments error: {e}")
            return jsonify(ok=False, error="proxy_error"), 502

    @app.route("/api/payments/create-checkout-session", methods=["POST","OPTIONS"])
    def proxy_checkout_api():
        if request.method == "OPTIONS": return ("",204)
        try:
            r = requests.post(f"{PAY_PROXY_BASE}/create-checkout-session",
                json=(request.get_json(silent=True) or {}), headers={"Content-Type":"application/json"}, timeout=12)
            return Response(response=r.content, status=r.status_code, headers={"Content-Type": r.headers.get("Content-Type","application/json")})
        except Exception as e:
            current_app.logger.warning(f"proxy payments error: {e}")
            return jsonify(ok=False, error="proxy_error"), 502

    # Salud
    @app.get("/health")
    @app.get("/healthz")
    def health(): return jsonify(ok=True, service="spainroom-backend")

    return app

def _init_logging(app):
    app.logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    sh = logging.StreamHandler(); sh.setFormatter(fmt); app.logger.addHandler(sh)
    logs_dir = BASE_DIR / "logs"; logs_dir.mkdir(exist_ok=True)
    try:
        fh = RotatingFileHandler(logs_dir / "backend.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt); app.logger.addHandler(fh)
    except Exception:
        pass
    app.logger.info("Logging listo")

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")), debug=True)
