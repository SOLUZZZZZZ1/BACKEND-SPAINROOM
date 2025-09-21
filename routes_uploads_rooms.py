# routes_uploads_rooms.py
import os, hashlib
from datetime import datetime
from io import BytesIO
from PIL import Image
from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models_contracts import Contract, ContractItem
from models_rooms import Room
from models_uploads import Upload

bp_upload_rooms = Blueprint("upload_rooms", __name__)

# ---------- Helpers ----------
def ensure_dir(p): os.makedirs(p, exist_ok=True)
def sha256_bytes(b: bytes) -> str: h=hashlib.sha256(); h.update(b); return h.hexdigest()

def process_image(file_storage, max_w=1600, thumb_w=480):
    """
    Devuelve {w,h, full(bytes JPEG), thumb(bytes JPEG)}
    """
    im = Image.open(file_storage.stream).convert("RGB")
    w, h = im.size
    # full
    if w > max_w:
        ratio = max_w / float(w)
        im_full = im.resize((max_w, int(h*ratio)), Image.LANCZOS)
    else:
        im_full = im.copy()
    # thumb
    if w > thumb_w:
        ratio = thumb_w / float(w)
        im_thumb = im.resize((thumb_w, int(h*ratio)), Image.LANCZOS)
    else:
        im_thumb = im.copy()
    bf = BytesIO(); im_full.save(bf, "JPEG", quality=88, optimize=True); full = bf.getvalue()
    bt = BytesIO(); im_thumb.save(bt, "JPEG", quality=82, optimize=True); thumb = bt.getvalue()
    return {"w": im_full.size[0], "h": im_full.size[1], "full": full, "thumb": thumb}

def get_contract_item(sub_ref: str, ref: str, room_code: str):
    """
    Localiza la línea del contrato (ContractItem) por:
      - sub_ref (preferido)  o
      - ref + room_code
    Devuelve (contract, item, room) o (None, None, None)
    """
    item = None
    contract = None
    room = None
    if sub_ref:
        item = ContractItem.query.filter_by(sub_ref=sub_ref).first()
        if item:
            contract = item.contract
            room = db.session.get(Room, item.room_id)
            return contract, item, room
        return None, None, None
    # ref + room_code
    if not ref or not room_code:
        return None, None, None
    contract = Contract.query.filter_by(ref=ref).first()
    if not contract: return None, None, None
    room = Room.query.filter_by(code=room_code).first()
    if not room: return None, None, None
    item = ContractItem.query.filter_by(contract_id=contract.id, room_id=room.id).first()
    return contract, item, room

# ---------- Endpoints ----------
@bp_upload_rooms.post("/api/rooms/upload_photos")
def upload_room_photos():
    """
    Subida de FOTOS. Reglas:
      - Requiere contrato firmado (contract.status == 'signed')
      - Localiza ContractItem por:
          * sub_ref=SR-XXXXX-01   (preferido)  o
          * ref=SR-XXXXX + room_code=ROOM-YYY
      - Acepta 1 o varias fotos (campo 'file' o 'files[]')
      - Publica la habitación (room.published = True) al menos con 1 foto
    form-data:
      sub_ref?   | ref? + room_code?
      file       | files[] múltiples
    headers:
      X-Franquiciado (opcional validar con contract.franchisee_id)
    """
    sub  = (request.form.get("sub_ref") or "").strip().upper()
    ref  = (request.form.get("ref") or "").strip().upper()
    rcode= (request.form.get("room_code") or "").strip()
    franq= (request.headers.get("X-Franquiciado") or "").strip()

    # Archivos: soporta 'file' y 'files[]'
    files = []
    if "file" in request.files:
        files.append(request.files["file"])
    if "files[]" in request.files:
        # werkzeug agrupa múltiples bajo el mismo nombre con getlist
        files.extend(request.files.getlist("files[]"))
    # también soporta múltiples 'file' si el cliente los repite
    for k, fs in request.files.items(multi=True):
        if k not in ("file", "files[]"):  # no duplicar los ya cogidos
            files.append(fs)

    if not files:
        return jsonify(ok=False, error="missing_files"), 400

    contract, item, room = get_contract_item(sub, ref, rcode)
    if not item or not contract or not room:
        return jsonify(ok=False, error="contract_item_not_found"), 404
    if contract.status != "signed":
        return jsonify(ok=False, error="contract_not_signed"), 403
    if contract.franchisee_id and franq and franq != contract.franchisee_id:
        return jsonify(ok=False, error="forbidden_franquiciado"), 403

    yyyymm = datetime.utcnow().strftime("%Y%m")
    base_dir = os.path.join(current_app.instance_path, "uploads", "contracts", contract.ref, item.sub_ref, yyyymm)
    ensure_dir(base_dir)

    added = []
    for fs in files:
        try:
            im = process_image(fs)
            hexname = sha256_bytes(im["full"])[:16]
            full_name, thumb_name = f"{hexname}.jpg", f"{hexname}_t.jpg"
            with open(os.path.join(base_dir, full_name), "wb") as f: f.write(im["full"])
            with open(os.path.join(base_dir, thumb_name), "wb") as f: f.write(im["thumb"])

            # Registrar upload
            up = Upload(
                role="room", subject_id=item.sub_ref, category="room_photo",
                path=f"uploads/contracts/{contract.ref}/{item.sub_ref}/{yyyymm}/{full_name}",
                mime="image/jpeg", size_bytes=len(im["full"]),
                width=im["w"], height=im["h"], sha256=hexname
            )
            db.session.add(up)

            # Actualizar galería de la room
            gallery = (room.images_json or {}).get("gallery", [])
            gallery.append({
                "url": f"/instance/uploads/contracts/{contract.ref}/{item.sub_ref}/{yyyymm}/{full_name}",
                "thumb": f"/instance/uploads/contracts/{contract.ref}/{item.sub_ref}/{yyyymm}/{thumb_name}",
                "w": im["w"], "h": im["h"], "sha": hexname, "sub_ref": item.sub_ref
            })
            room.images_json = {"gallery": gallery, "cover": gallery[0] if gallery else None}
            room.published = True
            added.append(full_name)
        except Exception as e:
            # continúa con las demás imágenes, y deja constancia en respuesta
            added.append(f"ERR:{getattr(e,'message',str(e))}")

    # estado línea -> published si subimos al menos 1 foto válida
    if any(not a.startswith("ERR:") for a in added) and item.status in ("draft", "ready"):
        item.status = "published"

    db.session.commit()
    return jsonify(ok=True,
                   contract={"ref": contract.ref},
                   item={"sub_ref": item.sub_ref, "status": item.status},
                   room={"code": room.code, "published": room.published, "images": room.images_json},
                   uploaded=added)

@bp_upload_rooms.post("/api/rooms/upload_sheet")
def upload_room_sheet():
    """
    Subida de FICHA (PDF/JSON). Reglas:
      - Requiere contrato firmado
      - Localiza ContractItem por sub_ref o ref+room_code
      - Acepta 1 o varias fichas ('file' o 'files[]')
      - Guarda ruta en images_json.sheet (última) y en images_json.sheets (histórico)
      - NO publica por sí sola (publica la foto)
    form-data:
      sub_ref?   | ref? + room_code?
      file       | files[]
    """
    sub  = (request.form.get("sub_ref") or "").strip().upper()
    ref  = (request.form.get("ref") or "").strip().upper()
    rcode= (request.form.get("room_code") or "").strip()
    franq= (request.headers.get("X-Franquiciado") or "").strip()

    files = []
    if "file" in request.files:
        files.append(request.files["file"])
    if "files[]" in request.files:
        files.extend(request.files.getlist("files[]"))
    for k, fs in request.files.items(multi=True):
        if k not in ("file", "files[]"):
            files.append(fs)

    if not files:
        return jsonify(ok=False, error="missing_files"), 400

    contract, item, room = get_contract_item(sub, ref, rcode)
    if not item or not contract or not room:
        return jsonify(ok=False, error="contract_item_not_found"), 404
    if contract.status != "signed":
        return jsonify(ok=False, error="contract_not_signed"), 403
    if contract.franchisee_id and franq and franq != contract.franchisee_id:
        return jsonify(ok=False, error="forbidden_franquiciado"), 403

    yyyymm = datetime.utcnow().strftime("%Y%m")
    base_dir = os.path.join(current_app.instance_path, "uploads", "contracts", contract.ref, item.sub_ref, "sheets", yyyymm)
    ensure_dir(base_dir)

    saved = []
    for fs in files:
        try:
            b = fs.read()
            if not b: 
                saved.append("ERR:empty"); 
                continue
            hexname = sha256_bytes(b)[:16]
            ext = ".bin"
            if fs.filename and "." in fs.filename:
                ext = "." + fs.filename.rsplit(".",1)[-1].lower()
                if len(ext) > 8: ext = ".bin"
            fname = f"sheet_{hexname}{ext}"
            fpath = os.path.join(base_dir, fname)
            with open(fpath, "wb") as f: f.write(b)

            rel = f"/instance/uploads/contracts/{contract.ref}/{item.sub_ref}/sheets/{yyyymm}/{fname}"

            # Registrar upload
            up = Upload(
                role="room", subject_id=item.sub_ref, category="room_sheet",
                path=f"uploads/contracts/{contract.ref}/{item.sub_ref}/sheets/{yyyymm}/{fname}",
                mime=fs.mimetype, size_bytes=len(b), sha256=hexname
            )
            db.session.add(up)

            # Guardar en images_json.sheet / images_json.sheets
            images = room.images_json or {}
            sheets = images.get("sheets", [])
            sheets.append({"url": rel, "sha": hexname, "ts": datetime.utcnow().isoformat()})
            images["sheets"] = sheets
            images["sheet"]  = sheets[-1]   # última como principal
            room.images_json = images

            saved.append(fname)
        except Exception as e:
            saved.append(f"ERR:{getattr(e,'message',str(e))}")

    db.session.commit()
    return jsonify(ok=True,
                   contract={"ref": contract.ref},
                   item={"sub_ref": item.sub_ref, "status": item.status},
                   room={"code": room.code, "published": room.published, "images": room.images_json},
                   sheets=saved)
