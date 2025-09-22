# payments.py
import os, json
from urllib.parse import urljoin
from flask import Blueprint, request, jsonify, current_app, make_response

bp_payments = Blueprint("payments", __name__)

# Config/entorno
def _front_base():
    return (os.getenv("FRONTEND_BASE_URL") or "http://localhost:5176").rstrip("/")

def _success_url(path):
    if not path: path = "/reservas/ok"
    return path if path.startswith("http") else urljoin(_front_base()+"/", path.lstrip("/"))

def _cancel_url(path):
    if not path: path = "/reservas/error"
    return path if path.startswith("http") else urljoin(_front_base()+"/", path.lstrip("/"))

def _stripe():
    """Devuelve instancia stripe o None si no está disponible."""
    key = os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY")
    if not key:
        return None, None
    try:
        import stripe  # type: ignore
        stripe.api_key = key
        return stripe, key
    except Exception as e:
        current_app.logger.warning(f"[PAY] stripe import fail: {e}")
        return None, None

@bp_payments.post("/create-checkout-session")
def create_checkout_session():
    """
    Cuerpo esperado (JSON):
    {
      "amount": 50,            # EUR unidades (no céntimos)
      "currency": "eur",
      "customer_email": "x@y.com",
      "success_path": "/reservas/ok",
      "cancel_path": "/reservas/error",
      "metadata": { ... }      # opcional
    }
    """
    data = request.get_json(silent=True) or {}
    try:
        amount = int(data.get("amount") or 0)
    except Exception:
        amount = 0
    currency = (data.get("currency") or "eur").lower()
    email    = (data.get("customer_email") or "").strip() or None
    success  = _success_url(data.get("success_path"))
    cancel   = _cancel_url(data.get("cancel_path"))
    metadata = data.get("metadata") or {}

    if amount <= 0:
        return jsonify(ok=False, error="bad_amount"), 400

    stripe, key = _stripe()
    if not stripe:
        # Fallback demo: no Stripe configurado
        demo_url = success  # simulamos éxito
        current_app.logger.info(f"[PAY] DEMO session amount={amount} -> {demo_url}")
        return jsonify(ok=True, url=demo_url, demo=True)

    try:
        # Creamos una sesión rápida (un único item con price_data inlined)
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "quantity": 1,
                "price_data": {
                    "currency": currency,
                    "unit_amount": amount * 100,  # céntimos
                    "product_data": { "name": "Depósito SpainRoom" },
                }
            }],
            success_url=success + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=cancel,
            customer_email=email,
            metadata=metadata
        )
        return jsonify(ok=True, url=session.url)
    except Exception as e:
        current_app.logger.exception("[PAY] create session error")
        # Fallback demo si falla
        demo_url = cancel
        return jsonify(ok=False, error="stripe_error", demo_url=demo_url), 502

# (Opcional) Webhook de Stripe
@bp_payments.post("/webhook")
def stripe_webhook():
    """
    (Opcional) Maneja eventos de Stripe si configuras STRIPE_WEBHOOK_SECRET.
    """
    stripe, key = _stripe()
    if not stripe:
        return make_response("stripe disabled", 200)

    sig = request.headers.get("Stripe-Signature", "")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    payload = request.get_data(as_text=True)

    if not secret:
        # Si no hay secret, no verificamos — solo log
        current_app.logger.info("[PAY] webhook (no secret): %s", payload)
        return make_response("", 200)

    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except Exception as e:
        current_app.logger.warning(f"[PAY] webhook signature error: {e}")
        return make_response("bad signature", 400)

    # Manejo básico
    t = event.get("type")
    data = event.get("data", {}).get("object", {})
    current_app.logger.info(f"[PAY] webhook {t} id={data.get('id')}")

    # Aquí puedes actualizar estados en BD si lo necesitas
    return make_response("", 200)
