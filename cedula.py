# Backend SpainRoom — Módulo de CÉDULAS (completo y con CORS por endpoint)

from flask import Blueprint, request, jsonify
from flask_cors import cross_origin
import os
import sqlite3
import uuid
from datetime import datetime

# ---------------------------------------------------------------------
# Configuración de la base de datos (SQLite)
# ---------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(__file__))   # carpeta raíz del backend
DB_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DB_DIR, "cedula_checks.db")

cedula_bp = Blueprint("cedula", __name__)


def _conn():
    """Devuelve conexión SQLite con row_factory tipo dict."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Crea la tabla si no existe."""
    os.makedirs(DB_DIR, exist_ok=True)
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cedula_checks (
              id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,   -- ISO8601 con Z (ej: 2025-08-27T18:00:00Z)
              status TEXT NOT NULL,       -- 'received' (inicial)
              address TEXT,
              ref_catastral TEXT,
              email TEXT,
              city TEXT,
              comunidad TEXT
            )
            """
        )
        conn.commit()


# ---------------------------------------------------------------------
# Helpers de validación
# ---------------------------------------------------------------------
def _clean_refc(s: str) -> str:
    return (s or "").strip().upper().replace(" ", "")


def _is_valid_refc(s: str) -> bool:
    # 20 caracteres alfanuméricos
    return len(s) == 20 and s.isalnum()


# ---------------------------------------------------------------------
# Endpoints (con CORS por endpoint)
# ---------------------------------------------------------------------

# Crear verificación (acepta /check y /check/ y maneja preflight OPTIONS)
@cedula_bp.route("/check", methods=["POST", "OPTIONS"])
@cedula_bp.route("/check/", methods=["POST", "OPTIONS"])
@cross_origin(
    origins=[
        "http://127.0.0.1:5173", "http://localhost:5173",
        "http://127.0.0.1:5176", "http://localhost:5176",
        "http://127.0.0.1:5177", "http://localhost:5177",
        "http://127.0.0.1:5199", "http://localhost:5199",
    ],
    allow_headers=["Content-Type"],
    methods=["POST", "OPTIONS"]
)
def create_check():
    """
    Crea una verificación de cédula.

    Body JSON (address o ref_catastral obligatorio al menos uno):
      - address (str)
      - ref_catastral (str, 20 alfanum)
      - email (str)
      - city (str)
      - comunidad (str)

    Respuesta 201:
      { "check_id": "<uuid>", "status": "received" }
    """
    # Preflight CORS
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}

    address = (payload.get("address") or "").strip()
    refc = _clean_refc(payload.get("ref_catastral") or "")
    email = (payload.get("email") or "").strip()
    city = (payload.get("city") or "").strip()
    ccaa = (payload.get("comunidad") or "").strip()

    if not address and not refc:
        return jsonify({"error": "Debes indicar dirección o referencia catastral"}), 400
    if refc and not _is_valid_refc(refc):
        return jsonify({"error": "La referencia catastral debe tener 20 caracteres alfanuméricos"}), 400

    check_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO cedula_checks
              (id, created_at, status, address, ref_catastral, email, city, comunidad)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (check_id, now, "received", address or None, refc or None, email or None, city or None, ccaa or None),
        )
        conn.commit()

    return jsonify({"check_id": check_id, "status": "received"}), 201


# Métodos no permitidos en /check (evita 405 confuso si alguien hace GET)
@cedula_bp.route("/check", methods=["GET", "PUT", "PATCH", "DELETE"])
@cedula_bp.route("/check/", methods=["GET", "PUT", "PATCH", "DELETE"])
@cross_origin()
def check_wrong_method():
    return jsonify({"error": "Usa POST en /api/cedula/check"}), 405


# Obtener una verificación por ID (con y sin barra final)
@cedula_bp.route("/check/<check_id>", methods=["GET"])
@cedula_bp.route("/check/<check_id>/", methods=["GET"])
@cross_origin(
    origins=[
        "http://127.0.0.1:5173", "http://localhost:5173",
        "http://127.0.0.1:5176", "http://localhost:5176",
        "http://127.0.0.1:5177", "http://localhost:5177",
        "http://127.0.0.1:5199", "http://localhost:5199",
    ],
    methods=["GET"]
)
def get_check(check_id: str):
    with _conn() as conn:
        cur = conn.execute("SELECT * FROM cedula_checks WHERE id = ?", (check_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "No existe la verificación solicitada"}), 404
        return jsonify(dict(row))


# Listar verificaciones (paginación). Acepta /list y /list/
@cedula_bp.route("/list", methods=["GET"])
@cedula_bp.route("/list/", methods=["GET"])
@cross_origin(
    origins=[
        "http://127.0.0.1:5173", "http://localhost:5173",
        "http://127.0.0.1:5176", "http://localhost:5176",
        "http://127.0.0.1:5177", "http://localhost:5177",
        "http://127.0.0.1:5199", "http://localhost:5199",
    ],
    methods=["GET"]
)
def list_checks():
    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
        limit = max(1, min(200, limit))
        offset = max(0, offset)
    except Exception:
        return jsonify({"error": "Parámetros de paginación inválidos"}), 400

    with _conn() as conn:
        cur = conn.execute(
            "SELECT * FROM cedula_checks ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        items = [dict(r) for r in cur.fetchall()]

    return jsonify({"items": items, "limit": limit, "offset": offset})
