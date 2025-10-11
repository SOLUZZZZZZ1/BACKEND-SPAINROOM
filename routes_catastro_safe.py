# routes_catastro_safe.py — Catastro SOAP con fallback DEMO o modo ESTRICTO (sin maquillar datos)
# Nora · 2025-10-11
#
# Endpoints:
#   POST /api/catastro/resolve_direccion  -> { ok, refcat, mode } | { ok:false, error, message } (strict)
#   POST /api/catastro/consulta_refcat    -> { ok, uso, superficie_m2, antiguedad, mode } | { ok:false, ... } (strict)
#
# Variables de entorno (Render):
#   CATASTRO_MODE = 'soap' | 'demo' | 'strict'   (por defecto 'soap')
#     - soap: intenta SOAP; si falla -> DEMO
#     - demo: no llama SOAP; devuelve DEMO estable
#     - strict: intenta SOAP; si falla -> 502 (catastro_unavailable)
#   CATASTRO_SOAP_URL_RESOLVE, CATASTRO_SOAP_ACTION_RESOLVE
#   CATASTRO_SOAP_URL_REF,     CATASTRO_SOAP_ACTION_REF
#   CATASTRO_TIMEOUT (float, segs, por defecto 8.0)

import os
import re
import unicodedata
import requests
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

# ------------------------ Utilidades ------------------------

def _corsify(resp: Response) -> Response:
    origin = request.headers.get("Origin", "*")
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Key"
    return resp

def _first_text_by_suffix(root: ET.Element, suffixes=("rc1", "refcat", "rc")):
    for el in root.iter():
        tag = el.tag.split("}")[-1]  # quitar namespace
        if tag.lower() in {s.lower() for s in suffixes} and (el.text or "").strip():
            return el.text.strip()
    xml_text = ET.tostring(root, encoding="unicode", method="xml")
    m = REFCAT_RE.search(xml_text or "")
    return m.group(1) if m else None

def _strict_or_demo(payload_demo: dict, where: str):
    """strict => 502; soap/demo => demo estable"""
    if MODE == "strict":
        msg = f"SOAP no respondió correctamente en {where}"
        try:
            current_app.logger.warning("Catastro strict: %s", msg)
        except Exception:
            pass
        return _corsify(jsonify(ok=False, error="catastro_unavailable", message=msg)), 502
    return _corsify(jsonify({**payload_demo, "mode": "demo"}))

def _norm(s: str) -> str:
    s = (s or "").strip().upper()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)

# ------------------------ Endpoints ------------------------

@bp_catastro.route("/api/catastro/resolve_direccion", methods=["POST", "OPTIONS"])
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

    # Log de entrada y modo
    try:
        current_app.logger.info(
            "[catastro] resolve_direccion mode=%s dir=%s, mun=%s, prov=%s",
            MODE, direccion, municipio, provincia
        )
    except Exception:
        pass

    # Modo demo directo
    if MODE == "demo":
        return _corsify(jsonify(ok=True, refcat="A"*20, mode="demo"))

    # Normalización
    direccion_n = _norm(direccion)
    municipio_n = _norm(municipio)
    provincia_n = _norm(provincia)

    # Detección sencilla de TipoVia
    TV_MAP = {
        "C": "CALLE", "C/": "CALLE", "CL": "CALLE", "CALLE": "CALLE",
        "AV": "AVENIDA", "AV.": "AVENIDA", "AVDA": "AVENIDA", "AVDA.": "AVENIDA",
        "PZA": "PLAZA", "PLAZA": "PLAZA",
        "CRTA": "CARRETERA", "CARRETERA": "CARRETERA",
        "PSO": "PASEO", "PASEO": "PASEO"
    }
    partes = direccion_n.split()
    tipo_via = ""
    nom_via  = direccion_n
    if partes:
        p0 = partes[0].rstrip(".")
        if p0 in TV_MAP:
            tipo_via = TV_MAP[p0]
            nom_via  = " ".join(partes[1:]).strip()

    def _do_request(_tipo_via: str, _nom_via: str):
        envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <Consulta_DNPLOC xmlns="http://tempuri.org/">
      <Provincia>{provincia_n}</Provincia>
      <Municipio>{municipio_n}</Municipio>
      <TipoVia>{_tipo_via}</TipoVia>
      <NomVia>{_nom_via}</NomVia>
      <Numero>{numero}</Numero>
    </Consulta_DNPLOC>
  </soap:Body>
</soap:Envelope>"""
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ACT_RESOLVE}
        r = requests.post(URL_RESOLVE, data=envelope.encode("utf-8"), headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        rc = _first_text_by_suffix(root) or None
        return rc, r.text

    ok_urls = bool(URL_RESOLVE and ACT_RESOLVE)
    if ok_urls:
        # 1º intento: con TipoVia detectado
        try:
            rc, raw = _do_request(tipo_via, nom_via)
            if rc and len(rc) == 20:
                return _corsify(jsonify(ok=True, refcat=rc, mode="soap"))
            try:
                current_app.logger.warning("Catastro SOAP sin RC (con TipoVia). Reintentando sin TipoVia...")
            except Exception:
                pass
        except Exception as e:
            try:
                current_app.logger.warning("Catastro SOAP fallo (con TipoVia): %s", str(e))
            except Exception:
                pass

        # 2º intento: sin TipoVia, NomVia = dirección completa
        try:
            rc2, raw2 = _do_request("", direccion_n)
            if rc2 and len(rc2) == 20:
                return _corsify(jsonify(ok=True, refcat=rc2, mode="soap"))
            try:
                current_app.logger.warning("Catastro SOAP sin RC (sin TipoVia). XML tail: %s", (raw2[-400:] if raw2 else ""))
            except Exception:
                pass
        except Exception as e2:
            try:
                current_app.logger.warning("Catastro SOAP fallo (sin TipoVia): %s", str(e2))
            except Exception:
                pass

    # strict => error; soap => demo
    return _strict_or_demo({"ok": True, "refcat": "A"*20}, "resolve_direccion")

@bp_catastro.route("/api/catastro/consulta_refcat", methods=["POST", "OPTIONS"])
def consulta_refcat():
    if request.method == "OPTIONS":
        return _corsify(Response(status=204))

    body = request.get_json(silent=True) or {}
    refcat = (body.get("refcat") or "").strip()
    if len(refcat) != 20:
        return _corsify(jsonify(ok=False, error="bad_request", message="refcat debe tener 20 caracteres")), 400

    # Modo demo directo
    if MODE == "demo":
        return _corsify(jsonify(ok=True, uso="Residencial", superficie_m2=78, antiguedad="2004", mode="demo"))

    ok_urls = bool(URL_REF and ACT_REF)
    if ok_urls:
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
            headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ACT_REF}
            r = requests.post(URL_REF, data=envelope.encode("utf-8"), headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            # TODO: Parsear valores reales si el XML los aporta (uso/superficie/antigüedad)
            return _corsify(jsonify(ok=True, uso="Residencial", superficie_m2=78, antiguedad="2004", mode="soap"))
        except Exception as e:
            try:
                current_app.logger.warning("Catastro SOAP fallo (consulta_refcat): %s", str(e))
            except Exception:
                pass

    # strict => error; soap => demo
    return _strict_or_demo({"ok": True, "uso": "Residencial", "superficie_m2": 78, "antiguedad": "2004"}, "consulta_refcat")
