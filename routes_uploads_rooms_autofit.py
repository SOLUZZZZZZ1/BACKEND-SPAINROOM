# routes_uploads_rooms_autofit.py
"""
Upload de fotos con AUTO-ADAPTACIÓN para la galería:
- Corrige orientación EXIF.
- Recorta centrado a relación 4:3 (ideal para hero/galería).
- Redimensiona a ancho objetivo (1600 px) y genera thumb (480 px), ambos 4:3.
- Opcional: clasifica como 'habitacion' (scope=room) o 'zonas comunes' (scope=common + common_type).
- Publica la habitación si hay al menos 1 foto de habitación.

Reemplaza al endpoint existente en /api/rooms/upload_photos.
Regístralo así en tu app Flask:

    from routes_uploads_rooms_autofit import bp_upload_rooms_autofit
    app.register_blueprint(bp_upload_rooms_autofit)

Asegúrate de NO registrar otro blueprint con el mismo path para evitar conflictos.
"""

import os, hashlib
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageOps
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename

from extensions import db
from models_contracts import Contract, ContractItem
from models_rooms import Room
from models_uploads import Upload

bp_upload_rooms_autofit = Blueprint("upload_rooms_autofit", __name__)

def ensure_dir(p: str): os.makedirs(p, exist_ok=True)
def sha256_bytes(b: bytes) -> str: h = hashlib.sha256(); h.update(b); return h.hexdigest()
def _yyyymm(): return datetime.utcnow().strftime("%Y%m")

def _open_image_rgb(fs):
    """Abre la imagen, corrige orientación EXIF y convierte a RGB."""
    im = Image.open(fs.stream)
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    elif im.mode == "RGBA":
        # fondo blanco para RGBA
        bg = Image.new("RGB", im.size, (255,255,255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    return im

def _center_crop_to_ratio(im: Image.Image, target_ratio: float) -> Image.Image:
    """Recorta centrado a la relación target_ratio (e.g., 4/3)."""
    w, h = im.size
    r = w / float(h)
    if abs(r - target_ratio) < 1e-3:
        return im.copy()
    if r > target_ratio:
        # demasiado ancha → recorta anchura
        new_w = int(h * target_ratio)
        x0 = (w - new_w) // 2
        return im.crop((x0, 0, x0 + new_w, h))
    else:
        # demasiado alta → recorta altura
        new_h = int(w / target_ratio)
        y0 = (h - new_h) // 2
        return im.crop((0, y0, w, y0 + new_h))

def process_image_autofit(fs, target_w=1600, thumb_w=480, ratio=4/3.0):
    """
    Devuelve dict con:
      { 'w':W, 'h':H, 'full_jpg':bytes, 'thumb_jpg':bytes }
    Imágenes a 4:3, full ~1600px ancho, thumb ~480px ancho.
    """
    im = _open_image_rgb(fs)
    # Recorte 4:3 centrado
    im43 = _center_crop_to_ratio(im, ratio)
    w, h = im43.size

    # Resize full
    if w > target_w:
        scale = target_w / float(w)
        im_full = im43.resize((target_w, int(h*scale)), Image.LANCZOS)
    else:
        im_full = im43.copy()

    # Resize thumb
    if w > thumb_w:
        scale_t = thumb_w / float(w)
        im_thumb = im43.resize((thumb_w, int(h*scale_t)), Image.LANCZOS)
    else:
        im_thumb = im43.copy()

    # Encode JPG
    bf = BytesIO(); im_full.save(bf, "JPEG", quality=88, optimize=True, progressive=True); full_jpg = bf.getvalue()
    bt = BytesIO(); im_thumb.save(bt, "JPEG", quality=82, optimize=True); thumb_jpg = bt.getvalue()

    return {
        "w": im_full.size[0], "h": im_full.size[1],
        "full_jpg": full_jpg, "thumb_jpg": thumb_jpg
    }

def _contract_item_by_ref(sub_ref: str, ref: str, room_code: str):
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

def _authorize_franquiciado(contract: Contract, item: ContractItem, franq_header: str):
    need = item.franchisee_id or contract.franchisee_id
    if not need: return True, None
    if not franq_header: return False, "missing_franquiciado"
    if franq_header != need: return False, "forbidden_franquiciado"
    return True, None

@bp_upload_rooms_autofit.post("/api/rooms/upload_photos")
def upload_room_photos_autofit():
    """
    Igual que tu upload, PERO adaptando imágenes a 4:3 automáticamente.
    form-data:
      sub_ref? | ref? + room_code?
      scope? = room|common (default room)
      common_type? = kitchen|bathroom|living|laundry|other (obligatorio si scope=common)
      file | files[] (1..N)
    headers:
      X-Franquiciado (si aplica)
    """
    sub   = (request.form.get("sub_ref") or "").strip().upper()
    ref   = (request.form.get("ref") or "").strip().upper()
    rcode = (request.form.get("room_code") or "").strip()
    franq = (request.headers.get("X-Franquiciado") or "").strip()

    scope = (request.form.get("scope") or "room").strip().lower()
    ctype = (request.form.get("common_type") or "").strip().lower()

    files = []
    if "file" in request.files: files.append(request.files["file"])
    if "files[]" in request.files: files.extend(request.files.getlist("files[]"))
    for k, fs in request.files.items(multi=True):
        if k not in ("file","files[]"): files.append(fs)

    if not files:
        return jsonify(ok=False, error="missing_files", message="Debes adjuntar una o más imágenes."), 400

    contract, item, room = _contract_item_by_ref(sub, ref, rcode)
    if not (contract and item and room):
        return jsonify(ok=False, error="contract_item_not_found", message="No se encontró el contrato/línea para esa habitación."), 404
    if contract.status != "signed":
        return jsonify(ok=False, error="contract_not_signed", message="El contrato no está firmado; no puedes subir fotos aún."), 403

    ok_auth, auth_err = _authorize_franquiciado(contract, item, franq)
    if not ok_auth:
        msg = "Falta franquiciado en cabecera." if auth_err == "missing_franquiciado" else "No autorizado para esta habitación."
        return jsonify(ok=False, error=auth_err, message=msg), 403

    if scope not in ("room","common"):
        return jsonify(ok=False, error="bad_scope", message="scope debe ser room|common"), 400
    if scope == "common" and ctype not in ("kitchen","bathroom","living","laundry","other"):
        return jsonify(ok=False, error="bad_common_type", message="common_type debe ser kitchen|bathroom|living|laundry|other"), 400

    yyyymm = _yyyymm()
    base_dir = os.path.join(current_app.instance_path, "uploads", "contracts", contract.ref, item.sub_ref, yyyymm)
    ensure_dir(base_dir)

    added = []
    for fs in files:
        try:
            ext = os.path.splitext(secure_filename(fs.filename or ""))[1].lower()
            if ext not in (".jpg",".jpeg",".png",".webp"):
                added.append("ERR:bad_image_type"); continue

            im = process_image_autofit(fs, target_w=1600, thumb_w=480, ratio=4/3.0)
            hexname = sha256_bytes(im["full_jpg"])[:16]
            full_name, thumb_name = f"{hexname}.jpg", f"{hexname}_t.jpg"

            with open(os.path.join(base_dir, full_name), "wb") as f: f.write(im["full_jpg"])
            with open(os.path.join(base_dir, thumb_name), "wb") as f: f.write(im["thumb_jpg"])

            # Registrar upload principal
            up = Upload(
                role="room", subject_id=item.sub_ref, category="room_photo" if scope=="room" else f"common_{ctype}",
                path=f"uploads/contracts/{contract.ref}/{item.sub_ref}/{yyyymm}/{full_name}",
                mime="image/jpeg", size_bytes=len(im["full_jpg"]),
                width=im["w"], height=im["h"], sha256=hexname
            )
            db.session.add(up)

            images = room.images_json or {}

            entry = {
                "url": f"/instance/uploads/contracts/{contract.ref}/{item.sub_ref}/{yyyymm}/{full_name}",
                "thumb": f"/instance/uploads/contracts/{contract.ref}/{item.sub_ref}/{yyyymm}/{thumb_name}",
                "w": im["w"], "h": im["h"], "sha": hexname, "sub_ref": item.sub_ref, "ratio": "4:3"
            }

            if scope == "room":
                gallery = images.get("gallery", []); gallery.append(entry); images["gallery"] = gallery
                if not images.get("cover") and gallery: images["cover"] = gallery[0]
            else:
                common = images.get("common", {})
                bucket = list(common.get(ctype, [])); bucket.append(entry)
                common[ctype] = bucket; images["common"] = common

            room.images_json = images
            added.append(full_name)
        except Exception as e:
            current_app.logger.exception("upload_room_photos_autofit error")
            added.append("ERR:invalid_image")

    # Publicación: solo si existe al menos 1 foto de HABITACIÓN
    if (room.images_json or {}).get("gallery"):
        room.published = True
        if item.status in ("draft","ready"): item.status = "published"

    db.session.commit()
    return jsonify(ok=True,
      contract={"ref": contract.ref},
      item={"sub_ref": item.sub_ref, "status": item.status},
      room={"code": room.code, "published": room.published, "images": room.images_json},
      uploaded=added,
      scope=scope
    )
