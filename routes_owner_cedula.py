# routes_owner_cedula.py — Owner endpoints (check + upload) protegidos por ADMIN_API_KEY/ADMIN_KEY
# Nora · 2025-10-11
import os, uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, current_app

bp_owner = Blueprint("owner", __name__)

ADMIN_KEY = (os.getenv("ADMIN_API_KEY") or os.getenv("ADMIN_KEY") or "").strip()

def _authorized() -> bool:
    if not ADMIN_KEY:
        return True  # modo dev si no hay clave en entorno
    return request.headers.get("X-Admin-Key") == ADMIN_KEY

@bp_owner.route("/check", methods=["POST","OPTIONS"])
def check():
    if request.method == "OPTIONS":
        return ("", 204)
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    return jsonify(ok=True, id="SRV-CHK-" + uuid.uuid4().hex[:8])

@bp_owner.route("/cedula/upload", methods=["POST","OPTIONS"])
def upload():
    if request.method == "OPTIONS":
        return ("", 204)
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401

    f = request.files.get("file")
    if not f:
        return jsonify(ok=False, error="no_file"), 400

    up = Path(current_app.instance_path) / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    tgt = up / f.filename
    f.save(tgt)

    return jsonify(ok=True, filename=f.filename)
