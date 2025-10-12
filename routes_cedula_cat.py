# routes_cedula_cat.py — Adaptador Cataluña (Agència de l’Habitatge)
# Verificación real de cédula: API oficial (si existe) o HTML parsing configurable
# Nora · 2025-10-11

import os
import re
import json
from datetime import datetime
from typing import Optional, Dict, Any

import requests
from flask import Blueprint, request, jsonify, Response
try:
    # Fallback suave si no está instalada; solo se usará si configuras el modo HTML
    from bs4 import BeautifulSoup  # pip install beautifulsoup4
except Exception:
    BeautifulSoup = None

bp_cedula_cat = Blueprint("cedula_cat", __name__)

# ===================== Configuración =====================

# Opción 1: API oficial (si la tienes)
CATA_CAT_API_URL = (os.getenv("CATA_CAT_API_URL") or "").strip()       # p.ej. https://api.habitatge.gencat.cat/cedula
CATA_CAT_API_KEY = (os.getenv("CATA_CAT_API_KEY") or "").strip()       # si requiere token

# Opción 2: HTML público (fallback scraping controlado)
# URL del buscador público de cédulas (si no hay API)
CATA_CAT_HTML_URL      = (os.getenv("CATA_CAT_HTML_URL") or "").strip()
CATA_CAT_HTML_METHOD   = (os.getenv("CATA_CAT_HTML_METHOD") or "GET").strip().upper()  # GET | POST
# Nombre del parámetro de consulta (p.ej. "refcat", "rc", "numCedula", etc.)
CATA_CAT_HTML_PARAM_RC = (os.getenv("CATA_CAT_HTML_PARAM_RC") or "refcat").strip()
# Selectores CSS (ajústalos a la página real)
CATA_CAT_SEL_STATUS    = (os.getenv("CATA_CAT_SEL_STATUS") or ".estado, .result .status").strip()
CATA_CAT_SEL_EXPIRA    = (os.getenv("CATA_CAT_SEL_EXPIRA") or ".caducidad, .result .expires").strip()
CATA_CAT_SEL_NUM       = (os.getenv("CATA_CAT_SEL_NUM") or ".numero, .result .number").strip()

# Timeouts
CATA_CAT_TIMEOUT = float(os.getenv("CATA_CAT_TIMEOUT") or "12.0")

# Provincias admitidas para Cataluña
CAT_PROVINCES = {"barcelona", "girona", "lleida", "tarragona", "catalunya", "cataluña"}

# ===================== Utilidades =====================

def _corsify(resp: Response) -> Response:
    origin = request.headers.get("Origin", "*")
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Key, X-Catastro-Mode"
    return resp

def _norm(s: Optional[str]) -> str:
    return (s or "").strip()

def _status_map(text: str) -> str:
    """Normaliza el estado a {vigente, caducada, pendiente, no_consta}."""
    t = (text or "").lower()
    if "vigent" in t or "vigente" in t:
        return "vigente"
    if "caduc" in t or "expira" in t or "expirad" in t:
        return "caducada"
    if "tram" in t or "trámite" in t or "tramite" in t or "en curs" in t:
        return "pendiente"
    return "no_consta"

def _extract_date(text: str) -> Optional[str]:
    """Reconoce AAAA-MM-DD o DD/MM/AAAA y devuelve ISO."""
    if not text:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    m = re.search(r"(\d{2}/\d{2}/\d{4})", text)
    if m:
        dd, mm, yyyy = m.group(1).split("/")
        return f"{yyyy}-{mm}-{dd}"
    return None

# ===================== Lookups =====================

def _api_lookup(refcat: str) -> Optional[Dict[str, Any]]:
    """Consulta API oficial si está configurada."""
    if not CATA_CAT_API_URL:
        return None
    headers = {"Accept": "application/json"}
    if CATA_CAT_API_KEY:
        headers["Authorization"] = f"Bearer {CATA_CAT_API_KEY}"
    try:
        r = requests.get(
            CATA_CAT_API_URL,
            params={"refcat": refcat},
            headers=headers,
            timeout=CATA_CAT_TIMEOUT,
        )
        r.raise_for_status()
        j = r.json()
        # Esperado (ejemplo): {"status":"vigente","expires_at":"2027-10-11","number":"ABC123"}
        status = _status_map(str(j.get("status", "")))
        return {
            "source": "api",
            "status": status,
            "expires_at": j.get("expires_at") or None,
            "number": j.get("number") or j.get("cedula") or None,
            "raw": j,
        }
    except Exception:
        return None

def _html_lookup(refcat: str) -> Optional[Dict[str, Any]]:
    """Consulta HTML público si no hay API; requiere beautifulsoup4 instalada."""
    if not CATA_CAT_HTML_URL or not BeautifulSoup:
        return None
    try:
        sess = requests.Session()
        if CATA_CAT_HTML_METHOD == "POST":
            r = sess.post(CATA_CAT_HTML_URL, data={CATA_CAT_HTML_PARAM_RC: refcat}, timeout=CATA_CAT_TIMEOUT)
        else:
            r = sess.get(CATA_CAT_HTML_URL, params={CATA_CAT_HTML_PARAM_RC: refcat}, timeout=CATA_CAT_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        status_el = soup.select_one(CATA_CAT_SEL_STATUS)
        expira_el = soup.select_one(CATA_CAT_SEL_EXPIRA)
        num_el    = soup.select_one(CATA_CAT_SEL_NUM)

        status  = _status_map(status_el.get_text(" ", strip=True) if status_el else "")
        expires = _extract_date(expira_el.get_text(" ", strip=True) if expira_el else "")
        number  = (num_el.get_text(" ", strip=True) if num_el else None)

        return {
            "source": "html",
            "status": status,
            "expires_at": expires,
            "number": number,
        }
    except Exception:
        return None

# ===================== Endpoint =====================

@bp_cedula_cat.route("/api/legal/cat/check", methods=["POST", "OPTIONS"])
def check_cat():
    if request.method == "OPTIONS":
        return _corsify(Response(status=204))

    body = request.get_json(silent=True) or {}
    refcat    = _norm(body.get("refcat"))
    provincia = _norm(body.get("provincia")).lower()

    # Validaciones básicas
    if provincia and provincia not in CAT_PROVINCES:
        return _corsify(jsonify(ok=False, error="bad_request", message="Provincia no es Cataluña")), 400
    if not refcat or len(refcat) != 20:
        return _corsify(jsonify(ok=False, error="bad_request", message="Se requiere refcat de 20 caracteres")), 400

    # 1) API oficial (si está configurada)
    data = _api_lookup(refcat)

    # 2) Fallback HTML (si no hay API o ésta falla)
    if not data:
        data = _html_lookup(refcat)

    # 3) Sin fuente fiable => no constar
    if not data:
        return _corsify(jsonify(
            ok=True,
            status="no_consta",
            data={"refcat": refcat},
            checked_at=datetime.utcnow().isoformat()
        ))

    # 4) Respuesta normalizada
    resp = {
        "ok": True,
        "status": data["status"],  # vigente | caducada | pendiente | no_consta
        "data": {
            "refcat": refcat,
            "expires_at": data.get("expires_at"),
            "cedula_numero": data.get("number"),
            "source": data.get("source"),
        },
        "checked_at": datetime.utcnow().isoformat()
    }
    return _corsify(jsonify(resp))
