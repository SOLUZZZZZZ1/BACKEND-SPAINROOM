# services_images.py
import io, os, hashlib
from PIL import Image

def _sha256_bytes(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()

def ensure_dir(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)

def process_to_webp(in_fp, max_w=1600, quality=86):
    im = Image.open(in_fp).convert("RGB")
    w, h = im.size
    if w > max_w:
        nh = int(h * (max_w / float(w)))
        im = im.resize((max_w, nh), Image.LANCZOS)
        w, h = im.size
    buf = io.BytesIO()
    im.save(buf, format="WEBP", quality=quality, method=6)
    data = buf.getvalue()
    return data, w, h

def process_thumb_webp(in_fp, thumb_w=400, quality=80):
    im = Image.open(in_fp).convert("RGB")
    w, h = im.size
