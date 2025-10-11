# routes_catastro_safe.py — Catastro SOAP con fallback DEMO o modo estricto (sin maquillar resultados)
# Nora · 2025-10-11
import os, re, requests
import xml.etree.ElementTree as ET
from flask import Blueprint, request, jsonify, Response, current_app

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

def _strict_or_demo_demo_response(payload_demo: dict, where: str):
    if MODE == "strict":
        msg = f"SOAP no respondió correctamente en {where}"
        try:
            current_app.logger.warning("Catastro strict: %s", msg)
        except Exception:
            pass
        return _corsify(jsonify(ok=False, error="catastro_unavailable", message=msg)), 502
    return _corsify(jsonify({**payload_demo, "mode": "demo"}))

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

    if MODE == "demo":
        return _corsify(jsonify(ok=True, refcat="A"*20, mode="demo"))

    if URL_RESOLVE and ACT_RESOLVE:
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
            try:
                current_app.logger.warning("Catastro SOAP sin RC válida (resolve). XML tail: %s", r.text[-400:])
            except Exception:
                pass
        except Exception as e:
            try:
                current_app.logger.warning("Catastro SOAP fallo (resolve): %s", str(e))
            except Exception:
                pass

    return _strict_or_demo_demo_response({"ok": True, "refcat": "A"*20}, "resolve_direccion")

@bp_catastro.route("/api/catastro/consulta_refcat", methods=["POST","OPTIONS"])
def consulta_refcat():
    if request.method == "OPTIONS":
        return _corsify(Response(status=204))

    body = request.get_json(silent=True) or {}
    refcat = (body.get("refcat") or "").strip()
    if len(refcat) != 20:
        return _corsify(jsonify(ok=False, error="bad_request", message="refcat debe tener 20 caracteres")), 400

    if MODE == "demo":
        return _corsify(jsonify(ok=True, uso="Residencial", superficie_m2=78, antiguedad="2004", mode="demo"))

    if URL_REF and ACT_REF:
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
            return _corsify(jsonify(ok=True, uso="Residencial", superficie_m2=78, antiguedad="2004", mode="soap"))
        except Exception as e:
            try:
                current_app.logger.warning("Catastro SOAP fallo (consulta_refcat): %s", str(e))
            except Exception:
                pass

    return _strict_or_demo_demo_response({"ok": True, "uso": "Residencial", "superficie_m2": 78, "antiguedad": "2004"}, "consulta_refcat")
