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

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
UPLOAD_DIR = INSTANCE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = INSTANCE_DIR / "app.db"
SQLALCHEMY_DATABASE_URI = f"sqlite:///{DB_PATH}"

db = SQLAlchemy()


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

    # CORS (incluye localhost + vercel; añade más si lo necesitas)
    allowed_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://*.vercel.app",
        "https://*.onrender.com",
    ]
    CORS(
        app,
        resources={r"/*": {"origins": allowed_origins}},
        supports_credentials=True,
    )

    # =========================
    # Defensa activa (cañones)
    # =========================
    try:
        from defense import init_defense
        init_defense(app)
        print("[DEFENSE] Defensa activa inicializada.")
    except Exception as e:
        print("[DEFENSE] No se pudo activar defensa:", e)

    # =========================
    # Inicializa DB
    # =========================
    db.init_app(app)
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            print("[DB] Aviso: create_all falló (no crítico):", e)

    # =========================
    # Blueprints opcionales
    # =========================

    # Auth (sin dependencia de SQLAlchemy)
    try:
        from auth import bp_auth, register_auth_models
        register_auth_models(db)  # es no-op en nuestra versión
        app.register_blueprint(bp_auth)
        print("[AUTH] Blueprint auth registrado.")
    except Exception as e:
        print("[WARN] Auth module not loaded:", e)

    # Payments (Stripe) — opcional: requiere STRIPE_SECRET_KEY
    try:
        from payments import bp_pay
        if not os.getenv("STRIPE_SECRET_KEY"):
            raise RuntimeError("'STRIPE_SECRET_KEY'")
        app.register_blueprint(bp_pay)
        print("[PAY] Blueprint payments registrado.")
    except Exception as e:
        print("[WARN] Payments module not loaded:", e)

    # Opportunities (formulario de oportunidades/colaboradores)
    try:
        from opportunities import bp_opps
        app.register_blueprint(bp_opps)
        print("[OPPS] Blueprint opportunities registrado.")
    except Exception as e:
        print("[WARN] Opportunities module not loaded:", e)

    # Voice bot (Twilio) — opcional
    try:
        from voice_bot import bp_voice
        app.register_blueprint(bp_voice)
        print("[VOICE] Blueprint voice registrado.")
    except Exception as e:
        print("[WARN] Voice module not loaded:", e)

    # =========================
    # Rutas base
    # =========================

    @app.route("/health")
    def health():
        return jsonify(ok=True, service="spainroom-backend")

    # Demo Rooms (en memoria)
    @app.get("/api/rooms")
    def list_rooms():
        # Demostración simple (cuando no está la tabla persistente)
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

    # Subida de ficheros (imágenes de habitaciones, etc.)
    @app.post("/api/upload")
    def upload_file():
        if "file" not in request.files:
            return jsonify(error="Archivo requerido"), 400

        f = request.files["file"]
        if f.filename == "":
            return jsonify(error="Archivo inválido"), 400

        # Nombre seguro
        filename = secure_filename(f.filename)
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        name, ext = os.path.splitext(filename)
        safe_name = f"{name}-{ts}{ext}".replace(" ", "_")
        target = UPLOAD_DIR / safe_name
        f.save(target)

        return jsonify(ok=True, path=f"/uploads/{safe_name}")

    # Servir ficheros subidos
    @app.get("/uploads/<path:filename>")
    def serve_upload(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=False)

    return app


# =========================
# CLI / dev runner
# =========================
if __name__ == "__main__":
    app = create_app()
    print(f">>> SQLALCHEMY_DATABASE_URI = {SQLALCHEMY_DATABASE_URI}")
    # Flask Dev Server (solo desarrollo)
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5001")), debug=True)
