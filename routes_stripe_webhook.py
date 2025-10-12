# routes_stripe_webhook.py
import os, json
from flask import Blueprint, request, jsonify, current_app

bp_stripe_wh = Blueprint("stripe_webhook", __name__)

@bp_stripe_wh.post("/stripe/webhook")
def stripe_webhook():
    payload = request.data
    sig     = request.headers.get("Stripe-Signature", "")
    secret  = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

    # Parse/verify
    try:
        import stripe
        stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        event = stripe.Webhook.construct_event(payload, sig, secret) if secret else json.loads(payload)
    except Exception as e:
        current_app.logger.exception("Webhook error: %s", e)
        return jsonify(ok=False), 400

    if event.get("type") == "checkout.session.completed":
        session = event["data"]["object"]
        meta    = session.get("metadata") or {}
        reserva_id = meta.get("reserva_id")  # si creas la reserva antes del checkout
        room_code  = meta.get("room_code")
        # TODO: actualizar DB: reservas.set_status(reserva_id, "approved") o
        # localizar por (room_code, fechas) y marcar como aprobada.
        current_app.logger.info("Reserva aprobada (room=%s, reserva=%s)", room_code, reserva_id)

    return jsonify(ok=True)
