# app.py
"""
SpainRoom BACKEND (API)
Flask + SQLAlchemy + CORS + logging + health.

Blueprints:
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
"""
import os, sys, types, logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from flask import Flask, jsonify, request
from flask_cors import CORS

# DB robusto
try:
    from extensions import db
except Exception:
    from flask_sqlalchemy import SQLAlchemy
    db = SQLAlchemy()
    mod = types.ModuleType("extensions"); mod.db = db; sys.modules["extensions"] = mod

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = f"sqlite:///{(BASE_DIR / 'spainroom.db').as_posix()}"
SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", DEFAULT_DB)
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
        JWT_SECRET=JWT_SECRET,
        JWT_TTL_MIN=JWT_TTL_MIN,
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
    _init_logging(app)

    # Importar modelos y rutas ANTES de create_all
    from routes_auth import bp_auth; import models_auth
    from routes_contact import bp_contact; import models_contact
    from routes_contracts import bp_contracts; import models_contracts
    from routes_rooms import bp_rooms; import models_rooms; import models_roomleads
    from routes_uploads_rooms import bp_upload_rooms; import models_uploads
    from routes_upload_generic import bp_upload_generic
    from routes_franchise import bp_franchise; import models_franchise
    from routes_kyc import bp_kyc
    from routes_sms import bp_sms
    from payments import bp_payments  # << pagos/Stripe

    # create_all
    with app.app_context():
        try:
            import models_kyc  # si existe, crea tabla; si no, sigue
        except Exception:
            pass
        db.create_all()
        app.logger.info("DB create_all() OK")

    # Registrar blueprints
    app.register_blueprint(bp_auth, url_prefix="/api/auth")
    app.register_blueprint(bp_contact)
    app.register_blueprint(bp_contracts)
    app.register_blueprint(bp_rooms)
    app.register_blueprint(bp_upload_rooms)
    app.register_blueprint(bp_upload_generic)
    app.register_blueprint(bp_franchise)
    app.register_blueprint(bp_kyc)
    app.register_blueprint(bp_payments, url_prefix="/api/payments")
    app.register_blueprint(bp_sms)

    # CORS global
    ALLOWED_ORIGINS = {
        "http://localhost:5176",
        "http://127.0.0.1:5176",
        # añade Vercel si lo usas:
        # "https://tu-frontend.vercel.app",
    }
    @app.after_request
    def add_cors_headers(resp):
        origin = request.headers.get("Origin")
        if origin and (origin in ALLOWED_ORIGINS or origin.endswith(".vercel.app")):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Key, X-Franquiciado"
            resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
        return resp

    # Salud y errores
    @app.get("/health")
    def health(): return jsonify(ok=True, service="spainroom-backend")

    @app.get("/")
    def index(): return jsonify(ok=True, msg="SpainRoom API")

    @app.errorhandler(404)
    def nf(e): return jsonify(ok=False, error="not_found", message="No encontrado"), 404

    @app.errorhandler(500)
    def se(e):
        app.logger.exception("500"); return jsonify(ok=False, error="server_error"), 500

    return app

def _init_logging(app):
    app.logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    sh = logging.StreamHandler(); sh.setFormatter(fmt); sh.setLevel(logging.INFO)
    app.logger.addHandler(sh)
    logs_dir = BASE_DIR / "logs"; logs_dir.mkdir(exist_ok=True)
    fh = RotatingFileHandler(logs_dir / "backend.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt); fh.setLevel(logging.INFO); app.logger.addHandler(fh)
    app.logger.info("Logging listo")

def run_dev():
    app = create_app()
    debug = os.environ.get("FLASK_DEBUG", "1") in ("1","true","True")
    port = int(os.environ.get("PORT", "5000"))
    app.logger.info("Dev http://127.0.0.1:%s (debug=%s)", port, debug)
    app.run(host="0.0.0.0", port=port, debug=debug)

if __name__ == "__main__":
    run_dev()
