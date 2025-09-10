# codigo_flask.py
# SpainRoom · Backend principal (Render: gunicorn codigo_flask:app)
# - Defensa (WAF ligero) embebida y ACTIVA (no inspecciona /voice, /health, /__routes)
# - IVR voz natural /voice/* (Polly.Conchita + SSML compatible + barge-in), rutas GET/POST blindadas
# - Root y fallback siempre TwiML
# - Post-Dial fallback: si no contestan → mensaje + buzón
# - Geocoder / Jobs (mock con Haversine)

from flask import Flask, request, jsonify, Response
import requests
from math import radians, sin, cos, sqrt, atan2
from urllib.parse import unquote_plus
import os, re, random, json

app = Flask(__name__)

# ========================== DEFENSA (WAF) ==========================
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
    if DEF_CFG["TRUST_PROXY"]:
        xf = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return xf or (request.remote_addr or "")
    return request.remote_addr or ""

def _jlog(event, **kw):
    try:
        from datetime import datetime, timezone
        payload = {"ts": datetime.now(tz=timezone.utc).isoformat(),
                   "event": event, "ip": _ip(), "path": request.path,
                   "method": request.method, "rid": request.headers.get("X-Request-ID", "")}
        payload.update(kw)
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    except Exception:
        pass

def _skip():
    p = request.path or ""
    for pref in DEF_CFG["SKIP_PREFIXES"]:
        if p.startswith(pref): return True
    return False

def _host_ok():
    if not DEF_CFG["STRICT_HOSTS"]: return None
    host = (request.headers.get("Host") or "").split(":")[0].lower().strip()
    return None if host in DEF_CFG["STRICT_HOSTS"] else f"host_not_allowed:{host}"

def _ua_ok():
    ua = (request.headers.get("User-Agent") or "").lower()
    for bad in DEF_CFG["BLOCKED_UA"]:
        if bad and bad in ua: return f"ua_blocked:{bad}"
    return None

def _size_ok():
    cl = request.content_length or 0
    return None if not cl or cl <= DEF_CFG["MAX_BODY"] else f"body_too_large:{cl}"

def _ct_ok():
    if request.method in ("GET","HEAD","OPTIONS"): return None
    ct = (request.headers.get("Content-Type","") or "").split(";")[0].strip().lower()
    return None if (not ct or ct in DEF_CFG["ALLOW_CT"]) else f"bad_content_type:{ct}"

def _badh_ok():
    for h in _BADH:
        if h in request.headers: return f"bad_header:{h}"
    return None

def _hit(pats, text):
    if not text: return None
    for p in pats:
        if re.search(p, text): return p
    return None

def _qs_ok():
    qs = request.query_string.decode("utf-8","ignore")
    if _hit(_SQLI, qs): return "sqli_qs"
    if _hit(_XSS, qs):  return "xss_qs"
    return None

def _trav_ok():
    return "traversal_path" if _hit(_TRAV, (request.path or "").lower()) else None

def _body_ok():
    try:
        raw = request.get_data(cache=False, as_text=True)[:4096]
    except Exception:
        return None
    if _hit(_SQLI, raw): return "sqli_body"
    if _hit(_XSS, raw):  return "xss_body"
    return None

@app.before_request
def _waf_gate():
    if _skip():
        return
    score, reasons = 0, []
    for chk in (_host_ok, _ua_ok, _size_ok, _ct_ok, _badh_ok, _trav_ok, _qs_ok, _body_ok):
        r = chk()
        if r:
            reasons.append(r)
            score += 2 if chk in (_host_ok,_ua_ok,_size_ok,_ct_ok,_badh_ok) else 3
    if request.method not in DEF_CFG["ALLOW_METHODS"]:
        reasons.append(f"method_not_allowed:{request.method}"); score += 2
    if score >= DEF_CFG["ANOMALY_THRESHOLD"]:
        _jlog("waf_block", reason=",".join(reasons), score=score)
        return Response(status=403)

@app.after_request
def _secure_headers(resp):
    resp.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    return resp

@app.get("/defense/health")
def defense_health():
    return jsonify(ok=True, defense="registered", skips=list(DEF_CFG["SKIP_PREFIXES"])), 200

# ========================== SALUD ==========================
@app.get("/health")
def health():
    return jsonify(ok=True, service="BACKEND-SPAINROOM"), 200

# ========================== UTILS ==========================
def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1); dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

@app.get("/api/geocode")
def geocode():
    address = request.args.get("address")
    if not address: return jsonify({"error": "Falta parámetro address"}), 400
    url = f"https://nominatim.openstreetmap.org/search?q={address}&format=json&limit=1"
    headers = {"User-Agent": "SpainRoom/1.0"}
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code != 200 or not r.json(): return jsonify({"error": "No se pudo geocodificar"}), 500
    d = r.json()[0]; return jsonify({"lat": float(d["lat"]), "lng": float(d["lon"])})

@app.get("/api/jobs/search")
def search_jobs():
    try:
        lat = float(request.args.get("lat")); lng = float(request.args.get("lng"))
        radius = float(request.args.get("radius_km", 2)); keyword = (request.args.get("q","")).lower()
    except Exception: return jsonify({"error":"Parámetros inválidos"}), 400
    ofertas = [
        {"id":1,"titulo":"Camarero/a","empresa":"Bar Central","lat":lat+0.01,"lng":lng+0.01},
        {"id":2,"titulo":"Dependiente/a","empresa":"Tienda Local","lat":lat+0.015,"lng":lng},
        {"id":3,"titulo":"Administrativo/a","empresa":"Gestoría","lat":lat-0.02,"lng":lng-0.01},
        {"id":4,"titulo":"Carpintero/a","empresa":"Taller Madera","lat":lat+0.03,"lng":lng+0.02},
    ]
    res=[]
    for o in ofertas:
        dist = calcular_distancia(lat,lng,o["lat"],o["lng"])
        if dist <= radius and (not keyword or keyword in o["titulo"].lower()):
            res.append({"id":o["id"],"titulo":o["titulo"],"empresa":o["empresa"],"distancia_km":round(dist,2)})
    return jsonify(res)

# ========================== IVR /voice/* ==========================
VOICE_PREFIX = "/voice"
TTS_VOICE = os.getenv("TTS_VOICE", "Polly.Conchita")  # Twilio-compatible
TWILIO_CALLER = os.getenv("TWILIO_VOICE_FROM", "+12252553716")

def _twiml(body: str) -> Response:
    body = body.strip()
    if not body.startswith("<Response"): body = f"<Response>{body}</Response>"
    return Response(body, mimetype="text/xml")

def _say_es_ssml(text: str) -> str:
    # SSML 100% compatible con Twilio (sin amazon:*)
    return f'<Say language="es-ES" voice="{TTS_VOICE}"><prosody rate="medium" pitch="+2%">{text}</prosody></Say>'

def _line(*opts): return random.choice(opts)
def _pause(sec=0.3): return f'<Pause length="{max(0.2, min(2.0, sec))}"/>'

def _gather_es(action: str, timeout="8", end_silence="auto",
               hints=("sí, si, no, propietario, inquilino, jaen, madrid, valencia, sevilla, "
                      "barcelona, malaga, granada, soy, me llamo, mi nombre es"),
               allow_dtmf: bool=False) -> str:
    gather_input = "speech dtmf" if allow_dtmf else "speech"
    return (f'<Gather input="{gather_input}" language="es-ES" timeout="{timeout}" '
            f'speechTimeout="{end_silence}" speechModel="phone_call" bargeIn="true" '
            f'action="{action}" method="POST" actionOnEmptyResult="true" hints="{hints}">')

def _ack(): return _line("vale","ok","perfecto","genial","ajá","te sigo","sí","de una","dale")

_IVR_MEM = {}  # CallSid -> { role, zone, name, miss }

PROVS = {"jaen":"Jaén","madrid":"Madrid","valencia":"Valencia","sevilla":"Sevilla",
         "barcelona":"Barcelona","malaga":"Málaga","granada":"Granada"}
FRAN_MAP = {
    "jaen":{"name":"Jaén","phone":"+34683634299"},
    "madrid":{"name":"Madrid Centro","phone":"+34600000001"},
    "valencia":{"name":"Valencia","phone":"+34600000003"},
    "sevilla":{"name":"Sevilla","phone":"+34600000004"},
    "barcelona":{"name":"Barcelona","phone":"+34600000005"},
    "malaga":{"name":"Málaga","phone":"+34600000006"},
    "granada":{"name":"Granada","phone":"+34600000007"},
}
_YES = {"si","sí","vale","correcto","claro","ok","de acuerdo"}
_NO  = {"no","negativo"}

def _yesno(s: str) -> str:
    s = (s or "").lower().strip()
    if any(w in s for w in _YES): return "yes"
    if any(w in s for w in _NO):  return "no"
    return ""

def _role(s: str) -> str:
    s = (s or "").lower()
    if "propiet" in s or "dueñ" in s: return "propietario"
    if "inquil" in s or "alquil" in s or "habitacion" in s or "habitación" in s or "busco" in s: return "inquilino"
    return ""

def _zone(s: str) -> str:
    s = (s or "").lower().strip()
    s = (s.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u"))
    aliases = {"barna":"barcelona","md":"madrid","vlc":"valencia","sevill":"sevilla"}
    for k,v in aliases.items():
        if k in s: s = v
    for key in PROVS.keys():
        if key in s or s == key: return key
    return ""

def _name(s: str) -> str:
    s = (s or "").strip(); low = s.lower()
    for cue in ["me llamo","soy","mi nombre es"]:
        if cue in low:
            after = s.lower().split(cue,1)[1].strip()
            return after.title()[:60]
    parts = [w for w in s.split() if len(w)>1]
    return parts[0].title()[:40] if parts else ""

def _assign(zone_key: str):
    return FRAN_MAP.get(zone_key or "", {"name":"Central SpainRoom","phone":None})

@app.route("/voice/health", methods=["GET"])
def voice_health():
    return jsonify(ok=True, service="voice"), 200

# GET/POST blindado
@app.route("/voice/answer", methods=["GET
