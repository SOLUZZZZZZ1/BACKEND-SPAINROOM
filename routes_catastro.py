# routes_catastro.py — Resolución por Dirección/Ref. Catastral (modo SOAP/DEMO) + helpers DB
import os, json, hashlib, unicodedata, datetime
from flask import Blueprint, request, jsonify, current_app

bp_catastro = Blueprint("catastro", __name__)

def _ok(**kw): return jsonify({"ok": True, **kw})

def _norm(s: str) -> str:
    s = (s or "").strip()
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")

# ===================== Integración Catastro (dos modos) =====================
# MODO = 'soap' → llamar SOAP (si configuras URL/ACTION); 'demo' por defecto.
CATASTRO_MODE = os.getenv("CATASTRO_MODE", "demo").lower()
SOAP_URL_RESOLVE = os.getenv("CATASTRO_SOAP_URL_RESOLVE", "")   # ej: https://ovc.catastro.meh.es/ovcservweb/OVCSWLocalizacionRC/OVCCallejero.asmx
SOAP_ACTION_RESOLVE = os.getenv("CATASTRO_SOAP_ACTION_RESOLVE", "")  # ej: "http://tempuri.org/Consulta_DNPLOC"
SOAP_URL_REF = os.getenv("CATASTRO_SOAP_URL_REF", "")          # ej: https://ovc.catastro.meh.es/ovcservweb/OVCSWRC/OVCCallejero.asmx
SOAP_ACTION_REF = os.getenv("CATASTRO_SOAP_ACTION_REF", "")    # ej: "http://tempuri.org/Consulta_DNPRC"
HTTP_TIMEOUT = float(os.getenv("CATASTRO_TIMEOUT", "8"))

def _soap_call(url: str, action: str, envelope: str):
    """Llamada SOAP genérica (si configuras endpoints)."""
    import requests
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": action
    }
    r = requests.post(url, data=envelope.encode("utf-8"), headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.text

def _fake_refcat_from(direccion: str, municipio: str, provincia: str) -> str:
    base = (direccion or "") + "|" + (municipio or "") + "|" + (provincia or "")
    h = hashlib.sha1(base.encode("utf-8")).hexdigest().upper()
    # 20 alfanuméricos "estilo" refcat (demo)
    return (h[:12] + h[20:28])[:20]

# ===================== BBDD: legal_requirements =====================
def _pg_conn():
    url = os.getenv("DATABASE_URL", "")
    if not url: return None
    try:
        import psycopg2
    except ImportError:
        os.system(f"{__import__('sys').executable} -m pip install -q psycopg2-binary")
        import psycopg2
    if "sslmode" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return psycopg2.connect(url)

def _query_requirement(municipio: str|None, provincia: str|None):
    """Devuelve la fila más específica (municipio+provincia) o sólo provincia, o None."""
    con = _pg_conn()
    if not con: return None
    try:
        with con, con.cursor() as cur:
            if municipio and provincia:
                cur.execute("""
                  SELECT municipality, province, cat, doc, org, vig, notas, link
                  FROM legal_requirements
                  WHERE lower(municipality)=lower(%s) AND lower(province)=lower(%s)
                  ORDER BY updated_at DESC
                  LIMIT 1
                """, (municipio, provincia))
                row = cur.fetchone()
                if row:
                    keys = ["municipality","province","cat","doc","org","vig","notas","link"]
                    return dict(zip(keys, row))
            if provincia:
                cur.execute("""
                  SELECT municipality, province, cat, doc, org, vig, notas, link
                  FROM legal_requirements
                  WHERE municipality IS NULL AND lower(province)=lower(%s)
                  ORDER BY updated_at DESC
                  LIMIT 1
                """, (provincia,))
                row = cur.fetchone()
                if row:
                    keys = ["municipality","province","cat","doc","org","vig","notas","link"]
                    return dict(zip(keys, row))
    except Exception as e:
        current_app.logger.warning("legal_requirements query failed: %s", e)
    finally:
        try: con.close()
        except Exception: pass
    return None

# ===================== ENDPOINTS =====================

@bp_catastro.route("/api/catastro/resolve_direccion", methods=["POST","OPTIONS"])
def resolve_direccion():
    """Entrada: { direccion, municipio, provincia, cp? } → { refcat?, direccion_normalizada, municipio, provincia }"""
    if request.method == "OPTIONS": return ("", 204)
    body = request.get_json(silent=True) or {}
    direccion = body.get("direccion") or ""
    municipio = body.get("municipio") or ""
    provincia = body.get("provincia") or ""
    cp        = (body.get("cp") or "").strip()

    if not (direccion and municipio and provincia):
        return jsonify(ok=False, error="missing_fields", needed="direccion, municipio, provincia"), 400

    if CATASTRO_MODE == "soap" and SOAP_URL_RESOLVE and SOAP_ACTION_RESOLVE:
        # TODO: arma el envelope correcto para el servicio SOAP que uses (Consulta_DNPLOC / etc.)
        # Ejemplo de patrón (ajústalo a la doc oficial):
        envelope = f"""<?xml version="1.0" encoding="utf-8"?>
          <soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                         xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                         xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
            <soap:Body>
              <Consulta_DNPLOC xmlns="http://tempuri.org/">
                <Provincia>{provincia}</Provincia>
                <Municipio>{municipio}</Municipio>
                <Direccion>{direccion}</Direccion>
                <CP>{cp}</CP>
              </Consulta_DNPLOC>
            </soap:Body>
          </soap:Envelope>"""
        try:
            xml_text = _soap_call(SOAP_URL_RESOLVE, SOAP_ACTION_RESOLVE, envelope)
            # TODO: parsea xml_text para extraer refcat y dirección normalizada.
            # Devolvemos por ahora sin refcat (hasta que conectes la respuesta).
            return _ok(direccion_normalizada=_norm(direccion), municipio=municipio, provincia=provincia, refcat=None, raw="soap_ok")
        except Exception as e:
            current_app.logger.warning("SOAP resolve_direccion failed: %s", e)

    # DEMO: si no SOAP, fabricamos una refcat sintética para poder continuar
    refcat = _fake_refcat_from(direccion, municipio, provincia)
    return _ok(direccion_normalizada=_norm(direccion), municipio=municipio, provincia=provincia, refcat=refcat, raw="demo")

@bp_catastro.route("/api/catastro/consulta_refcat", methods=["POST","OPTIONS"])
def consulta_refcat():
    """Entrada: { refcat } → { uso, superficie_m2, antiguedad, … } (real si SOAP, demo si no)"""
    if request.method == "OPTIONS": return ("", 204)
    body = request.get_json(silent=True) or {}
    refcat = (body.get("refcat") or "").strip()
    if not refcat:
        return jsonify(ok=False, error="missing_refcat"), 400

    if CATASTRO_MODE == "soap" and SOAP_URL_REF and SOAP_ACTION_REF:
        # TODO: envelope correcto para Consulta_DNPRC / etc.
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
        try:
            xml_text = _soap_call(SOAP_URL_REF, SOAP_ACTION_REF, envelope)
            # TODO: parse xml_text y devolver campos reales
            return _ok(refcat=refcat, uso="Residencial", superficie_m2=None, antiguedad=None, raw="soap_ok")
        except Exception as e:
            current_app.logger.warning("SOAP consulta_refcat failed: %s", e)

    # DEMO
    h = int(hashlib.sha1(refcat.encode("utf-8")).hexdigest(), 16)
    return _ok(refcat=refcat,
               uso="Residencial" if h % 2 == 0 else "Mixto",
               superficie_m2=60 + (h % 80),
               antiguedad=1990 + (h % 25),
               raw="demo")

@bp_catastro.route("/api/legal/requirement", methods=["POST","OPTIONS"])
def legal_requirement():
    """Busca en tabla legal_requirements por municipio+provincia (o sólo provincia)."""
    if request.method == "OPTIONS": return ("", 204)
    body = request.get_json(silent=True) or {}
    municipio = body.get("municipio")
    provincia = body.get("provincia")
    row = _query_requirement(municipio, provincia)
    if row:
        return _ok(requirement={
            "cat": row["cat"], "doc": row["doc"], "org": row["org"],
            "vig": row["vig"], "notas": row["notas"], "link": row["link"],
            "municipio": row["municipality"], "provincia": row["province"]
        })
    return jsonify(ok=False, error="not_found"), 404
