# --- BEGIN: Fix sys.path for local modules ---
import os, sys
BASE_DIR_STR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR_STR not in sys.path:
    sys.path.insert(0, BASE_DIR_STR)
# --- END ---
# -*- coding: utf-8 -*-
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, Blueprint
from flask_cors import CORS
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy

# =========================
# Configuración base
# =========================
BASE_DIR = Path(BASE_DIR_STR)
INSTANCE_DIR = BASE_DIR / "instance"
UPLOAD_DIR = INSTANCE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = INSTANCE_DIR / "app.db"
SQLALCHEMY_DATABASE_URI = f"sqlite:///{DB_PATH}"

db = SQLAlchemy()

# =========================
# Helpers de carga (Franquicia)
# =========================
import importlib.util as _importlib_util
import types as _types

def _load_local_module(_qualified_name: str, _path: Path):
    """Carga un .py por ruta con un nombre totalmente calificado para evitar colisiones."""
    _p = str(_path)
    if not os.path.exists(_p):
        raise FileNotFoundError(_p)
    _spec = _importlib_util.spec_from_file_location(_qualified_name, _p)
    _mod = _importlib_util.module_from_spec(_spec)
    assert _spec and _spec.loader
    _spec.loader.exec_module(_mod)
    return _mod

def _ensure_franquicia_package():
    """
    Garantiza que existe un paquete 'franquicia' válido en sys.modules y
    devuelve el módulo routes cargado. Intenta en este orden:

    1) Paquete real franquicia/ (from franquicia.routes import ...)
    2) Paquete virtual montado desde archivos sueltos en raíz:
       - models.py   -> franquicia.models
       - services.py -> franquicia.services
       - routes.py   -> franquicia.routes

    Así, routes.py puede hacer imports relativos:  from .services import ...
    """
    if "franquicia.routes" in sys.modules:
        return sys.modules["franquicia.routes"], "ya_importado"

    # Intento 1: paquete real
    try:
        import importlib
        routes_mod = importlib.import_module("franquicia.routes")
        return routes_mod, "paquete"
    except Exception:
        pass

    # Intento 2: paquete virtual desde archivos sueltos en raíz
    pkg = _types.ModuleType("franquicia")
    pkg.__path__ = [str(BASE_DIR)]
    sys.modules.setdefault("franquicia", pkg)

    if "franquicia.models" not in sys.modules and (BASE_DIR / "models.py").exists():
        sys.modules["franquicia.models"] = _load_local_module("franquicia.models", BASE_DIR / "models.py")
    if "franquicia.services" not in sys.modules and (BASE_DIR / "services.py").exists():
        sys.modules["franquicia.services"] = _load_local_module("franquicia.services", BASE_DIR / "services.py")
    if "franquicia.routes" not in sys.modules and (BASE_DIR / "routes.py").exists():
        sys.modules["franquicia.routes"] = _load_local_module("franquicia.routes", BASE_DIR / "routes.py")

    if "franquicia.routes" in sys.modules:
        return sys.modules["franquicia.routes"], "virtual"

    raise RuntimeError("No se pudo localizar franquicia.routes ni en paquete ni en raíz")


def create_app():
    app = Flask(__name__, instance_path=str(INSTANCE_DIR))

    # Clave / DB / Uploads
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "spainroom-dev-secret")
    app.config["SQLALCHEMY_DATABASE_URI"] = SQLALCHEMY_DATABASE_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
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

    # Defensa (dentro de app context)
    try:
        from defense import init_defense
        with app.app_context():
            init_defense(app)
        print("[DEFENSE] Defense stack initialized.")
    except Exception as e:
        print("[DEFENSE] No se pudo activar defensa:", e)

    # DB
    db.init_app(app)

    # Franquicia: cargar modelos si flag ON (para create_all)
    flag_franq_on = os.getenv("BACKEND_FEATURE_FRANQ_PLAZAS", "off").lower() == "on"
    routes_mod = None
    if flag_franq_on:
        # Montar paquete real o virtual (models/services/routes)
        try:
            routes_mod, how = _ensure_franquicia_package()
            print(f"[FRANQ] paquete routes localizado por: {how}")
        except Exception as e:
            print("[FRANQ] No se pudo localizar paquete/rutas:", e)

    # Crear tablas bajo contexto (evita warning de app context)
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

    # VOZ — FORZADO OFF en Flask (solo en servicio VOZ)
    try:
        print("[VOICE] Desactivado en Flask (uso exclusivo del servicio VOZ).")
        # Si quisieras activarlo algún día:
        # if os.getenv("BACKEND_VOICE_ENABLED", "off").lower() == "on":
        #     from voice_bot import bp_voice
        #     app.register_blueprint(bp_voice, url_prefix="/voice")
        #     print("[VOICE] Blueprint voice registrado (Flask).")
    except Exception as e:
        print("[WARN] Voice:", e)

    # Franquicia (Admin Interno)
    if flag_franq_on:
        try:
            if routes_mod is not None:
                bp_franquicia = getattr(routes_mod, "bp_franquicia", None)
                if bp_franquicia is not None:
                    app.register_blueprint(bp_franquicia, url_prefix="/api/admin/franquicia")
                    print("[FRANQ] Blueprint Franquicia registrado.")
                else:
                    raise AttributeError("bp_franquicia no encontrado en routes")
            else:
                # Placeholder para nunca devolver 404 mientras diagnosticas
                _bp = Blueprint("franquicia_placeholder", __name__)
                @_bp.get("/api/admin/franquicia/summary")
                def _fr_placeholder():
                    return jsonify(ok=True, placeholder=True, note="Blueprint mínimo activo"), 200
                app.register_blueprint(_bp, url_prefix="")
                print("[FRANQ] Blueprint placeholder registrado (temporal).")
        except Exception as e:
            print("[WARN] Franquicia:", e)
            _bp2 = Blueprint("franquicia_placeholder2", __name__)
            @_bp2.get("/api/admin/franquicia/summary")
            def _fr_placeholder2():
                return jsonify(ok=True, placeholder=True, note="Blueprint mínimo activo (fallback 2)"), 200
            app.register_blueprint(_bp2, url_prefix="")
            print("[FRANQ] Blueprint placeholder2 registrado.")
    else:
        print("[FRANQ] Flag OFF: módulo Franquicia no registrado.")

    # Rutas base
    @app.route("/health")
    def health():
        return jsonify(ok=True, service="spainroom-backend")

    @app.get("/api/rooms")
    def list_rooms():
        rooms = [
            {
                "id": 1,
                "title": "Habitación centro Madrid",
                "description": "Luminoso y céntrico",
                "price": 400,
                "city": "Madrid",
                "address": "Calle Mayor, 1",
                "photo": None,
                "created_at": datetime(2025, 9, 3, 17, 59, 37, 673635).isoformat(),
                "updated_at": datetime(2025, 9, 3, 17, 59, 37, 673647).isoformat(),
            },
            {
                "id": 2,
                "title": "Habitación en Valencia",
                "description": "Cerca de la playa",
                "price": 380,
                "city": "Valencia",
                "address": "Avenida del Puerto, 22",
                "photo": None,
                "created_at": datetime(2025, 9, 3, 17, 59, 37, 673654).isoformat(),
                "updated_at": datetime(2025, 9, 3, 17, 59, 37, 673658).isoformat(),
            },
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
