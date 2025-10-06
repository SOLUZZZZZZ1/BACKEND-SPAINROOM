# routes_payments.py — DEMO + Stripe real (si hay clave)
import os
from flask import Blueprint, request, jsonify

bp_pay = Blueprint("payments", __name__)

@bp_pay.post("/create-checkout-session")
def create_checkout_session():
    """
    Espera un JSON como:
    {
      "amount": 150,                      # euros
      "currency": "eur",
      "customer_email": "opcional",
      "success_path": "/habitaciones/DEMO?reserva=ok",
      "cancel_path":  "/habitaciones/DEMO?reserva=error",
      "metadata": { "room_code": "...", "startDate":"...", "endDate":"...", "telefono":"..." }
    }
    """
    data = request.get_json(silent=True) or {}
    amount_eur    = int(data.get("amount") or 150)  # default 150 €
    currency      = (data.get("currency") or "eur").lower()
    success_path  = data.get("success_path") or "/?reserva=ok"
    cancel_path   = data.get("cancel_path")  or "/?reserva=error"

    # Si NO hay clave Stripe, devolvemos DEMO (redirige a success directamente)
    stripe_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    base_front = request.headers.get("Origin") or ""  # p.ej. http://localhost:5176
    if not stripe_key:
        demo_url = f"{base_front}{success_path}"
        return jsonify(ok=True, demo=True, url=demo_url)

    # ---- Stripe real (si hay clave) ----
    try:
        import stripe
        stripe.api_key = stripe_key
        # Stripe usa menores unidades (céntimos)
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
            success_url=f"{base_front}{success_path}",
            cancel_url=f"{base_front}{cancel_path}",
            customer_email=data.get("customer_email") or None,
            metadata=(data.get("metadata") or {}),
        )
        return jsonify(ok=True, url=session.url)
    except Exception as e:
        # fallback a demo para no bloquear el flujo
        return jsonify(ok=True, demo=True, url=f"{base_front}{success_path}", error=str(e))
