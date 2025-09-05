import os
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
from models import db, Room
from utils_images import process_photo

photos_bp = Blueprint("photos", __name__, url_prefix="/api")

ALLOWED = {"jpg","jpeg","png","webp"}
MAX_MB = 12

@photos_bp.post("/rooms/<int:room_id>/photos")
def upload_room_photo(room_id):
    Room.query.get_or_404(room_id)
    if "file" not in request.files:
        return jsonify({"error":"Falta archivo 'file'"}), 400
    f = request.files["file"]
    ext = (secure_filename(f.filename).rsplit(".",1)[-1] or "").lower()
    if ext not in ALLOWED:
        return jsonify({"error":"Formato no permitido"}), 400
    f.seek(0, os.SEEK_END); size = f.tell(); f.seek(0)
    if size > MAX_MB * 1024*1024:
        return jsonify({"error":f"Máximo {MAX_MB}MB"}), 413

    out = process_photo(f, room_id, upload_root=current_app.config.get("UPLOAD_ROOT","uploads"))
    return jsonify(out), 201
