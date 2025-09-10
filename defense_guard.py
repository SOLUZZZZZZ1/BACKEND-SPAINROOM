# defense_guard.py
# SpainRoom · Backend Defense (WAF ligero + firmas + headers + logs + rate limit)
# - Capa antes de cada request: inspección de payload/UA/ruta/tamaño/SQLi/XSS/traversal
# - Firmas de Webhooks (Twilio / Stripe)
# - Cabeceras seguras + CORS estricto (si está flask_cors)
# - Rate limit (si está flask_limiter)
# - Logs JSON con máscara de PII y correlación por request-id

from functools import wraps
from flask import request, Response, current_app, g
import os, re, json, time, hmac, hashlib
from urllib.parse import unquote_plus

# ======================
# Config por entorno
# ======================
CFG = {
    "MAX_BODY": int(os.getenv("DEFENSE_MAX_BODY", "524288")),  # 512 KB
    "ALLOW_METHODS": set((os.getenv("DEFENSE_ALLOW_METHODS", "GET,POST,OPTIONS")).split(",")),
    "BLOCKED_UA": set(ua.strip().lower() for ua in os.getenv("DEFENSE_BLOCKED_UA", "sqlmap,nmap,nikto,dirbuster,acunetix").split(",")),
    "ALLOW_CONTENT_TYPES": set(ct.strip().lower() for ct in os.getenv("DEFENSE_ALLOW_CT", "application/json,application/x-www-form-urlencoded,multipart/form-data,text/xml,application/xml").split(",")),
    "TWILIO_AUTH_TOKEN": os.getenv("TWILIO_AUTH_TOKEN", ""),
    "STRIPE_ENDPOINT_SECRET": os.getenv("STRIPE_ENDPOINT_SECRET", ""),  # whsec_...
    "CORS_ORIGINS": os.getenv("CORS_ORIGINS", "https://spainroom.vercel.app").split(","),
    "STRICT_HOSTS": [h.strip() for h in os.getenv("DEFENSE_HOSTS", "").split(",") if h.strip()],
    "TRUST_PROXY": os.getenv("DEFENSE_TRUST_PROXY", "true").lower() == "true",
    "ANOMALY_THRESHOLD": int(os.getenv("DEFENSE_ANOMALY_THRESHOLD", "8")),
}

SQLI_PATTERNS = [
    r"(?i)\bunion\b.+\bselect\b", r"(?i)\b(select|insert|update|delete)\b.+\bfrom\b",
    r"(?i)or\s+1=1", r"(?i)sleep\(", r"(?i)information_schema", r"(?i)load_file\(",
]
XSS_PATTERNS = [r"(?i)<script\b", r"(?i)javascript:", r"(?i)onerror\s*="]
TRAVERSAL_PATTERNS = [r"\.\./", r"%2e%2e%2f", r"\x00"]
BAD_HEADERS = ["X-Original-URL", "X-Override-URL"]

def _now_iso():
    import datetime as dt
    return dt.datetime.utcnow().isoformat() + "Z"

def _client_ip():
    if CFG["TRUST_PROXY"]:
        return (request.headers.get("X-Forwarded-For","").split(",")[0] or request.remote_addr or "").strip()
    return request.remote_addr or ""

def _mask(s):
    if not s: return s
    if isinstance(s, str) and s.startswith("+") and len(s) >= 7:
        return s[:-6] + "******"
    return s

def _jlog(event, **kw):
    try:
        payload = {"ts": _now_iso(), "event": event, "ip": _client_ip(), "path": request.path,
                   "method": request.method, "rid": request.headers.get("X-Request-ID","")}
        payload.update(kw)
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    except Exception:
        pass

def _normalize_host():
    if not CFG["STRICT_HOSTS"]:
        return None
    host = request.headers.get("Host","").split(":")[0].strip().lower()
    if host not in CFG["STRICT_HOSTS"]:
        return f"host_not_allowed:{host}"
    return None

def _content_type_ok():
    ct = (request.headers.get("Content-Type","") or "").split(";")[0].strip().lower()
    if request.method in ("GET","HEAD","OPTIONS"):
        return None
    if ct and ct in CFG["ALLOW_CONTENT_TYPES"]:
        return None
    return f"bad_content_type:{ct}"

def _ua_ok():
    ua = (request.headers.get("User-Agent") or "").lower()
    for bad in CFG["BLOCKED_UA"]:
        if bad and bad in ua:
            return f"ua_blocked:{bad}"
    return None

def _size_ok():
    cl = request.content_length or 0
    if cl and cl > CFG["MAX_BODY"]:
        return f"body_too_large:{cl}"
    return None

def _bad_headers():
    for h in BAD_HEADERS:
        if h in request.headers:
            return f"bad_header:{h}"
    return None

def _pattern_hit(patterns, text):
    if not text: return None
    for p in patterns:
        if re.search(p, text):
            return p
    return None

def _traversal_ok(path):
    if _pattern_hit(TRAVERSAL_PATTERNS, path) is not None:
        return "traversal_path"
    return None

def _qstring_ok():
    qs = request.query_string.decode("utf-8","ignore")
    if _pattern_hit(SQLI_PATTERNS, qs): return "sqli_qs"
    if _pattern_hit(XSS_PATTERNS, qs):  return "xss_qs"
    return None

def _body_ok():
    try:
        raw = request.get_data(cache=False, as_text=True)[:4096]  # inspección parcial
    except Exception:
        return None
    if _pattern_hit(SQLI_PATTERNS, raw): return "sqli_body"
    if _pattern_hit(XSS_PATTERNS, raw):  return "xss_body"
    return None

def waf_inspect():
    """Devuelve razón (str) si se bloquea, o None si pasa."""
    score = 0
    reasons = []

    r = _normalize_host()
    if r: reasons.append(r); score += 3

    r = _ua_ok()
    if r: reasons.append(r); score += 3

    r = _size_ok()
    if r: reasons.append(r); score += 3

    r = _content_type_ok()
    if r: reasons.append(r); score += 2

    r = _traversal_ok(request.path.lower())
    if r: reasons.append(r); score += 4

    r = _qstring_ok()
    if r: reasons.append(r); score += 3

    r = _body_ok()
    if r: reasons.append(r); score += 3

    if request.method not in CFG["ALLOW_METHODS"]:
        reasons.append(f"method_not_allowed:{request.method}"); score += 2

    if score >= CFG["ANOMALY_THRESHOLD"]:
        return f"blocked[{score}]:" + ",".join(reasons)
    return None

# ======================
# Firmas Webhooks
# ======================
def verify_twilio(f):
    """Verifica X-Twilio-Signature para este endpoint."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = CFG["TWILIO_AUTH_TOKEN"]
        if not token:
            _jlog("twilio_verify_skip", reason="no_token")
            return f(*args, **kwargs)
        sig = request.headers.get("X-Twilio-Signature", "")
        url = request.url
        # Construir el string: URL + params ordenados (Twilio rule)
        params = request.form or {}
        payload = url
        for k in sorted(params.keys()):
            payload += k + params.get(k, "")
        digest = hmac.new(token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
        expected = digest.encode("base64") if hasattr(digest, "encode") else None
        # Twilio usa base64; equivalencia Python:
        import base64
        expected = base64.b64encode(digest).decode("utf-8")
        if not hmac.compare_digest(sig, expected):
            _jlog("twilio_verify_fail", sig=_mask(sig), expected=_mask(expected))
            return Response(status=403)
        _jlog("twilio_verify_ok")
        return f(*args, **kwargs)
    return wrapper

def verify_stripe(f):
    """Verifica la firma Stripe en webhooks (Stripe-Signature + endpoint secret)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        secret = CFG["STRIPE_ENDPOINT_SECRET"]
        if not secret:
            _jlog("stripe_verify_skip", reason="no_secret")
            return f(*args, **kwargs)
        sig_header = request.headers.get("Stripe-Signature", "")
        payload = request.get_data()
        try:
            import stripe
            stripe.Webhook.construct_event(payload, sig_header, secret)
            _jlog("stripe_verify_ok")
            return f(*args, **kwargs)
        except Exception as e:
            _jlog("stripe_verify_fail", error=str(e))
            return Response(status=400)
    return wrapper

# ======================
# Registro principal
# ======================
def register_defense(app):
    """Activa WAF + headers + CORS + rate limit (opcional) en la app."""
    # WAF
    @app.before_request
    def _waf_gate():
        reason = waf_inspect()
        if reason:
            _jlog("waf_block", reason=reason)
            return Response(status=403)

    # Cabeceras seguras
    @app.after_request
    def _secure_headers(resp):
        resp.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        # API: CSP restrictiva (ajusta si sirves HTML)
        resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return resp

    # CORS estricto (si está Flask-Cors)
    try:
        from flask_cors import CORS
        CORS(app, resources={r"/api/*": {"origins": CFG["CORS_ORIGINS"]}})
        _jlog("cors_enabled", origins=CFG["CORS_ORIGINS"])
    except Exception:
        pass

    # Rate limit ya se inicializa en defense.py si existe; aquí solo logging
    _jlog("defense_registered", max_body=CFG["MAX_BODY"], anomaly=CFG["ANOMALY_THRESHOLD"])

# ======================
# Decoradores útiles que exponemos
# ======================
twilio_verified = verify_twilio
stripe_verified = verify_stripe
