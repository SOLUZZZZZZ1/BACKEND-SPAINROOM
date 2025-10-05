# routes_rooms_sheet_json.py
from datetime import datetime
import os, json, hashlib
from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models_rooms import Room
from models_contracts import Contract, ContractItem
from models_uploads import Upload

bp_rooms_sheet_json = Blueprint("rooms_sheet_json", __name__)

def _ensure_dir(p): os.makedirs(p, exist_ok=True)
def _sha(b: bytes) -> str:
    h = hashlib.sha256(); h.update(b); return h.hexdigest()

def _find_by_ref(sub_ref: str, ref: str, room_code: str):
    sub_ref = (sub_ref or "").strip().upper()
    ref     = (ref or "").strip().upper()
    room    = None

    if sub_ref:
        it = ContractItem.query.filter_by(sub_ref=sub_ref).first()
        if not it: return None, None, None
        c = it.contract
        room = db.session.get(Room, it.room_id)
        return c, it, room

    if not ref or not room_code:
        return None, None, None

    c = Contract.query.filter_by(ref=ref).first()
    if not c: return None, None, None
    room = Room.query.filter_by(code=(room_code or "").strip()).first()
    if not room: return None, None, None
    it = ContractItem.query.filter_by(contract_id=c.id, room_id=room.id).first()
    if not it: return None, None, None
    return c, it, room

def _authorize(contract: Contract, item: ContractItem, franq_header: str):
    need = item.franchisee_id or contract.franchisee_id
    if not need: return True, None
    if not franq_header: return False, "missing_franquiciado"
    if franq_header != need: return False, "forbidden_franquiciado"
    return True, None

def _yyyymm(): return datetime.utcnow().strftime("%Y%m")

@bp_rooms_sheet_json.post("/api/rooms/sheet_json")
def submit_sheet_json():
    # Recibe ficha JSON (en vez de PDF) y la guarda de forma estructurada y versionada.
    # headers: X-Franquiciado (si aplica)
    data = request.get_json(silent=True) or {}
    sub_ref = (data.get("sub_ref") or "").strip().upper()
    ref     = (data.get("ref") or "").strip().upper()
    rcode   = (data.get("room_code") or "").strip()
    sheet   = data.get("sheet") or {}
    update_core = bool(data.get("update_core", True))
    form_id = (data.get("form_id") or "").strip()

    if not sheet or not isinstance(sheet, dict):
        return jsonify(ok=False, error="missing_sheet"), 400

    franq = (request.headers.get("X-Franquiciado") or "").strip()

    contract, item, room = _find_by_ref(sub_ref, ref, rcode)
    if not (contract and item and room):
        return jsonify(ok=False, error="contract_item_not_found"), 404

    ok, err = _authorize(contract, item, franq)
    if not ok:
        msg = "Falta franquiciado en cabecera." if err == "missing_franquiciado" else "No autorizado para esta habitación."
        return jsonify(ok=False, error=err, message=msg), 403

    # Normalización
    def _to_int(v):
        try:
            return int(v)
        except Exception:
            return v

    norm = {
        "cama": str(sheet.get("cama") or ""),
        "ventana": bool(sheet.get("ventana")),
        "cerradura": bool(sheet.get("cerradura")),
        "escritorio": bool(sheet.get("escritorio")),
        "enchufes": str(sheet.get("enchufes") or ""),
        "bano_privado": bool(sheet.get("bano_privado")),
        "superficie_m2": _to_int(sheet.get("superficie_m2") or 0),
        "precio": _to_int(sheet.get("precio") or 0),
        "estado": (sheet.get("estado") or "").strip() or None,
        "fecha_disponibilidad": (sheet.get("fecha_disponibilidad") or "").strip() or None,
        "barrio": (sheet.get("barrio") or "").strip(),
        "orientacion": (sheet.get("orientacion") or "").strip(),
        "planta": (sheet.get("planta") or "").strip(),
        "metro": (sheet.get("metro") or "").strip(),
        "consumos_incluidos": bool(sheet.get("consumos_incluidos")),
        "normas": (sheet.get("normas") or "").strip(),
        "otros": (sheet.get("otros") or "").strip(),
        "descripcion": (sheet.get("descripcion") or "").strip(),
    }

    images = room.images_json or {}
    meta = images.get("meta", {})
    meta.update(norm)
    images["meta"] = meta

    # Generar form_id vinculado a la línea de contrato (sub_ref)
    base_id = (item.sub_ref or "FORM").replace(" ", "")
    if not form_id:
        existing = [f for f in (images.get("forms", []) or []) if str(f.get("form_id","")).startswith(base_id+"-F")]
        idx = (len(existing) or 0) + 1
        form_id = f"{base_id}-F{idx:03d}"

    # Persistir JSON y registrar Upload
    yyyymm = _yyyymm()
    base_dir = os.path.join(current_app.instance_path, "uploads", "contracts", contract.ref, item.sub_ref, "sheets", "json", yyyymm)
    _ensure_dir(base_dir)
    payload = {
        "form_id": form_id, "sub_ref": item.sub_ref, "ref": contract.ref, "room_code": room.code,
        "ts": datetime.utcnow().isoformat(), "sheet": norm
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    sha = _sha(raw)[:16]
    fname = f"sheet_{form_id}_{sha}.json"
    with open(os.path.join(base_dir, fname), "wb") as f: f.write(raw)
    rel = f"/instance/uploads/contracts/{contract.ref}/{item.sub_ref}/sheets/json/{yyyymm}/{fname}"

    up = Upload(role="room", subject_id=item.sub_ref, category="room_sheet_json",
                path=f"uploads/contracts/{contract.ref}/{item.sub_ref}/sheets/json/{yyyymm}/{fname}",
                mime="application/json", size_bytes=len(raw), sha256=sha)
    db.session.add(up)

    forms = images.get("forms", []); forms.append({"form_id": form_id, "ts": payload["ts"], "url": rel, "sha": sha, "type":"json"})
    images["forms"] = forms; images["sheet"] = {"url": rel, "sha": sha, "ts": payload["ts"], "form_id": form_id, "type":"json"}

    if update_core:
        if isinstance(norm.get("superficie_m2"), int) and norm["superficie_m2"]:
            room.m2 = norm["superficie_m2"]
        if isinstance(norm.get("precio"), int) and norm["precio"]:
            room.precio = norm["precio"]
        if norm.get("estado"):
            room.estado = norm["estado"]
        desc = norm.get("descripcion") or ""
        room.notas = (desc[:240] + ("…" if len(desc) > 240 else "")) or room.notas

    room.images_json = images
    db.session.commit()

    return jsonify(ok=True, form_id=form_id, room={"id": room.id, "code": room.code}, sheet=norm, url=rel)

@bp_rooms_sheet_json.get("/api/rooms/<room_id_or_code>/sheet")
def get_sheet(room_id_or_code):
    room = None
    if str(room_id_or_code).isdigit():
        room = db.session.get(Room, int(room_id_or_code))
    if not room:
        room = Room.query.filter_by(code=str(room_id_or_code)).first()
    if not room:
        return jsonify(ok=False, error="not_found"), 404
    images = room.images_json or {}
    meta = images.get("meta", {}); latest = images.get("sheet"); forms = images.get("forms", [])
    return jsonify(ok=True, room={"id": room.id, "code": room.code}, meta=meta, latest=latest, count=len(forms))
