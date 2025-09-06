# payments.py
from flask import Blueprint, jsonify, request
import os

try:
    import stripe
except Exception:
    stripe = None

bp_payments = Blueprint("payments", __name__, url_prefix="/api/payments")

def stripe_is_configured():
    return bool(os.getenv("STRIPE_SECRET_KEY")) and stripe is not None

@bp_payments.get("/health")
def payments_health():
    return jsonify({"ok": True, "stripe_enabled": stripe_is_configured()})

@bp_payments.post("/checkout/session")
def create_checkout_session():
    if not stripe_is_configured():
        return jsonify({"ok": False, "error": "stripe_not_configured"}), 503

    try:
        stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

        amount_eur = request.json.get("amount_eur")
        try:
            amount_cents = int(round(float(amount_eur) * 100))
        except Exception:
            return jsonify({"ok": False, "error": "invalid_amount"}), 400
        if amount_cents <= 0:
            return jsonify({"ok": False, "error": "invalid_amount"}), 400

        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "eur",
                    "product_data": {"name": "Pago SpainRoom"},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            success_url=request.json.get("success_url", "https://spainroom.vercel.app/pago-ok"),
            cancel_url=request.json.get("cancel_url", "https://spainroom.vercel.app/pago-cancel"),
        )
        return jsonify({"ok": True, "url": session.url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp_pay.post("/create-checkout-session")
def create_checkout_session():
    """
    Crea una sesión de Stripe Checkout.
    Espera JSON (ejemplo):
    {
      "amount_eur": 50,              # entero en EUR
      "concept": "Depósito SpainRoom",
      "quantity": 1                   # opcional (por defecto 1)
    }
    Alternativamente podrías enviar "price_id".
    """
    data = request.get_json(silent=True) or {}
    amount_eur = int(data.get("amount_eur", 0))
    concept = (data.get("concept") or "SpainRoom Pago").strip()
    quantity = int(data.get("quantity", 1) or 1)
    price_id = (data.get("price_id") or "").strip()

    if price_id:
        try:
            session = stripe.checkout.Session.create(
                mode="payment",
                line_items=[{"price": price_id, "quantity": quantity}],
                success_url=SUCCESS_URL,
                cancel_url=CANCEL_URL,
            )
            return jsonify({"id": session.id})
        except stripe.error.StripeError as e:
            return jsonify({"error": str(e)}), 400

    if amount_eur <= 0:
        return jsonify({"error": "amount_eur inválido"}), 400

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": CURRENCY,
                        "product_data": {"name": concept},
                        "unit_amount": amount_eur * 100,  # céntimos
                    },
                    "quantity": quantity,
                }
            ],
            success_url=SUCCESS_URL,
            cancel_url=CANCEL_URL,
        )
        return jsonify({"id": session.id})
    except stripe.error.StripeError as e:
        return jsonify({"error": str(e)}), 400

