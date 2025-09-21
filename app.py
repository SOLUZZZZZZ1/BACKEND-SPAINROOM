# app.py
"""
SpainRoom BACKEND (API)
Flask + SQLAlchemy + CORS + logging + health.

Blueprints activos:
  - /api/auth/*                -> routes_auth.bp_auth
  - /api/contacto/*            -> routes_contact.bp_contact
  - /api/contracts/*           -> routes_contracts.bp_contracts
  - /api/rooms/*               -> routes_rooms.bp_rooms
  - /api/rooms/upload_photos   -> routes_uploads_rooms.bp_upload_rooms
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS

from extensions import db   # << db global (evita import circular)

# ---------------- Config ----------------
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = f"sqlite:///{(BASE_DIR / 'spainroom.db').as_posix()}"

SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", DEFAULT_DB)
JWT_SECRET  = os.environ.get("JWT_SECRET", os.environ.get("SECRET_KEY", "sr-dev-secret"))
JWT_TTL_MIN = int(os.environ.get("JWT_TTL_MIN", "720"))

# ---------------- App factory ----------------
def create_app(test_config=None):
    app = Flask(__name__, static_folder="public", static_url_path="/")

    # Asegura instance/ y uploads
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

    # Extensiones
    db.init_app(app)
    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
    _init_logging(app)

    # ========== IMPORTAR MODELOS Y RUTAS ANTES DE CREAR TABLAS ==========
    # Auth (OTP/JWT)
    from routes_auth import bp_auth
    import models_auth

    # Contacto (oportunidades / tenants)
    from routes_contact import bp_contact
    import models_contact

    # Contratos + líneas (sub_ref) / Habitaciones / Uploads de habitaciones
    from routes_contracts import bp_contracts
    import models_contracts

    from routes_rooms import bp_rooms
    import models_rooms

    from routes_uploads_rooms import bp_upload_rooms
    import models_uploads

    # (Opcionales — descomenta cuando estén listos)
    # from franquicia.routes import bp_franquicia; import franquicia.models
    # from routes_owner import bp_owner; import models_owner

    # ======================= CREAR TABLAS =======================
    with app.app_context():
        db.create_all()
        app.logger.info("DB create_all() OK")

    # ===================== REGISTRAR BLUEPRINTS =================
    app.register_blueprint(bp_auth, url_prefix="/api/auth")
    app.register_blueprint(bp_contact)        # /api/contacto/*
    app.register_blueprint(bp_contracts)      # /api/contracts/*
    app.register_blueprint(bp_rooms)          # /api/rooms/*
    app.register_blueprint(bp_upload_rooms)   # /api/rooms/upload_photos
    # app.register_blueprint(bp_franquicia, url_prefix="/api/admin/franquicia")
    # app.register_blueprint(bp_owner)

    # ===================== CORS GLOBAL (after_request) =================
    ALLOWED_ORIGINS = {
        "http://localhost:5176",
        "http://127.0.0.1:5176",
        # añade aquí tus URLs de Vercel si las usas, p.ej.:
        # "https://frontend-xxxxx.vercel.app",
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

    # ====================== RUTAS BÁSICAS =======================
    @app.get("/health")
    def health():
        return jsonify(ok=True, service="spainroom-backend", env=os.environ.get("FLASK_ENV", ""))

    @app.get("/")
    def index():
        return jsonify(ok=True, msg="SpainRoom API")

    @app.errorhandler(404)
    def nf(e): return jsonify(ok=False, error="not_found"), 404

    @app.errorhandler(500)
    def se(e):
        app.logger.exception("500")
        return jsonify(ok=False, error="server_error"), 500

    return app

# ---------------- Logging ----------------
def _init_logging(app):
    app.logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    sh = logging.StreamHandler(); sh.setFormatter(fmt); sh.setLevel(logging.INFO)
    app.logger.addHandler(sh)
    logs_dir = BASE_DIR / "logs"; logs_dir.mkdir(exist_ok=True)
    fh = RotatingFileHandler(logs_dir / "backend.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt); fh.setLevel(logging.INFO)
    app.logger.addHandler(fh)
    app.logger.info("Logging listo")

# ---------------- Run (local) ----------------
def run_dev():
    app = create_app()
    debug = os.environ.get("FLASK_DEBUG", "1") in ("1", "true", "True")
    port  = int(os.environ.get("PORT", "5000"))
    app.logger.info("Dev http://127.0.0.1:%s (debug=%s)", port, debug)
    app.run(host="0.0.0.0", port=port, debug=debug)

if __name__ == "__main__":
    run_dev()
