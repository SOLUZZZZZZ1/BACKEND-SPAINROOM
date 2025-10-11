# routes_cedula.py — /api/legal/* (requisito por provincia + check cédula con control de fuente)
# Nora · 2025-10-11
from datetime import date
from flask import Blueprint, request, jsonify, Response

bp_legal = Blueprint("bp_legal", __name__)

def _corsify(resp: Response) -> Response:
    origin = request.headers.get("Origin", "*")
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Key, X-Catastro-Mode"
    return resp

OBLIG = {
    "Barcelona":    {"cat":"si", "doc":"Cédula d'habitabilitat", "org":"Agència de l'Habitatge de Catalunya", "vig":"15 años", "notas":"Obligatoria para alquilar o vender.", "link":"https://habitatge.gencat.cat/"},
    "Girona":       {"cat":"si", "doc":"Cédula d'habitabilitat", "org":"Agència de l'Habitatge de Catalunya", "vig":"15 años", "notas":"—", "link":"https://habitatge.gencat.cat/"},
    "Lleida":       {"cat":"si", "doc":"Cédula d'habitabilitat", "org":"Agència de l'Habitatge de Catalunya", "vig":"15 años", "notas":"—", "link":"https://habitatge.gencat.cat/"},
    "Tarragona":    {"cat":"si", "doc":"Cédula d'habitabilitat", "org":"Agència de l'Habitatge de Catalunya", "vig":"15 años", "notas":"—", "link":"https://habitatge.gencat.cat/"},
    "Valencia":     {"cat":"si", "doc":"Licencia de ocupación / cédula", "org":"GVA", "vig":"10 años", "notas":"—", "link":"https://www.gva.es/"},
    "Alicante":     {"cat":"si", "doc":"Licencia de ocupación / cédula", "org":"GVA", "vig":"10años", "notas":"—", "link":"https://www.gva.es/"},
    "Castellón":    {"cat":"si", "doc":"Licencia de ocupación / cédula", "org":"GVA", "vig":"10 años", "notas":"—", "link":"https://www.gva.es/"},
    "Islas Baleares":{"cat":"si","doc":"Cèdula d'habitabilitat","org":"Consells Insulars","vig":"10 años","notas":"—","link":"https://www.caib.es/"},
    "Mallorca":     {"cat":"si", "doc":"Cèdula d'habitabilitat", "org":"Consell de Mallorca", "vig":"10 años", "notas":"—", "link":"https://www.conselldemallorca.cat/"},
    "Menorca":      {"cat":"si", "doc":"Cèdula d'habitabilitat", "org":"Consell de Menorca", "vig":"10 años", "notas":"—", "link":"https://www.cime.es/"},
    "Ibiza":        {"cat":"si", "doc":"Cèdula d'habitabilitat", "org":"Consell d'Eivissa", "vig":"10 años", "notas":"—", "link":"https://www.conselldeivissa.es/"},
    "Madrid":       {"cat":"no", "doc":"Licencia de primera ocupación / declaración responsable", "org":"Ayuntamiento", "vig":"—", "notas":"No se exige cédula autonómica.", "link":"https://www.madrid.es/"},
}

@bp_legal.route("/api/legal/requirement", methods=["POST","OPTIONS"])
def requirement():
    if request.method == "OPTIONS":
        return _corsify(Response(status=204))
    data = request.get_json(silent=True) or {}
    provincia = (data.get("provincia") or "").strip()
    info = OBLIG.get(provincia, {"cat":"depende","doc":"Licencia / cédula", "org":"Ayuntamiento/CCAA", "vig":"—", "notas":"Según municipio y tipo de vivienda", "link":None})
    return _corsify(jsonify(ok=True, requirement=info))

@bp_legal.route("/api/legal/cedula/check", methods=["POST","OPTIONS"])
def cedula_check():
    if request.method == "OPTIONS":
        return _corsify(Response(status=204))
    data = request.get_json(silent=True) or {}

    refcat = (data.get("refcat") or "").strip()
    num = (data.get("cedula_numero") or "").strip()
    catastro_mode = (request.headers.get("X-Catastro-Mode") or "").strip().lower()

    vigente = False
    expires_at = None

    # Reglas: vigente si nº >= 6; o si refcat=20 y Catastro fue SOAP (no demo)
    if num and len(num) >= 6:
        vigente = True
    elif len(refcat) == 20 and catastro_mode == "soap":
        vigente = True

    if vigente:
        expires_at = date.today().replace(year=date.today().year + 2).isoformat()

    result = {
        "ok": True,
        "has_doc": vigente,
        "status": "vigente" if vigente else "no_consta",
        "data": {
            "refcat": refcat or None,
            "cedula_numero": num or None,
            "expires_at": expires_at
        }
    }
    return _corsify(jsonify(result))
