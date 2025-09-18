# defense.py — SpainRoom backend hardening (Flask)
import os, re, time, hmac, hashlib, logging, json
from typing import Callable, Optional
from flask import request, abort, g, jsonify, Blueprint, current_app
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    import stripe  # opcional
except Exception:
    stripe = None

def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)

def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1","true","yes","on")

def _compile_regex(pat: str) -> Optional[re.Pattern]:
    if not pat:
        return None
    try:
        return re.compile(pat, re.I)
    except Exception:
        return None

def _parse_csv(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]

def _client_ip() -> str:
    # Respeta X-Forwarded-For en Render (ProxyFix ya aplicado)
    return request.headers.get("X-Forwarded-For", request.remote_addr or "" ).split(",")[0].strip()

def _admin_key_ok() -> bool:
    k = _env("ADMIN_API_KEY", "")
    if not k:
        return True  # si no hay key configurada, no forzar
    return request.headers.get("X-Admin-Key") == k

def _json_error(status: int, code: str, msg: str):
    resp = jsonify({"ok": False, "error": code, "message": msg})
    resp.status_code = status
    return resp

def _install_security_headers(app):
    csp = _env("CSP_POLICY",
               "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
               "script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src *")
    hsts_seconds = int(_env("HSTS_SECONDS", "31536000"))
    xfo = _env("X_FRAME_OPTIONS", "DENY")
    rp = _env("REFERRER_POLICY", "no-referrer")
    xcto = "nosniff"
    xss = "0"  # moderne browsers ignoran X-XSS-Protection; lo dejamos neutro

    @app.after_request
    def _set_headers(resp):
        resp.headers.setdefault("Content-Security-Policy", csp)
        resp.headers.setdefault("Strict-Transport-Security", f"max-age={hsts_seconds}; includeSubDomains; preload")
        resp.headers.setdefault("X-Frame-Options", xfo)
        resp.headers.setdefault("X-Content-Type-Options", xcto)
        resp.headers.setdefault("Referrer-Policy", rp)
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        resp.headers.setdefault("X-XSS-Protection", xss)
        return resp

def _install_request_guards(app):
    # Deny/Allow por IP + bloqueo por User-Agent
    allow = set(_parse_csv(_env("DEFENSE_IP_ALLOWLIST", "")))  # ej: "1.2.3.4,10.0.0.0/8"  (solo IP exacta aquí)
    deny  = set(_parse_csv(_env("DEFENSE_IP_DENYLIST", "")))
    ua_re = _compile_regex(_env("DEFENSE_BLOCK_UA_REGEX", r"(sqlmap|nikto|acunetix|nmap|dirbuster)"))

    # Límite blando de JSON
    max_json = int(_env("DEFENSE_MAX_JSON_KB", "512")) * 1024  # 512 KB por defecto
    slow_ms  = int(_env("DEFENSE_SLOW_MS", "1200"))

    @app.before_request
    def _pre_guard():
        g._t0 = time.perf_counter()

        ip = _client_ip()
        if ip in deny:
            abort(403)
        if allow and ip not in allow:
            # si definiste allowlist, todo lo demás bloqueado
            abort(403)

        ua = (request.headers.get("User-Agent") or "").lower()
        if ua_re and ua_re.search(ua or ""):
            abort(403)

        # Tamaño de JSON defensivo (además de MAX_CONTENT_LENGTH de Flask)
        if request.mimetype and "json" in request.mimetype.lower():
            raw = request.get_data(cache=True, as_text=False) or b""
            if len(raw) > max_json:
                abort(413)

        # Enforce Admin Key en rutas internas si prefieres (prefijo configurable)
        admin_prefix = _env("DEFENSE_ADMIN_PREFIX", "/api/admin/")
        if request.path.startswith(admin_prefix) and not _admin_key_ok():
            return _json_error(403, "forbidden", "Admin key required")

    @app.after_request
    def _slow_log(resp):
        dt_ms = int((time.perf_counter() - getattr(g, "_t0", time.perf_counter())) * 1000)
        if dt_ms >= slow_ms:
            current_app.logger.warning("SLOW %s %s %sms ip=%s ua=%s",
                                       request.method, request.path, dt_ms, _client_ip(),
                                       (request.headers.get("User-Agent") or "")[:200])
        return resp

def _install_rate_limits(app):
    # requiere flask-limiter
    try:
        from flask_limiter import Limiter
        from flask_limiter.util import get_remote_address

        # Ejemplos: "100/minute; 1000/hour"
        default_limits = _parse_csv(_env("RATE_LIMITS", "200/minute, 2000/hour"))
        burst_limits   = _parse_csv(_env("RATE_LIMITS_BURST", "20/10seconds"))

        def _key_func():
            # permite agrupar por API key si existe
            ak = request.headers.get("X-Admin-Key") or request.headers.get("Authorization")
            return ak or get_remote_address()

        limiter = Limiter(get_remote_address=_key_func, storage_uri=_env("LIMITER_STORAGE_URI", "memory://"))
        limiter.init_app(app)

        # Global default
        for l in default_limits:
            app.config.setdefault("RATELIMIT_DEFAULT", default_limits)

        # Ejemplo de burst en rutas sensibles
        @app.before_request
        def _apply_dynamic_limits():
            if request.path.startswith(("/api/login", "/api/admin/", "/voice")):
                for l in burst_limits:
                    limiter.limit(l)(lambda: None)()  # aplica límite inmediato

    except Exception as e:
        current_app.logger.warning("[DEFENSE] Rate limiter not active: %s", e)

def _install_proxyfix_and_cookies(app):
    # Render usa proxy → fija forwards para scheme/host/port
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    # Endurece cookies de sesión (si se usan)
    app.config.setdefault("SESSION_COOKIE_SECURE", True)
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("PREFERRED_URL_SCHEME", "https")

def _install_json_errors(app):
    @app.errorhandler(400)
    def _400(e): return _json_error(400, "bad_request", "Solicitud inválida")
    @app.errorhandler(401)
    def _401(e): return _json_error(401, "unauthorized", "No autorizado")
    @app.errorhandler(403)
    def _403(e): return _json_error(403, "forbidden", "Prohibido")
    @app.errorhandler(404)
    def _404(e): return _json_error(404, "not_found", "No encontrado")
    @app.errorhandler(405)
    def _405(e): return _json_error(405, "method_not_allowed", "Método no permitido")
    @app.errorhandler(413)
    def _413(e): return _json_error(413, "payload_too_large", "Carga demasiado grande")
    @app.errorhandler(429)
    def _429(e): return _json_error(429, "rate_limited", "Demasiadas solicitudes")
    @app.errorhandler(500)
    def _500(e): return _json_error(500, "server_error", "Error interno")

def _install_stripe_webhook(app):
    # Se registra solo si hay secret y stripe instalado
    secret = _env("STRIPE_WEBHOOK_SECRET", "")
    if not secret or stripe is None:
        app.logger.info("[DEFENSE] Stripe webhook not configured.")
        return

    bp = Blueprint("webhooks", __name__)

    @bp.post("/webhooks/stripe")
    def _stripe_webhook():
        payload = request.get_data(cache=False, as_text=False)
        sig_header = request.headers.get("Stripe-Signature", "")
        try:
            event = stripe.Webhook.construct_event(payload=payload, sig_header=sig_header, secret=secret)
        except Exception as exc:
            app.logger.warning("Stripe signature verification failed: %s", exc)
            return _json_error(400, "invalid_signature", "Firma inválida")

        # Manejo básico; adapta a tus necesidades
        t = event.get("type")
        app.logger.info("Stripe event: %s id=%s", t, event.get("id"))
        return jsonify(ok=True)

    app.register_blueprint(bp, url_prefix="")

def _install_logging(app):
    lvl = _env("DEFENSE_LOG_LEVEL","INFO").upper()
    app.logger.setLevel(getattr(logging, lvl, logging.INFO))
    if not app.logger.handlers:
        h = logging.StreamHandler()
        fmt = logging.Formatter('[%(asctime)s] %(levelname)s %(message)s')
        h.setFormatter(fmt)
        app.logger.addHandler(h)

def init_defense(app):
    """Call from app.py:  from defense import init_defense ; init_defense(app)"""
    _install_logging(app)
    _install_proxyfix_and_cookies(app)
    _install_security_headers(app)
    _install_request_guards(app)
    _install_rate_limits(app)
    _install_json_errors(app)
    _install_stripe_webhook(app)
    app.logger.info("[DEFENSE] Defense stack initialized.")
