# routes_uploads_rooms.py
import os, hashlib
from datetime import datetime
from io import BytesIO
from PIL import Image
from flask import Blueprint, request, jsonify, current_app
from app import db
from models_contracts import Contract, ContractItem
from models_rooms import Room
from models_uploads import Upload

bp_upload_rooms = Blueprint("upload_rooms", __name__)

def ensure_dir(p): os.makedirs(p, exist_ok=True)
def sha256_bytes(b: bytes) -> str: h=hashlib.sha256(); h.update(b); return h.hexdigest()

def process_image(file_storage, max_w=1600, thumb_w=480):
    im = Image.open(file_storage.stream).convert("RGB")
    w, h = im.size
    im_full = im.resize((max_w, int(h*(max_w/w))), Image.LANCZOS) if w>max_w else im.copy()
    im_thumb= im.resize((thumb_w,int(h*(thumb_w/w))), Image.LANCZOS) if w>thumb_w else im.copy()
    bf = BytesIO(); im_full.save(bf,"JPEG",quality=88,optimize=True); full = bf.getvalue()
    bt = BytesIO(); im_thumb.save(bt,"JPEG",quality=82,optimize=True); thumb= bt.getvalue()
    return {"w": im_full.size[0], "h": im_full.size[1], "full": full, "thumb": thumb}

@bp_upload_rooms.post("/api/rooms/upload_photos")
def upload_room_photos():
    """
    form-data:
      file      : imagen
      sub_ref   : SR-12345-01 (preferido), o
      ref       : SR-12345  + room_code: ROOM-022
    headers:
      X-Franquiciado: id franquiciado (opcional validar)
    Reglas:
      - contrato debe estar status='signed'
      - debe existir ContractItem (sub_ref o {ref+room})
    """
    fs   = request.files.get("file")
    sub  = (request.form.get("sub_ref") or "").strip().upper()
    ref  = (request.form.get("ref") or "").strip().upper()
    rcode= (request.form.get("room_code") or "").strip()
    franq= (request.headers.get("X-Franquiciado") or "").strip()
    if not fs: return jsonify(ok=False, error="missing_file"), 400

    item = None; contract = None
    if sub:
        item = ContractItem.query.filter_by(sub_ref=sub).first()
        contract = item.contract if item else None
    else:
        if not (ref and rcode): return jsonify(ok=False, error="missing_ref_or_room"), 400
        contract = Contract.query.filter_by(ref=ref).first()
        if not contract: return jsonify(ok=False, error="contract_not_found"), 404
        room = Room.query.filter_by(code=rcode).first()
        if not room: return jsonify(ok=False, error="room_not_found"), 404
        item = ContractItem.query.filter_by(contract_id=contract.id, room_id=room.id).first()
    if not item or not contract: return jsonify(ok=False, error="contract_item_not_found"), 404
    if contract.status != "signed": return jsonify(ok=False, error="contract_not_signed"), 403
    if contract.franchisee_id and franq and franq != contract.franchisee_id:
        return jsonify(ok=False, error="forbidden_franquiciado"), 403

    im = process_image(fs)
    yyyymm = datetime.utcnow().strftime("%Y%m")
    base_dir = os.path.join(current_app.instance_path, "uploads", "contracts", contract.ref, item.sub_ref, yyyymm)
    ensure_dir(base_dir)
    hexname = sha256_bytes(im["full"])[:16]
    full_name, thumb_name = f"{hexname}.jpg", f"{hexname}_t.jpg"
    with open(os.path.join(base_dir, full_name), "wb") as f: f.write(im["full"])
    with open(os.path.join(base_dir, thumb_name), "wb") as f: f.write(im["thumb"])

    # Registro de upload
    up = Upload(role="room", subject_id=item.sub_ref, category="room_photo",
                path=f"uploads/contracts/{contract.ref}/{item.sub_ref}/{yyyymm}/{full_name}",
                mime="image/jpeg", size_bytes=len(im["full"]), width=im["w"], height=im["h"], sha256=hexname)
    db.session.add(up)

    # Publicar/actualizar galería de la Room
    room = db.session.get(Room, item.room_id)
    gallery = (room.images_json or {}).get("gallery", [])
    gallery.append({
        "url": f"/instance/uploads/contracts/{contract.ref}/{item.sub_ref}/{yyyymm}/{full_name}",
        "thumb": f"/instance/uploads/contracts/{contract.ref}/{item.sub_ref}/{yyyymm}/{thumb_name}",
        "w": im["w"], "h": im["h"], "sha": hexname, "sub_ref": item.sub_ref
    })
    room.images_json = {"gallery": gallery, "cover": gallery[0] if gallery else None}
    room.published = True

    if item.status in ("draft","ready"): item.status = "published"
    db.session.commit()
    return jsonify(ok=True, contract={"ref":contract.ref},
                   item={"sub_ref": item.sub_ref, "status": item.status},
                   room={"code": room.code, "published": room.published, "images": room.images_json})
