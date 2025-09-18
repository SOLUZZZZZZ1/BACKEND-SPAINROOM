# --- BEGIN: Fix sys.path for local modules ---
import os, sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
# --- END ---
# -*- coding: utf-8 -*-
import os
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy

# =========================
# Configuración base
# =========================
BASE_PATH = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_PATH / "instance"
UPLOAD_DIR = INSTANCE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = INSTANCE_DIR / "app.db"
SQLALCHEMY_DATABASE_URI = f"sqlite:///{DB_PATH}"

db = SQLAlchemy()

# =========================
# Helpers de carga dinámica (fallback franquicia)
# =========================
import importlib.util as _importlib_util
import types as _types

def _load_local_module(_name: str, _path: Path):
    """Carga un .py por ruta concreta, evitando colisiones de nombre."""
    _p = str(_path)
    if not os.path.exists(_p):
        raise FileNotFoundError(_p)
    _spec = _importlib_util.spec_from_file_location(f"_fr_{_name}", _p)
    _mod = _importlib_util.module_from_spec(_spec)
    assert _spec and _spec.loader
    _spec.loader.exec_module(_mod)
    return _mod

def _build_franquicia_pkg_from_root():
    """
    Crea un paquete virtual 'franquicia' a partir de archivos en raíz:
    models.py -> franquicia.models
    services.py -> franquicia.services
    routes.py -> franquicia.routes
    Permite imports relativos en routes.py (from .services import ...).
    """
    pkg = _types.ModuleType("franquicia")
    pkg.__path__ = [str(BASE_PATH)]
    sys.modules["franquicia"] = pkg

    models_mod   = _load_local_module("franquicia.models",   BASE_PATH / "models.py")
    services_mod = _load_local_module("franquicia.services", BASE_PATH / "services.py")
    routes_mod   = _load_local_module("franquicia.routes",   BASE_PATH / "routes.py")

    sys.modules["franquicia.models"]   = models_mod
    sys.modules["franquicia.services"] = services_mod
    sys.modules["franquicia.routes"]   = routes_mod
    return routes_mod


def create_app():
    app = Flask(__name__, instance_path=str(INSTANCE_DIR))

    # Clave Flask
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "spainroom-dev-secret")
    # DB
    app.config["SQLALCHEMY_DATABASE_URI"] = SQLALCHEMY_DATABASE_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Subidas
    app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024  # 30MB
    app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)

    # CORS
    allowed_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://*.vercel.app",
        "https://*.onrender.com",
    ]
    CORS(app, resources={r"/*": {"origins": allowed_origins}}, supports_credentials=True)

    # Defensa (opcional)
    try:
        from defense import init_defense
        init_defense(app)
        print("[DEFENSE] Defense stack initialized.")
    except Exception as e:
        print("[DEFENSE] No se pudo activar defensa:", e)

    # DB + contexto
    db.init_app(app)
    # Importar modelos de Franquicia si el flag está activo (para que create_all cree tablas)
    try:
        if os.getenv("BACKEND_FEATURE_FRANQ_PLAZAS", "off").lower() == "on":
            try:
                from franquicia import models as _fr_models  # noqa: F401
                print("[FRANQ] Modelos importados (paquete).")
            except Exception:
                _ = _load_local_module("models", BASE_PATH / "models.py")  # noqa: F401
                print("[FRANQ] Modelos importados (raíz).")
        else:
            print("[FRANQ] Flag OFF: no se importan modelos.")
    except Exception as e:
        print("[WARN] Import modelos Franquicia:", e)

    # Crear tablas bajo contexto
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            print("[DB] Aviso create_all:", e)

    # Blueprints opcionales
    try:
        from auth import bp_auth, register_auth_models
        try:
            with app.app_context():
                register_auth_models(db)
        except Exception as e:
            print("[WARN] register_auth_models:", e)
        app.register_blueprint(bp_auth)
        print("[AUTH] OK")
    except Exception as e:
        print("[WARN] Auth:", e)

    try:
        from payments import bp_pay
        if not os.getenv("STRIPE_SECRET_KEY"):
            raise RuntimeError("'STRIPE_SECRET_KEY' no configurada")
        app.register_blueprint(bp_pay)
        print("[PAY] OK")
    except Exception as e:
        print("[WARN] Payments:", e)

    try:
        from opportunities import bp_opps
        app.register_blueprint(bp_opps)
        print("[OPPS] OK")
    except Exception as e:
        print("[WARN] Opps:", e)

    try:
        from voice_bot import bp_voice
        app.register_blueprint(bp_voice, url_prefix="/voice")
        print("[VOICE] OK")
    except Exception as e:
        print("[WARN] Voice:", e)

    # Franquicia (interno, por flag) — con 3 niveles: paquete, paquete virtual, placeholder
    try:
        if os.getenv("BACKEND_FEATURE_FRANQ_PLAZAS", "off").lower() == "on":
            bp_franquicia = None
            try:
                # 1) Paquete real
                from franquicia.routes import bp_franquicia as _bp
                bp_franquicia = _bp
                print("[FRANQ] Blueprint (paquete) localizado.")
            except Exception as e_pkg:
                try:
                    # 2) Paquete virtual desde raíz
                    _fr_routes = _build_franquicia_pkg_from_root()
                    bp_franquicia = getattr(_fr_routes, "bp_franquicia")
                    print("[FRANQ] Blueprint (raíz c/ paquete virtual) localizado.")
                except Exception as e_virt:
                    # 3) Placeholder mínimo para validar wiring
                    print("[FRANQ] Fallback placeholder por error:", e_virt or e_pkg)
                    from flask import Blueprint
                    _bp = Blueprint("franquicia", __name__)
                    @_bp.get("/api/admin/franquicia/summary")
                    def _fr_placeholder():
                        return jsonify(ok=True, placeholder=True, note="Blueprint mínimo activo"), 200
                    bp_franquicia = _bp
                    print("[FRANQ] Blueprint placeholder registrado (temporal).")

            app.register_blueprint(bp_franquicia, url_prefix="")
            print("[FRANQ] Blueprint Franquicia registrado.")
        else:
            print("[FRANQ] Flag OFF: módulo Franquicia no registrado.")
    except Exception as e:
        print("[WARN] Franquicia:", e)

    # Rutas base
    @app.route("/health")
    def health():
        return jsonify(ok=True, service="spainroom-backend")

    @app.get("/api/rooms")
    def list_rooms():
        rooms = [
            {"id": 1, "title": "Habitación centro Madrid", "description": "Luminoso y céntrico",
             "price": 400, "city": "Madrid", "address": "Calle Mayor, 1", "photo": None,
             "created_at": datetime(2025, 9, 3, 17, 59, 37, 673635).isoformat(),
             "updated_at": datetime(2025, 9, 3, 17, 59, 37, 673647).isoformat()},
            {"id": 2, "title": "Habitación en Valencia", "description": "Cerca de la playa",
             "price": 380, "city": "Valencia", "address": "Avenida del Puerto, 22", "photo": None,
             "created_at": datetime(2025, 9, 3, 17, 59, 37, 673654).isoformat(),
             "updated_at": datetime(2025, 9, 3, 17, 59, 37, 673658).isoformat()},
        ]
        return jsonify(rooms)

    @app.post("/api/upload")
    def upload_file():
        if "file" not in request.files:
            return jsonify(error="Archivo requerido"), 400
        f = request.files["file"]
        if f.filename == "":
            return jsonify(error="Archivo inválido"), 400
        filename = secure_filename(f.filename)
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        name, ext = os.path.splitext(filename)
        safe_name = f"{name}-{ts}{ext}".replace(" ", "_")
        target = UPLOAD_DIR / safe_name
        f.save(target)
        return jsonify(ok=True, path=f"/uploads/{safe_name}")

    @app.get("/uploads/<path:filename>")
    def serve_upload(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=False)

    return app


if __name__ == "__main__":
    app = create_app()
    print(f">>> SQLALCHEMY_DATABASE_URI = {SQLALCHEMY_DATABASE_URI}")
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5001")), debug=True)
