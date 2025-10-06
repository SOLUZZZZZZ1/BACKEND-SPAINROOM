# rooms_proxy.py — proxy de /api/rooms/* y /instance/* hacia el backend de habitaciones
import os
import requests
from flask import Blueprint, request, Response

bp_rooms = Blueprint("rooms_proxy", __name__)

# Backend de habitaciones (upstream)
ROOMS_UPSTREAM = os.getenv("ROOMS_UPSTREAM", "https://backend-spainroom.onrender.com").rstrip("/")

def _forward(path: str):
    """Reenvía la petición actual al upstream preservando método, params y cuerpo."""
    url = f"{ROOMS_UPSTREAM}{path}"
    # Filtra cabeceras que no tienen sentido reenviar
    fwd_headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in ("host", "content-length", "connection")}
    try:
        r = requests.request(
            method=request.method,
            url=url,
            params=request.args,
            data=request.get_data(),
            headers=fwd_headers,
            stream=True,
            timeout=30,
        )
        resp = Response(r.content, status=r.status_code)
        # Copia cabeceras “útiles”
        for k, v in r.headers.items():
            lk = k.lower()
            if lk in ("content-length", "transfer-encoding", "content-encoding", "connection"):
                continue
            resp.headers[k] = v
        return resp
    except requests.RequestException:
        return Response("Upstream error", status=502)

# === Endpoints de habitaciones ===
@bp_rooms.route("/api/rooms/published", methods=["GET"])
def rooms_published():
    return _forward("/api/rooms/published")

@bp_rooms.route("/api/rooms/search", methods=["GET"])
def rooms_search():
    return _forward("/api/rooms/search")

@bp_rooms.route("/api/rooms/<room_id>", methods=["GET"])
def rooms_get(room_id):
    return _forward(f"/api/rooms/{room_id}")

# === Archivos/imagenes del instance del backend de habitaciones ===
@bp_rooms.route("/instance/<path:subpath>", methods=["GET"])
def instance_files(subpath):
    return _forward(f"/instance/{subpath}")
