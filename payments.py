# payments.py
import os
from flask import Blueprint, request, jsonify
import stripe

bp_pay = Blueprint("payments", __name__, url_prefix="/api/payments")

# Clave secreta (SOLO en el BACKEND: Render u host Flask)
# Configura: STRIPE_SECRET_KEY = sk_test_... / sk_live_...
stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

SUCCESS_URL = os.environ.get(
    "STRIPE_SUCCESS_URL",
    "https://spainroom.vercel.app/pago-exito?session_id={CHECKOUT_SESSION_ID}",
)
CANCEL_URL = os.environ.get(
    "STRIPE_CANCEL_URL",
    "https://spainroom.vercel.app/pago-cancelado",
)
CURRENCY = os.environ.get("STRIPE_CURRENCY", "eur")


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
