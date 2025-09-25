# routes_reservas.py
from __future__ import annotations
from datetime import datetime, date
from typing import Optional
from flask import Blueprint, request, jsonify
from extensions import db
from models_reservas import Reserva, overlaps

bp_reservas = Blueprint("reservas", __name__)

def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s: return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None

# --- Disponibilidad -----------------------------------------------------------
@bp_reservas.get("/api/reservas/availability")
def disponibilidad():
    try:
        room_id = int(request.args.get("room_id","0"))
    except Exception:
        room_id = 0
    start = _parse_date(request.args.get("start"))
    end   = _parse_date(request.args.get("end"))
    if not room_id or not start or not end:
        return jsonify(ok=False, error="missing_params"), 400
    if end < start:
        return jsonify(ok=False, error="bad_dates"), 400

    blocked = overlaps(room_id, start, end)
    return jsonify(ok=True, room_id=room_id, start=start.isoformat(), end=end.isoformat(), available=(not blocked))

# --- Crear reserva ------------------------------------------------------------
@bp_reservas.post("/api/reservas")
def crear_reserva():
    data = request.get_json(force=True) or {}
    try:
        room_id = int(data.get("room_id","0"))
    except Exception:
        room_id = 0
    nombre   = (data.get("nombre") or "").strip()
    email    = (data.get("email") or "").strip() or None
    telefono = (data.get("telefono") or "").strip() or None
    start    = _parse_date(data.get("start_date"))
    end      = _parse_date(data.get("end_date"))
    notas    = (data.get("notas") or None)
    meta     = data.get("meta_json") if isinstance(data.get("meta_json"), dict) else None

    if not room_id or not nombre or not start or not end:
        return jsonify(ok=False, error="missing_fields"), 400
    if end < start:
        return jsonify(ok=False, error="bad_dates"), 400

    if overlaps(room_id, start, end):
        return jsonify(ok=False, error="overlap"), 409

    r = Reserva(
        room_id=room_id, nombre=nombre, email=email, telefono=telefono,
        start_date=start, end_date=end, status="pending",
        notas=notas, meta_json=meta
    )
    db.session.add(r); db.session.commit()
    return jsonify(ok=True, id=r.id)

# --- Listar por estado --------------------------------------------------------
@bp_reservas.get("/api/reservas")
def listar_reservas():
    status = (request.args.get("status") or "pending").lower()
    q = Reserva.query
    if status in ("pending","approved","cancelled"):
        q = q.filter(Reserva.status == status)
    q = q.order_by(Reserva.id.desc()).limit(200)
    return jsonify([r.to_dict() for r in q.all()])

# --- Cambiar estado -----------------------------------------------------------
@bp_reservas.patch("/api/reservas/<int:reserva_id>")
def cambiar_estado(reserva_id:int):
    r = Reserva.query.get_or_404(reserva_id)
    data = request.get_json(force=True) or {}
    new_status = (data.get("status") or "").lower()
    if new_status not in ("approved","cancelled","pending"):
        return jsonify(ok=False, error="bad_status"), 400
    r.status = new_status
    db.session.commit()
    return jsonify(ok=True, id=r.id, status=r.status)
