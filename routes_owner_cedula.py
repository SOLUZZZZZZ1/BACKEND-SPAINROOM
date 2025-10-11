# routes_owner_cedula.py — Owner endpoints (check + upload) protegidos por ADMIN_API_KEY/ADMIN_KEY
# Nora · 2025-10-11
#
# Rutas (se registran en app.py con prefijo /api/owner):
#   POST /api/owner/check           -> { ok, id }
#   POST /api/owner/cedula/upload   -> { ok, filename }
#
# Seguridad:
#   - Lee la clave desde ADMIN_API_KEY o ADMIN_KEY (entorno Render).
#   - Exige cabecera: X-Admin-Key: <clave>
#   - Si no hay clave en entorno, queda en modo abierto (útil en dev).

import os, uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, current_app

bp_owner = Blueprint("owner", __name__)

ADMIN_KEY = (
    os.getenv("ADMIN_API_KEY") or
    os.getenv("ADMIN_KEY") or
    ""
).strip()

def _authorized() -> bool:
    if not ADMIN_KEY:
        # Sin clave definida en entorno => modo abierto (dev)
        return True
    return request.headers.get("X-Admin-Key") == ADMIN_KEY

@bp_owner.route("/check", methods=["POST","OPTIONS"])
def check():
    # Preflight
    if request.method == "OPTIONS":
        return ("", 204)
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    # Genera un ID simple para trazabilidad del proceso
    return jsonify(ok=True, id="SRV-CHK-" + uuid.uuid4().hex[:8])

@bp_owner.route("/cedula/upload", methods=["POST","OPTIONS"])
def upload():
    # Preflight
    if request.method == "OPTIONS":
        return ("", 204)
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401

    f = request.files.get("file")
    if not f:
        return jsonify(ok=False, error="no_file"), 400

    # Guardado básico en /instance/uploads (creado por app.py)
    up = Path(current_app.instance_path) / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    # Nombre tal cual (si quieres reforzar: usar secure_filename)
    tgt = up / f.filename
    f.save(tgt)

    return jsonify(ok=True, filename=f.filename)
