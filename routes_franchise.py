# routes_franchise.py
import os, hashlib, secrets
from datetime import datetime
from io import BytesIO
from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models_franchise import FranchiseApplication, FranchiseUpload

bp_franchise = Blueprint("franchise", __name__)

def _ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def _sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256(); h.update(b); return h.hexdigest()

@bp_franchise.post("/api/franchise/apply")
def franchise_apply():
    """
    Candidatura pública de franquiciado (pre-onboarding).
    body: { nombre, telefono, email, zona, mensaje }
    """
    data = request.get_json(force=True)
    nombre  = (data.get("nombre") or "").strip()
    telefono= (data.get("telefono") or "").strip()
    email   = (data.get("email") or "").strip()
    zona    = (data.get("zona") or "").strip()
    mensaje = (data.get("mensaje") or "").strip()

    if len(nombre.split()) < 2:
        return jsonify(ok=False, error="bad_nombre"), 400

    app_key = secrets.token_urlsafe(12)  # subjectId para vincular docs
    row = FranchiseApplication(
        nombre=nombre, telefono=telefono, email=email, zona=zona, mensaje=mensaje,
        status="received", app_key=app_key
    )
    db.session.add(row); db.session.commit()
    return jsonify(ok=True, app_id=app_key, id=row.id)

@bp_franchise.post("/api/franchise/apply/upload")
def franchise_apply_upload():
    """
    Subida de documentación vinculada a la candidatura (pre-onboarding).
    form-data:
      - subject_id: app_key (devuelto por /apply)
      - category: dni|plan|titular|otros
      - file: fichero
    """
    subject_id = (request.form.get("subject_id") or "").strip()
    category   = (request.form.get("category") or "otros").strip()
    fs         = request.files.get("file")
    if not subject_id or not fs:
        return jsonify(ok=False, error="missing_fields"), 400

    app_row = FranchiseApplication.query.filter_by(app_key=subject_id).first()
    if not app_row:
        return jsonify(ok=False, error="app_not_found"), 404

    yyyymm = datetime.utcnow().strftime("%Y%m")
    base_dir = os.path.join(current_app.instance_path, "uploads", "franchise", subject_id, yyyymm)
    _ensure_dir(base_dir)

    b = fs.read()
    if not b:
        return jsonify(ok=False, error="empty_file"), 400
    hexname = _sha256_bytes(b)[:16]
    ext = ".bin"
    if fs.filename and "." in fs.filename:
        ext = "." + fs.filename.rsplit(".",1)[-1].lower()
        if len(ext) > 8: ext = ".bin"

    fname = f"{category}_{hexname}{ext}"
    fpath = os.path.join(base_dir, fname)
    with open(fpath, "wb") as f:
        f.write(b)

    rel = f"uploads/franchise/{subject_id}/{yyyymm}/{fname}"
    up = FranchiseUpload(app_key=subject_id, category=category, path=rel, mime=fs.mimetype, size_bytes=len(b), sha256=hexname)
    db.session.add(up); db.session.commit()
    return jsonify(ok=True, file={"category": category, "path": f"/instance/{rel}"})
