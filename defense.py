# defense.py â€” SpainRoom BACKEND
# Defensa bÃ¡sica: rate limit (si flask-limiter estÃ¡ instalado), filtros de UA y rutas peligrosas.

import os
from flask import request, abort

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
except Exception:
    Limiter = None  # funcionamiento degradado sin dependencia


DEFAULT_LIMIT = os.getenv("RATE_LIMIT_DEFAULT", "60/minute")
BURST_LIMIT = os.getenv("RATE_LIMIT_BURST", "30/10second")


def init_defense(app):
    """Activa defensas en una app Flask. Devuelve True al finalizar."""
    # 1) Rate limiting (opcional)
    if Limiter is not None:
        try:
            Limiter(
                key_func=get_remote_address,
                app=app,
                default_limits=[DEFAULT_LIMIT],
            )
            app.logger.info("[DEFENSE] Rate limit ON (%s, burst %s)", DEFAULT_LIMIT, BURST_LIMIT)
        except Exception as e:
            app.logger.warning("[DEFENSE] Could not init rate limiting: %s", e)
    else:
        app.logger.warning("[DEFENSE] flask-limiter not installed; skipping rate limit")

    # 2) Filtros simples de agentes y rutas
    BAD_UA = ("sqlmap", "nmap", "nikto", "acunetix", "dirbuster", "wpscan")
    BAD_PATHS = ("/wp-admin", "/phpmyadmin", "/.env", "/.git", "/server-status")

    @app.before_request
    def _pre_block():
        path = (request.path or "").lower()
        ua = (request.headers.get("User-Agent") or "").lower()

        if any(bad in ua for bad in BAD_UA):
            abort(403)  # Forbidden
        if any(path.startswith(p) for p in BAD_PATHS):
            abort(404)  # Not Found para despistar

    return True
