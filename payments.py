# payments.py â€” SpainRoom BACKEND
# Minimal blueprint para pagos con ruta /api/payments/*
# Seguro ante ausencia de Stripe o de STRIPE_SECRET_KEY

import os
from flask import Blueprint, jsonify, request

try:
    import stripe  # pip install stripe
except Exception:
    stripe = None

bp_payments = Blueprint("payments", __name__, url_prefix="/api/payments")


@bp_payments.get("/health")
def payments_health():
    return jsonify({"ok": True, "module": "payments"})


def _setup_stripe():
    """Inicializa Stripe si hay SDK y clave; devuelve (stripe, err)."""
    key = os.getenv("STRIPE_SECRET_KEY")
    if stripe is None:
        return None, "Stripe SDK not installed"
    if not key:
        return None, "STRIPE_SECRET_KEY missing"
    stripe.api_key = key
    return stripe, None


@bp_payments.post("/intent")
def create_payment_intent():
    """
    Crea un PaymentIntent.
    body JSON: { "amount_eur": 50.0, "currency": "eur", "metadata": {...} }
    """
    s, err = _setup_stripe()
    if err:
        return jsonify({"ok": False, "error": err}), 503

    data = request.get_json(silent=True) or {}
    amount_eur = data.get("amount_eur")
    try:
        amount_cents = int(round(float(amount_eur) * 100))
    except Exception:
        amount_cents = 0

    if amount_cents <= 0:
        return jsonify({"ok": False, "error": "Invalid amount_eur"}), 400

    currency = (data.get("currency") or "eur").lower()
    metadata = data.get("metadata") or {}

    try:
        intent = s.PaymentIntent.create(
            amount=amount_cents,
            currency=currency,
            automatic_payment_methods={"enabled": True},
            metadata=metadata,
        )
        return jsonify({"ok": True, "client_secret": intent["client_secret"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
