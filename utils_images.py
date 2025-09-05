import os, uuid
from PIL import Image, ImageOps

SIZES = {
    "thumb":  (320, 240),   # miniatura (4:3)
    "card":   (640, 480),   # tarjeta listado (4:3)
    "full":   (1600, 1200), # detalle (4:3)
    "square": (800, 800),   # cuadrada opcional
}

def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def _save(img: Image.Image, path: str, fmt="JPEG", quality=82):
    if fmt.upper() in ("JPG", "JPEG"):
        img.save(path, format="JPEG", quality=quality, optimize=True, progressive=True)
    elif fmt.upper() == "WEBP":
        img.save(path, format="WEBP", quality=quality, method=6)
    else:
        img.save(path)

def _center_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_w, src_h = img.size
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h
    if src_ratio > target_ratio:
        new_w = int(src_h * target_ratio)
        x1 = (src_w - new_w) // 2
        box = (x1, 0, x1 + new_w, src_h)
    else:
        new_h = int(src_w / target_ratio)
        y1 = (src_h - new_h) // 2
        box = (0, y1, src_w, y1 + new_h)
    return img.crop(box).resize((target_w, target_h), Image.LANCZOS)

def process_photo(file_storage, room_id: int, upload_root="uploads"):
    base_id = uuid.uuid4().hex
    out_dir = os.path.join(upload_root, "rooms", str(room_id))
    _ensure_dir(out_dir)

    img = Image.open(file_storage.stream)
    img = ImageOps.exif_transpose(img).convert("RGB")

    outputs = {}
    for key, (w, h) in SIZES.items():
        cropped = _center_crop(img, w, h)
        jpg_path = os.path.join(out_dir, f"{base_id}_{key}.jpg")
        webp_path = os.path.join(out_dir, f"{base_id}_{key}.webp")
        _save(cropped, jpg_path, "JPEG", 82)
        _save(cropped, webp_path, "WEBP", 80)
        outputs[key] = {
            "jpg": f"/{jpg_path.replace(os.sep,'/')}",
            "webp": f"/{webp_path.replace(os.sep,'/')}",
            "w": w, "h": h
        }
    return {"id": base_id, "variants": outputs}
