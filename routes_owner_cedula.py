# routes_owner_cedula.py — Verificación por Dirección / Ref. Catastral + upload (POST/OPTIONS)
import os, uuid, json, unicodedata
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app

bp_owner = Blueprint("owner_cedula", __name__)

# ===================== Reglas (conservadoras) por provincia/CCAA =====================
# Obligatorio (en la práctica): Catalunya, C. Valenciana, Illes Balears
PROVS_OBLIGATORIAS = {
    # Catalunya
    "barcelona","girona","lleida","tarragona",
    # Comunitat Valenciana
    "valencia","alicante","castellon","castellón",
    # Illes Balears
    "illes balears","islas baleares","balears","mallorca","menorca","ibiza","eivissa","formentera",
}

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")  # quita acentos
    return s

def _classify_requirement(provincia: str | None):
    """Devuelve requirement dict: cat (si/depende/no), doc, org, vig, notas, link"""
    p = _norm(provincia or "")
    if p in PROVS_OBLIGATORIAS:
        if p in {"barcelona","girona","lleida","tarragona"}:
            return {
                "cat": "si",
                "doc": "Cèdula d'habitabilitat (o 2a ocupació) vigent",
                "org": "Generalitat de Catalunya / Ajuntament",
                "vig": "Normalmente 15 años; revisar fecha exacta del documento",
                "notas": "En Catalunya es requisito habitual para arrendamiento y alta de suministros.",
                "link": "https://habitatge.gencat.cat/ca/ambits/rehabilitacio/certificats/certificat-habitabilitat/"
            }
        if p in {"valencia","alicante","castellon","castellón"}:
            return {
                "cat": "si",
                "doc": "Licencia de 2ª ocupación / declaración responsable (según municipio)",
                "org": "Ajuntament / Generalitat Valenciana",
                "vig": "Varía por municipio; muchas veces 5–10 años",
                "notas": "En la Comunitat Valenciana suele exigirse licencia/2ª ocupación para alquiler.",
                "link": "https://www.gva.es/va/inicio/procedimientos?id_proc=18592"  # genérico
            }
        # Illes Balears
        return {
            "cat": "si",
            "doc": "Cédula de habitabilidad (GOIB) o trámite municipal equivalente",
            "org": "GOIB / Ajuntament",
            "vig": "Habitual 10 años; confirmar en el documento",
            "notas": "En Illes Balears se exige de forma general para alquiler y altas.",
            "link": "https://www.caib.es/sites/habitatge/ca/cedula_habitabilitat/"
        }

    # Resto CCAA: depende (municipio/antigüedad/uso). Evitamos afirmar 'no'
    return {
        "cat": "depende",
        "doc": "Según municipio: LPO/2ª ocupación o declaración responsable",
        "org": "Ayuntamiento competente",
        "vig": "Según la resolución o normativa local",
        "notas": "Requisito variable por municipio/antigüedad. Te ayudamos a confirmarlo con el ayuntamiento.",
        "link": ""
    }

# ============================== Utilidades de persistencia mínima ==============================
def _ok(**kw): return jsonify({"ok": True, **kw})

def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path

def _instance_dir():
    base = current_app.instance_path
    return _ensure_dir(os.path.join(base, "owner_checks"))

def _new_check_id():
    d = datetime.utcnow()
    return f"SRV-{d:%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"

def _save_meta(check_id, payload):
    folder = _ensure_dir(os.path.join(_instance_dir(), check_id))
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def _allowed_file(fn):
    fn = (fn or "").lower()
    return any(fn.endswith(ext) for ext in (".pdf",".png",".jpg",".jpeg"))

# ============================== ENDPOINTS ==============================

# 1) Registrar el “check” (lead) — usa dirección o ref. catastral
@bp_owner.route("/api/owner/check", methods=["POST", "OPTIONS"])
def owner_check():
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}

    # Mínimos: nombre y teléfono
    nombre   = (body.get("nombre") or "").strip()
    telefono = (body.get("telefono") or "").replace(" ", "")
    if len(nombre.split()) < 2:
        return jsonify(ok=False, error="Nombre incompleto"), 400
    if not telefono or not any(ch.isdigit() for ch in telefono):
        return jsonify(ok=False, error="Teléfono inválido"), 400

    check_id = _new_check_id()
    payload = {
        "id": check_id,
        "ts": datetime.utcnow().isoformat(),
        "tipo": body.get("tipo") or "check_cedula",
        "via":  body.get("via")  or ("catastro" if (body.get("refcat") or "").strip() else "direccion"),
        "status": body.get("status") or "pendiente",
        "contacto": {
            "nombre": nombre, "telefono": telefono, "email": (body.get("email") or "").strip()
        },
        "direccion": {
            "direccion": body.get("direccion"),
            "cp": body.get("cp"),
            "municipio": body.get("municipio"),
            "provincia": body.get("provincia"),
            "refcat": body.get("refcat"),
        }
    }
    # Persistencia mínima (JSON en instance/)
    try:
        _save_meta(check_id, payload)
    except Exception as e:
        current_app.logger.warning("owner_check: no se pudo guardar meta: %s", e)

    # Si nos dan provincia, devolvemos ya el requirement
    req = _classify_requirement(body.get("provincia"))
    return _ok(id=check_id, requirement=req)

# 2) Verificación por DIRECCIÓN → dict de requirement
@bp_owner.route("/api/owner/cedula/verify/direccion", methods=["POST", "OPTIONS"])
def verify_direccion():
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}
    direccion = (body.get("direccion") or "").strip()
    municipio = (body.get("municipio") or "").strip()
    provincia = (body.get("provincia") or "").strip()

    req = _classify_requirement(provincia)
    return _ok(
        requirement=req,
        input={"direccion": direccion, "municipio": municipio, "provincia": provincia}
    )

# 3) Verificación por REFERENCIA CATASTRAL
#    - Valida formato; si hay provincia, aplica la misma regla que por dirección.
@bp_owner.route("/api/owner/cedula/verify/catastro", methods=["POST", "OPTIONS"])
def verify_catastro():
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}
    refcat = (body.get("refcat") or "").strip()
    provincia = (body.get("provincia") or "").strip()

    # Validación sintáctica básica de ref catastral (20 alfanuméricos)
    fmt_ok = len(refcat) == 20 and refcat.isalnum()
    fmt_msg = "OK (20 alfanuméricos)" if fmt_ok else "Formato no válido (debe tener 20 caracteres alfanuméricos)"

    req = _classify_requirement(provincia) if provincia else {
        "cat": "depende",
        "doc": "Según municipio: LPO/2ª ocupación o declaración responsable",
        "org": "Ayuntamiento competente",
        "vig": "Según normativa local",
        "notas": "Aporta provincia para un criterio más exacto.",
        "link": ""
    }

    return _ok(
        requirement=req,
        input={"refcat": refcat, "provincia": provincia, "formato": fmt_msg}
    )

# 4) Verificación por Nº CÉDULA (opcional). Útil si el propietario ya la tiene.
@bp_owner.route("/api/owner/cedula/verify/numero", methods=["POST", "OPTIONS"])
def verify_numero():
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}
    numero = (body.get("numero") or "").strip()
    # Sin consulta externa: aceptamos nº y devolvemos eco. El requirement lo marca la provincia en otros endpoints.
    status = "recibido" if numero else "sin_numero"
    return _ok(status=status, data={"numero": numero})

# 5) Upload de documento (opcional, para adjuntar escritura / cédula / PDF)
@bp_owner.route("/api/owner/cedula/upload", methods=["POST", "OPTIONS"])
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
    safe = f.filename.replace("/", "_").replace("\\", "_")
    dst = os.path.join(folder, safe)
    f.save(dst)

    rel = f"/instance/owner_checks/{check_id}/{safe}"  # servible por tu backend si expones /instance/*
    return _ok(doc_url=rel)

# 6) Consultar un check guardado (opcional, para panel interno)
@bp_owner.route("/api/owner/check/<check_id>", methods=["GET"])
def get_check(check_id):
    folder = os.path.join(_instance_dir(), check_id)
    meta = os.path.join(folder, "meta.json")
    if not os.path.exists(meta):
        return jsonify(ok=False, error="not_found"), 404
    with open(meta, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _ok(**data)
