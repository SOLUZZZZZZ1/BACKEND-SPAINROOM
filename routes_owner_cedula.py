# routes_owner_cedula.py — Verificación de cédula (owner) + upload
import os, uuid, json
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app

bp_owner = Blueprint("owner_cedula", __name__)

ALLOWED_ORIGINS = {
    "http://localhost:5176", "http://127.0.0.1:5176",
    "http://localhost:5173", "http://127.0.0.1:5173",
}
PROVS_OBLIGATORIAS = {
    "barcelona","girona","lleida","tarragona",
    "valencia","alicante","castellon","castellón",
    "illes balears","islas baleares","balears",
}

def _ok(data=None, **kw):
    out = {"ok": True}
    if data: out.update(data)
    out.update(kw)
    return jsonify(out)

def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path

def _instance_dir():
    # ubicación segura para ficheros
    base = current_app.instance_path
    return _ensure_dir(os.path.join(base, "owner_checks"))

def _new_check_id():
    d = datetime.utcnow()
    return f"SRV-{d:%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"

def _save_meta(check_id, payload):
    root = _instance_dir()
    folder = _ensure_dir(os.path.join(root, check_id))
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def _allowed_file(fn):
    fn = (fn or "").lower()
    return (fn.endswith(".pdf") or fn.endswith(".png")
            or fn.endswith(".jpg") or fn.endswith(".jpeg"))

def _abs_url(path):
    # Devolvemos una ruta relativa /instance/...; tu Nginx/Flask ya la sirve
    return path

def _fallback_status(refcat=None, provincia=""):
    if refcat and refcat.strip():
        last = refcat.strip()[-1]
        if last.isdigit() and int(last) % 2 == 0:
            return "valida"
        return "no_encontrada"
    provk = (provincia or "").strip().lower()
    return "depende" if provk in PROVS_OBLIGATORIAS else "no_encontrada"

# ──────────────────────────────────────────────────────────────────────────────
# 1) Registrar comprobación (lead)
# ──────────────────────────────────────────────────────────────────────────────
@bp_owner.route("/api/owner/check", methods=["POST","OPTIONS"])
def owner_check():
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}
    check_id = _new_check_id()
    payload = {
        "id": check_id,
        "ts": datetime.utcnow().isoformat(),
        "tipo": body.get("tipo") or "check_cedula",
        "via":  body.get("via")  or "direccion",
        "status": body.get("status") or "pendiente",
        "nombre": body.get("nombre"),
        "telefono": body.get("telefono"),
        "email": body.get("email"),
        "ciudad": body.get("ciudad"),
        "comunidad": body.get("comunidad"),
        "refcat": body.get("refcat"),
        "direccion": body.get("direccion"),
    }
    try:
        _save_meta(check_id, payload)
    except Exception as e:
        current_app.logger.warning("owner_check: no pude guardar meta: %s", e)
    return _ok(id=check_id)

# ──────────────────────────────────────────────────────────────────────────────
# 2) Verificación por número
# ──────────────────────────────────────────────────────────────────────────────
@bp_owner.route("/api/owner/cedula/verify/numero", methods=["POST","OPTIONS"])
def verify_numero():
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}
    numero = (body.get("numero") or "").strip()
    status = "valida" if numero.endswith(("OK","ok")) else "no_encontrada"
    return _ok(status=status, data={"numero": numero})

# ──────────────────────────────────────────────────────────────────────────────
# 3) Verificación por catastro
# ──────────────────────────────────────────────────────────────────────────────
@bp_owner.route("/api/owner/cedula/verify/catastro", methods=["POST","OPTIONS"])
def verify_catastro():
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}
    refcat = (body.get("refcat") or "").strip()
    status = _fallback_status(refcat=refcat)
    return _ok(status=status, data={"refcat": refcat})

# ──────────────────────────────────────────────────────────────────────────────
# 4) Verificación por dirección
# ──────────────────────────────────────────────────────────────────────────────
@bp_owner.route("/api/owner/cedula/verify/direccion", methods=["POST","OPTIONS"])
def verify_direccion():
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}
    direccion = (body.get("direccion") or "").strip()
    municipio = (body.get("municipio") or "").strip()
    provincia = (body.get("provincia") or "").strip()
    status = _fallback_status(provincia=provincia)
    return _ok(status=status, data={
        "direccion": direccion, "municipio": municipio, "provincia": provincia
    })

# ──────────────────────────────────────────────────────────────────────────────
# 5) Upload de copia (PDF/JPG/PNG)
# ──────────────────────────────────────────────────────────────────────────────
@bp_owner.route("/api/owner/cedula/upload", methods=["POST","OPTIONS"])
def upload_copy():
    if request.method == "OPTIONS":
        return ("", 204)
    check_id = (request.form.get("check_id") or "").strip()
    f = request.files.get("file")
    if not check_id or not f:
        return jsonify(ok=False, error="missing_params"), 400
    if not _allowed_file(f.filename):
        return jsonify(ok=False, error="invalid_ext"), 400

    folder = _ensure_dir(os.path.join(_instance_dir(), check_id))
    safe_name = f.filename.replace("/", "_").replace("\\", "_")
    dst = os.path.join(folder, safe_name)
    f.save(dst)

    # Ruta accesible por el front si sirves /instance/*
    rel = f"/instance/owner_checks/{check_id}/{safe_name}"
    return _ok(doc_url=_abs_url(rel))
