# opportunities.py
# Blueprint de oportunidades/colaboraciones (franquiciados, propietarios, colaboradores)
from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional, List

from flask import Blueprint, request, jsonify, current_app, abort

bp_opps = Blueprint("opportunities", __name__, url_prefix="/api/opportunities")


@dataclass
class Lead:
    created_at: float
    tipo: str             # 'franquiciado' | 'propietario' | 'colaborador'
    nombre: str
    email: str
    telefono: Optional[str]
    ciudad: Optional[str]
    mensaje: Optional[str]
    meta: Dict[str, Any]


def _storage_csv_path() -> str:
    base = current_app.instance_path  # p.ej. backend/instance
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "opportunities.csv")


def _append_csv(lead: Lead) -> None:
    path = _storage_csv_path()
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["created_at", "tipo", "nombre", "email", "telefono", "ciudad", "mensaje", "meta"],
        )
        if not exists:
            writer.writeheader()
        row = asdict(lead)
        # Guarda meta como string (json-like)
        row["meta"] = str(row["meta"])
        writer.writerow(row)


def _validate_payload(payload: Dict[str, Any]) -> Lead:
    tipo = str(payload.get("tipo", "")).strip().lower()
    if tipo not in {"franquiciado", "propietario", "colaborador"}:
        abort(400, description="tipo debe ser franquiciado | propietario | colaborador")

    nombre = str(payload.get("nombre", "")).strip()
    email = str(payload.get("email", "")).strip()
    telefono = str(payload.get("telefono", "")).strip() or None
    ciudad = str(payload.get("ciudad", "")).strip() or None
    mensaje = str(payload.get("mensaje", "")).strip() or None

    if not nombre or not email:
        abort(400, description="nombre y email son obligatorios")

    # Campos adicionales libres
    meta = payload.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {"_raw_meta": str(meta)}

    return Lead(
        created_at=time.time(),
        tipo=tipo,
        nombre=nombre,
        email=email,
        telefono=telefono,
        ciudad=ciudad,
        mensaje=mensaje,
        meta=meta,
    )


@bp_opps.get("/ping")
def ping():
    return jsonify({"ok": True, "opportunities": "alive"})


@bp_opps.post("/leads")
def create_lead():
    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    lead = _validate_payload(payload)
    _append_csv(lead)

    # (Opcional) enviar webhook a Slack/Discord si defines env
    webhook = os.getenv("OPPORTUNITIES_WEBHOOK_URL")
    if webhook:
        try:
            import requests  # solo cuando se usa
            requests.post(
                webhook,
                json={
                    "text": f"Nuevo lead ({lead.tipo}) — {lead.nombre} <{lead.email}> — {lead.telefono or '-'} — {lead.ciudad or '-'}"
                },
                timeout=5,
            )
        except Exception:
            # No rompemos el flujo si el webhook falla
            pass

    return jsonify({"ok": True, "stored": True})


@bp_opps.get("/admin/leads")
def list_leads_admin():
    # Protección mínima por cabecera
    role = (request.headers.get("X-User-Role") or "").lower()
    if role not in {"spainroom", "admin"}:
        abort(403, description="forbidden")

    path = _storage_csv_path()
    if not os.path.exists(path):
        return jsonify({"ok": True, "items": [], "count": 0})

    items: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            items.append(row)

    return jsonify({"ok": True, "items": items, "count": len(items)})
