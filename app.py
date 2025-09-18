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
    # Defensa activa (opcional)
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

    # Importar modelos de Franquicia si el flag está activo (para que create_all cree tablas)
    try:
        if os.getenv("BACKEND_FEATURE_FRANQ_PLAZAS", "off").lower() == "on":
            from franquicia import models as _fr_models  # noqa: F401
            print("[FRANQ] Modelos de Franquicia importados (flag ON).")
        else:
            print("[FRANQ] Flag OFF: no se importan modelos de Franquicia.")
    except Exception as e:
        print("[WARN] No se pudieron importar modelos de Franquicia:", e)

    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            print("[DB] Aviso: create_all falló (no crítico):", e)

    # =========================
    # Blueprints opcionales existentes
    # =========================

    # Auth (sin dependencia de SQLAlchemy)
    try:
        from auth import bp_auth, register_auth_models
        register_auth_models(db)  # es no-op en algunas versiones
