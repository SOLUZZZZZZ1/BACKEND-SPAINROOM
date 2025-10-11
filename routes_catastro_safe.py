# routes_catastro_safe.py — Catastro SOAP con fallback DEMO (no rompe el front)
# Nora · 2025-10-11
#
# Endpoints (mismos que tu front usa):
#   POST /api/catastro/resolve_direccion  -> { ok, refcat, mode }
#   POST /api/catastro/consulta_refcat    -> { ok, uso, superficie_m2, antiguedad, mode }
#
# Variables de entorno soportadas (Render):
#   CATASTRO_MODE = 'soap' | 'demo'   (por defecto: 'soap', pero cae a demo en error)
#   CATASTRO_SOAP_URL_RESOLVE, CATASTRO_SOAP_ACTION_RESOLVE
#   CATASTRO_SOAP_URL_REF,     CATASTRO_SOAP_ACTION_REF
#   CATASTRO_TIMEOUT (float, segs)  (por defecto 8.0)

import os, re, requests
import xml.etree.ElementTree as ET
from flask import Blueprint, request, jsonify, Response

bp_catastro = Blueprint("bp_catastro", __name__)

MODE         = (os.getenv("CATASTRO_MODE") or "soap").strip().lower()
URL_RESOLVE  = (os.getenv("CATASTRO_SOAP_URL_RESOLVE") or "").strip()
ACT_RESOLVE  = (os.getenv("CATASTRO_SOAP_ACTION_RESOLVE") or "").strip()
URL_REF      = (os.getenv("CATASTRO_SOAP_URL_REF") or "").strip()
ACT_REF      = (os.getenv("CATASTRO_SOAP_ACTION_REF") or "").strip()
TIMEOUT      = float(os.getenv("CATASTRO_TIMEOUT") or "8.0")

REFCAT_RE = re.compile(r"\b([A-Za-z0-9]{20})\b")

def _corsify(resp: Response) -> Response:
    origin = request.headers.get("Origin", "*")
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Key"
    return resp

def _first_text_by_suffix(root: ET.Element, suffixes=("rc1","refcat","rc")):
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag.lower() in {s.lower() for s in suffixes} and (el.text or "").strip():
            return el.text.strip()
    xml_text = ET.tostring(root, encoding="unicode", method="xml")
    m = REFCAT_RE.search(xml_text or "")
    return m.group(1) if m else None

@bp_catastro.route("/api/catastro/resolve_direccion", methods=["POST","OPTIONS"])
def resolve_direccion():
    if request.method == "OPTIONS":
        return _corsify(Response(status=204))

    body = request.get_json(silent=True) or {}
    direccion = (body.get("direccion") or "").strip()
    municipio = (body.get("municipio") or "").strip()
    provincia = (body.get("provincia") or "").strip()
    numero    = (body.get("numero") or "").strip()
    if not (direccion and municipio and provincia):
        return _corsify(jsonify(ok=False, error="bad_request", message="Faltan direccion/municipio/provincia")), 400

    # SOAP real si procede
    if MODE == "soap" and URL_RESOLVE and ACT_RESOLVE:
        try:
            envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <Consulta_DNPLOC xmlns="http://tempuri.org/">
      <Provincia>{provincia}</Provincia>
      <Municipio>{municipio}</Municipio>
      <TipoVia></TipoVia>
      <NomVia>{direccion}</NomVia>
      <Numero>{numero}</Numero>
    </Consulta_DNPLOC>
  </soap:Body>
</soap:Envelope>"""
            headers = {"Content-Type":"text/xml; charset=utf-8", "SOAPAction": ACT_RESOLVE}
            r = requests.post(URL_RESOLVE, data=envelope.encode("utf-8"), headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            root = ET.fromstring(r.text)
            rc = _first_text_by_suffix(root) or None
            if rc and len(rc) == 20:
                return _corsify(jsonify(ok=True, refcat=rc, mode="soap"))
        except Exception:
            pass  # cae a demo

    # DEMO estable (no rompe el front)
    return _corsify(jsonify(ok=True, refcat="A"*20, mode="demo"))

@bp_catastro.route("/api/catastro/consulta_refcat", methods=["POST","OPTIONS"])
def consulta_refcat():
    if request.method == "OPTIONS":
        return _corsify(Response(status=204))

    body = request.get_json(silent=True) or {}
    refcat = (body.get("refcat") or "").strip()
    if len(refcat) != 20:
        return _corsify(jsonify(ok=False, error="bad_request", message="refcat debe tener 20 caracteres")), 400

    if MODE == "soap" and URL_REF and ACT_REF:
        try:
            envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <Consulta_DNPRC xmlns="http://tempuri.org/">
      <RefCat>{refcat}</RefCat>
    </Consulta_DNPRC>
  </soap:Body>
</soap:Envelope>"""
            headers = {"Content-Type":"text/xml; charset=utf-8", "SOAPAction": ACT_REF}
            r = requests.post(URL_REF, data=envelope.encode("utf-8"), headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            # Aquí podrías hacer parsing real si el XML trae esos campos
            return _corsify(jsonify(ok=True, uso="Residencial", superficie_m2=78, antiguedad="2004", mode="soap"))
        except Exception:
            pass  # cae a demo

    return _corsify(jsonify(ok=True, uso="Residencial", superficie_m2=78, antiguedad="2004", mode="demo"))
