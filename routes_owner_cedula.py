
from flask import Blueprint, request, jsonify, current_app
from pathlib import Path

bp_owner = Blueprint("owner", __name__)

@bp_owner.route("/check", methods=["POST","OPTIONS"])
def check():
    if request.method == "OPTIONS": return ("",204)
    import uuid
    return jsonify(ok=True, id="SRV-CHK-" + uuid.uuid4().hex[:8])

@bp_owner.route("/cedula/upload", methods=["POST","OPTIONS"])
def upload():
    if request.method == "OPTIONS": return ("",204)
    f = request.files.get("file")
    if not f: return jsonify(ok=False, error="no_file"), 400
    up = Path(current_app.instance_path) / "uploads"; up.mkdir(parents=True, exist_ok=True)
    tgt = up / f.filename; f.save(tgt)
    return jsonify(ok=True, filename=f.filename)
