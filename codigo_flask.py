# codigo_flask.py
# SpainRoom · Backend principal (Render: gunicorn codigo_flask:app)
# - Defensa (WAF) embebida y ACTIVA (sin inspeccionar /voice, /health, /__routes)
# - IVR /voice/*: formulario por turnos (rol→población→nombre→teléfono→nota→confirmación)
# - Voz natural: variaciones de copy + un <Say> por turno (SSML <prosody> + micro-pausas)
# - Tareas con transcript + SMS a franquiciados (multi-destino) y UI /admin/tasks
# - Territorios (España): microzonas (bbox) + rejilla nacional; endpoints /territories/*
# - Geocoder (Nominatim) y utils

from flask import Flask, request, jsonify, Response, Response as _FlaskResponse, has_request_context
import requests, json, os, re, random, tempfile, shutil
from math import radians, sin, cos, sqrt, atan2, floor
from urllib.parse import unquote_plus
from datetime import datetime, timezone

app = Flask(__name__)

# ==============================
# Config / Entorno
# ==============================
CENTRAL_PHONE = os.getenv("CENTRAL_PHONE", "+12252553716")       # fallback si no hay franquiciado
SMS_FROM      = os.getenv("TWILIO_MESSAGING_FROM", "+12252553716")
VOICE_FROM    = os.getenv("TWILIO_VOICE_FROM", "+12252553716")
TTS_VOICE     = os.getenv("TTS_VOICE", "Polly.Conchita")         # Twilio-Polly compatible
TERR_FILE     = os.getenv("TERR_FILE", "/tmp/spainroom_territories.json")
TERR_TOKEN    = os.getenv("TERR_TOKEN", "")                      # token admin para /territories/*
GRID_SIZE_DEG = float(os.getenv("GRID_SIZE_DEG", "0.05"))        # ~0.05º ≈ 5–6 km

# ==============================
# Helpers base
# ==============================
def _twiml(body: str) -> Response:
    """Devuelve TwiML válido text/xml (EVITA 500 y el 'goodbye')."""
    b = (body or "").strip()
    if not b.startswith("<Response"): b = f"<Response>{b}</Response>"
    return Response(b, mimetype="text/xml")

def _now_iso():
    return datetime.now(tz=timezone.utc).isoformat()

# ==============================
# DEFENSA (WAF) embebida
# ==============================
DEF_CFG = {
    "MAX_BODY": int(os.getenv("DEFENSE_MAX_BODY", "524288")),
    "ALLOW_METHODS": set((os.getenv("DEFENSE_ALLOW_METHODS", "GET,POST,OPTIONS")).split(",")),
    "ALLOW_CT": set(ct.strip().lower() for ct in os.getenv(
        "DEFENSE_ALLOW_CT",
        "application/json,application/x-www-form-urlencoded,multipart/form-data,text/xml,application/xml"
    ).split(",")),
    "BLOCKED_UA": set(ua.strip().lower() for ua in os.getenv(
        "DEFENSE_BLOCKED_UA", "sqlmap,nmap,nikto,dirbuster,acunetix").split(",")),
    "STRICT_HOSTS": [h.strip().lower() for h in os.getenv("DEFENSE_HOSTS", "").split(",") if h.strip()],
    "TRUST_PROXY": os.getenv("DEFENSE_TRUST_PROXY", "true").lower() == "true",
    "ANOMALY_THRESHOLD": int(os.getenv("DEFENSE_ANOMALY_THRESHOLD", "8")),
    "SKIP_PREFIXES": [p.strip() for p in os.getenv("DEFENSE_SKIP_PREFIXES", "/voice,/__routes,/health").split(",") if p.strip()],
}
_SQLI = [r"(?i)\bunion\b.+\bselect\b", r"(?i)\b(select|insert|update|delete)\b.+\bfrom\b",
         r"(?i)\bor\s+1=1\b", r"(?i)\bsleep\(", r"(?i)information_schema", r"(?i)load_file\("]
_XSS  = [r"(?i)<script\b", r"(?i)javascript:", r"(?i)onerror\s*="]
_TRAV = [r"\.\./", r"%2e%2e%2f", r"\x00"]
_BADH = ["X-Original-URL", "X-Override-URL"]

def _ip():
    if has_request_context():
        if DEF_CFG["TRUST_PROXY"]:
            xf = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            return xf or (request.remote_addr or "")
        return request.remote_addr or ""
    return "-"

def _jlog(event, **kw):
    payload = {
        "ts": _now_iso(),
        "event": event,
        "ip": _ip(),
        "path": request.path if has_request_context() else "-",
        "method": request.method if has_request_context() else "-",
        "rid": (request.headers.get("X-Request-ID", "") if has_request_context() else "")
    }
    payload.update(kw)
    try: print(json.dumps(payload, ensure_ascii=False), flush=True)
    except Exception: print(str(payload), flush=True)

def _skip_waf():
    if not has_request_context(): return True
    p = request.path or ""
    return any(p.startswith(pref) for pref in DEF_CFG["SKIP_PREFIXES"])

def _host_ok():
    if not DEF_CFG["STRICT_HOSTS"] or not has_request_context(): return None
    host = (request.headers.get("Host") or "").split(":")[0].lower().strip()
    return None if host in DEF_CFG["STRICT_HOSTS"] else f"host_not_allowed:{host}"

def _ua_ok():
    if not has_request_context(): return None
    ua = (request.headers.get("User-Agent") or "").lower()
    for bad in DEF_CFG["BLOCKED_UA"]:
        if bad and bad in ua: return f"ua_blocked:{bad}"
    return None

def _size_ok():
    if not has_request_context(): return None
    cl = request.content_length or 0
    return None if not cl or cl <= DEF_CFG["MAX_BODY"] else
