# routes_upload_generic.py
import os, hashlib
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app
from extensions import db

bp_upload_generic = Blueprint("upload_generic", __name__)

def _ensure_dir(p): os.makedirs(p, exist_ok=True)
def _sha256_bytes(b: bytes) -> str: h=hashlib.sha256(); h.update(b); return h.hexdigest()

@bp_upload_generic.post("/api/upload")
def upload_generic():
    """
    form-data:
      role: tenant|owner|franchise_app|franq|room
      subject_id: tel/email/ref/app_key
      category: dni|factura_movil|selfie|cedula|plan|titular|otros
      file: fichero
    """
    role = (request.form.get("role") or "other").strip()
    subject_id = (request.form.get("subject_id") or "").strip()
    category = (request.form.get("category") or "otros").strip()
    fs = request.files.get("file")
    if not subject_id or not fs:
        return jsonify(ok=False, error="missing_fields"), 400

    b = fs.read()
    if not b:
        return jsonify(ok=False, error="empty_file"), 400
    hexname = _sha256_bytes(b)[:16]

    yyyymm = datetime.utcnow().strftime("%Y%m")
    base_dir = os.path.join(current_app.instance_path, "uploads", role, subject_id, yyyymm)
    _ensure_dir(base_dir)

    ext = ".bin"
    if fs.filename and "." in fs.filename:
        ext = "." + fs.filename.rsplit(".",1)[-1].lower()
        if len(ext) > 8: ext = ".bin"
    fname = f"{category}_{hexname}{ext}"
    fpath = os.path.join(base_dir, fname)
    with open(fpath, "wb") as f:
        f.write(b)

    rel = f"uploads/{role}/{subject_id}/{yyyymm}/{fname}"
    # Si quieres, registra en una tabla Upload genérica (opcional).
    return jsonify(ok=True, file={"role":role,"subject":subject_id,"category":category,"path": f"/instance/{rel}"})
