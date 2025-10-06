# routes_payments_api.py — Pagos en la API principal (demo + Stripe real si hay clave)
import os
from urllib.parse import urljoin
from flask import Blueprint, request, jsonify, make_response, current_app

bp_pay = Blueprint("payments_api", __name__)

def _allowed_origin(origin: str | None) -> bool:
    if not origin:
        return False
    if origin.endswith(".vercel.app"):
        return True
    return origin in {
        "http://localhost:5176", "http://127.0.0.1:5176",
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:8089", "http://127.0.0.1:8089",
        "http://localhost:8080", "http://127.0.0.1:8080",
    }

def _abs_url(origin: str, path: str) -> str:
    if not path:
        return origin
    if path.startswith(("http://", "https://")):
        return path
    return urljoin(origin.rstrip("/") + "/", path.lstrip("/"))

@bp_pay.route("/create-checkout-session", methods=["POST", "OPTIONS"])
def create_checkout_session():
    # ── Preflight CORS ─────────────────────────────────────────
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        origin = request.headers.get("Origin", "")
        if _allowed_origin(origin):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    # ── Petición real ──────────────────────────────────────────
    data   = request.get_json(silent=True) or {}
    origin = request.headers.get("Origin") or "http://localhost:5176"

    amount_eur   = int(data.get("amount") or 150)
    currency     = (data.get("currency") or "eur").lower()
    success_path = data.get("success_path") or "/?reserva=ok"
    cancel_path  = data.get("cancel_path")  or "/?reserva=error"
    success_url  = _abs_url(origin, success_path)
    cancel_url   = _abs_url(origin, cancel_path)

    secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()

    # Sin clave → DEMO (redirige al OK)
    if not secret:
        current_app.logger.info("Stripe demo: %s", success_url)
        return jsonify(ok=True, demo=True, url=success_url)

    # Stripe real
    try:
        import stripe
        stripe.api_key = secret
        amount_cents = int(amount_eur * 100)
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": "Depósito de reserva SpainRoom"},
                    "unit_amount": amount_cents
                },
                "quantity": 1
            }],
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=data.get("customer_email") or None,
            metadata=(data.get("metadata") or {}),
        )
        return jsonify(ok=True, url=session.url)
    except Exception as e:
        current_app.logger.exception("Stripe error: %s", e)
        # Fallback demo
        return jsonify(ok=True, demo=True, url=success_url, error=str(e))
