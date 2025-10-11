# routes_catastro.py — Endpoints Catastro (mock/resolución básica)
# Nora · 2025-10-11
from flask import Blueprint, request, jsonify, Response

bp_cat = Blueprint("catastro", __name__)

def _corsify(resp: Response) -> Response:
    origin = request.headers.get("Origin", "*")
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Key"
    return resp

@bp_cat.route("/api/catastro/resolve_direccion", methods=["POST","OPTIONS"])
def resolve_direccion():
    if request.method == "OPTIONS":
        return _corsify(Response(status=204))
    data = request.get_json(silent=True) or {}
    direccion = (data.get("direccion") or "").strip()
    municipio = (data.get("municipio") or "").strip()
    provincia = (data.get("provincia") or "").strip()
    cp = (data.get("cp") or "").strip()

    # DEMO: si hay dirección razonable, producimos una refcat falsa de 20 chars
    ok = bool(direccion and municipio and provincia)
    refcat = "A" * 20 if ok else None
    return _corsify(jsonify(ok=True, refcat=refcat))

@bp_cat.route("/api/catastro/consulta_refcat", methods=["POST","OPTIONS"])
def consulta_refcat():
    if request.method == "OPTIONS":
        return _corsify(Response(status=204))
    data = request.get_json(silent=True) or {}
    refcat = (data.get("refcat") or "").strip()
    if len(refcat) != 20:
        return _corsify(jsonify(ok=False, error="refcat debe tener 20 caracteres")), 400
    # DEMO: datos estáticos
    return _corsify(jsonify(ok=True, uso="Residencial", superficie_m2=78, antiguedad="2004"))
