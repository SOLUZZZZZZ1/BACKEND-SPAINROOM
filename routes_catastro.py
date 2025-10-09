# routes_catastro.py
# Integración Catastro (SOAP) usando variables de entorno de Render
# Endpoints:
#  - POST /api/catastro/resolve_direccion  -> { ok, refcat }  (desde dirección)
#  - POST /api/catastro/consulta_refcat    -> { ok, uso, superficie_m2, antiguedad } (si el SOAP lo devuelve)
#
# Variables de entorno esperadas (Render → Environment):
#  CATASTRO_RESOLVE_URL           (p.ej. https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx)
#  CATASTRO_RESOLVE_SOAP_ACTION   (p.ej. http://tempuri.org/Consulta_DNPRC)
#  CATASTRO_CONSULTA_URL          (p.ej. https://ovc.catastro.meh.es/ovcservweb/OVCSWRC/OVCBusquedaRC.asmx)
#  CATASTRO_CONSULTA_SOAP_ACTION  (acción SOAP de consulta por RC, según método que uses)
#  CATASTRO_TIMEOUT               (opcional, por defecto 8.0)

from flask import Blueprint, request, jsonify, current_app
import os, re, requests
import xml.etree.ElementTree as ET

bp_catastro = Blueprint("bp_catastro", __name__)

# --- Config desde entorno ---
RESOLVE_URL   = os.getenv("CATASTRO_RESOLVE_URL", "").strip()
RESOLVE_ACT   = os.getenv("CATASTRO_RESOLVE_SOAP_ACTION", "").strip()
CONSULTA_URL  = os.getenv("CATASTRO_CONSULTA_URL", "").strip()
CONSULTA_ACT  = os.getenv("CATASTRO_CONSULTA_SOAP_ACTION", "").strip()
TIMEOUT       = float(os.getenv("CATASTRO_TIMEOUT", "8.0"))

# Utilidad: busca una refcat de 20 alfanuméricos en un texto
REFCAT_RE = re.compile(r"\b([A-Za-z0-9]{20})\b")

def _first_text_by_suffix(root: ET.Element, suffixes=("rc1","refcat","rc")):
    for el in root.iter():
        tag = el.tag.split("}")[-1]  # quita namespace
        if tag.lower() in {s.lower() for s in suffixes} and (el.text or "").strip():
            return el.text.strip()
    # si no encontramos, probamos por regex sobre todo el XML
    xml_text = ET.tostring(root, encoding="unicode", method="xml")
    m = REFCAT_RE.search(xml_text or "")
    return m.group(1) if m else None

@bp_catastro.route("/api/catastro/resolve_direccion", methods=["POST", "OPTIONS"])
def resolve_direccion():
    if request.method == "OPTIONS":
        return ("", 204)

    if not RESOLVE_URL or not RESOLVE_ACT:
        return jsonify(ok=False, error="config_error",
                       message="Faltan variables CATASTRO_RESOLVE_URL / CATASTRO_RESOLVE_SOAP_ACTION"), 500

    body = request.get_json(silent=True) or {}
    direccion = (body.get("direccion") or "").strip()
    municipio = (body.get("municipio") or "").strip()
    provincia = (body.get("provincia") or "").strip()
    numero    = (body.get("numero") or "").strip()  # opcional
    if not (direccion and municipio and provincia):
        return jsonify(ok=False, error="bad_request",
                       message="Faltan direccion/municipio/provincia"), 400

    # Envelope típico para "Consulta_DNPRC" (puede variar según tu método/documentación que tengas en Render)
    envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <Consulta_DNPRC xmlns="http://tempuri.org/">
      <Provincia>{provincia}</Provincia>
      <Municipio>{municipio}</Municipio>
      <TipoVia></TipoVia>
      <NomVia>{direccion}</NomVia>
      <Numero>{numero}</Numero>
    </Consulta_DNPRC>
  </soap:Body>
</soap:Envelope>"""

    headers = {
        "Content-Type":
