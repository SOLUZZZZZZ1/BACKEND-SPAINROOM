# routes_owner.py — Registro de verificación y carga de documentos
# Nora · 2025-10-11
import time
from flask import Blueprint, request, jsonify, Response
from werkzeug.utils import secure_filename

bp_owner = Blueprint("owner", __name__)

def _corsify(resp: Response) -> Response:
    origin = request.headers.get("Origin", "*")
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Key"
    return resp

def _require_admin():
    # Sencillo control: cabecera X-Admin-Key (igual que usa el front)
    admin = request.headers.get("X-Admin-Key")
    expected = "ramon"  # o usa os.getenv("ADMIN_KEY")
    return admin == expected

@bp_owner.route("/api/owner/check", methods=["POST","OPTIONS"])
def owner_check():
    if request.method == "OPTIONS":
        return _corsify(Response(status=204))
    if not _require_admin():
        return _corsify(jsonify(ok=False, error="unauthorized")), 401
    data = request.get_json(silent=True) or {}
    # DEMO: devolvemos un ID de verificación generado
    check_id = f"CHK-{int(time.time())}"
    return _corsify(jsonify(ok=True, id=check_id))

@bp_owner.route("/api/owner/cedula/upload", methods=["POST","OPTIONS"])
def owner_upload():
    if request.method == "OPTIONS":
        return _corsify(Response(status=204))
    if not _require_admin():
        return _corsify(jsonify(ok=False, error="unauthorized")), 401
    file = request.files.get("file")
    if not file:
        return _corsify(jsonify(ok=False, error="no file")), 400
    name = secure_filename(file.filename or "cedula.pdf")
    # DEMO: no guardamos en disco en este ejemplo; solo respondemos OK
    return _corsify(jsonify(ok=True, stored=name))
