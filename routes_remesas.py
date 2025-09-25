
# routes_remesas.py
import os, hmac, hashlib, uuid
from typing import Optional
from flask import Blueprint, request, jsonify, current_app
from extensions import db
from models_remesas import Remesa

bp_remesas = Blueprint("remesas", __name__)

# --- Helpers -----------------------------------------------------------------
def _env(k: str, default: str = "") -> str:
    return os.getenv(k, default)

def _hmac_sha256(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

def _get_user_id() -> Optional[int]:
    # Simplificación: recibimos el usuario desde cabecera X-User-Id
    # (si usas JWT, sustituye por decode y extrae uid)
    uid = request.headers.get("X-User-Id", "").strip()
    try:
        return int(uid) if uid else None
    except Exception:
        return None

# --- Endpoints ----------------------------------------------------------------

@bp_remesas.get("/api/remesas/quote")
def quote_demo():
    """Cotización DEMO (no llama a RIA). Calcula un tipo de cambio ficticio estable."""
    try:
        amount = float(request.args.get("amount", "0"))
    except Exception:
        amount = 0.0
    currency_from = (request.args.get("currency_from") or "EUR").upper()
    currency_to   = (request.args.get("currency_to") or "EUR").upper()

    if amount <= 0:
        return jsonify(ok=False, error="bad_amount"), 400

    # Tipos demo (sencillos) — en producción vienen del partner
    demo_rates = {
        ("EUR", "COP"): 4300.0,
        ("EUR", "PEN"): 4.10,
        ("EUR", "USD"): 1.05,
        ("EUR", "MAD"): 10.9,   # Dirham marroquí
        ("EUR", "RON"): 4.97,
    }
    rate = demo_rates.get((currency_from, currency_to), 1.0)
    payout = round(amount * rate, 2)
    fee = round(max(0.99, amount * 0.01), 2)  # demo fee
    return jsonify(ok=True, amount=amount, currency_from=currency_from, currency_to=currency_to, rate=rate, payout=payout, fee=fee)

@bp_remesas.post("/api/remesas/start")
def start_remesa():
    """Registra una remesa y devuelve URL firmada del widget Hosted (RIA).
    Body JSON: { amount, currency_from?, currency_to, country_dest, receiver_name? }
    Cabeceras: X-User-Id: <int>
    """
    uid = _get_user_id()
    if not uid:
        return jsonify(ok=False, error="missing_user"), 401

    data = request.get_json(force=True) or {}
    try:
        amount = float(data.get("amount", 0))
    except Exception:
        amount = 0.0
    currency_from = (data.get("currency_from") or "EUR").upper()
    currency_to   = (data.get("currency_to") or "EUR").upper()
    country_dest  = (data.get("country_dest") or "").upper()
    receiver_name = (data.get("receiver_name") or "").strip() or None
    meta          = data.get("meta_json") if isinstance(data.get("meta_json"), dict) else None

    if amount <= 0 or not currency_to or not country_dest:
        return jsonify(ok=False, error="missing_fields"), 400

    # Creamos registro SpainRoom (estado created)
    req_id = uuid.uuid4().hex
    r = Remesa(
        user_id=uid, request_id=req_id, status="created",
        amount=amount, currency_from=currency_from, currency_to=currency_to,
        country_dest=country_dest, receiver_name=receiver_name, meta_json=meta
    )
    db.session.add(r); db.session.commit()

    # Construcción de URL firmada para Hosted/Widget del partner
    base = _env("RIA_WIDGET_BASE", "https://sandbox.ria.example/widget")
    partner_id = _env("RIA_PARTNER_ID", "spainroom")
    secret = _env("RIA_PARTNER_SECRET", "")
    # Parámetros mínimos recomendados
    params = f"partner_id={partner_id}&request_id={req_id}&amount={amount:.2f}&from={currency_from}&to={currency_to}&country={country_dest}"
    sig = _hmac_sha256(secret, params.encode("utf-8")) if secret else "demo"
    widget_url = f"{base}?{params}&sig={sig}"

    current_app.logger.info("[Remesas] start user=%s req=%s %s", uid, req_id, widget_url)

    return jsonify(ok=True, id=r.id, request_id=req_id, url=widget_url)

@bp_remesas.post("/api/remesas/webhook")
def webhook_ria():
    """Webhook de RIA → actualiza estado por request_id.
    Cabecera esperada: X-RIA-Signature: <hex hmac256>
    Body JSON: { request_id, status } con status in {created,pending,completed,failed}
    """
    secret = _env("RIA_WEBHOOK_SECRET", "")
    raw = request.get_data(cache=False, as_text=False)  # firma sobre el raw payload
    given_sig = request.headers.get("X-RIA-Signature", "")

    if secret:
        try:
            expected = _hmac_sha256(secret, raw)
            if not hmac.compare_digest(expected, given_sig):
                return jsonify(ok=False, error="bad_signature"), 400
        except Exception:
            return jsonify(ok=False, error="bad_signature"), 400

    payload = request.get_json(silent=True) or {}
    req_id = (payload.get("request_id") or "").strip()
    new_status = (payload.get("status") or "").lower()

    if not req_id or new_status not in {"created","pending","completed","failed"}:
        return jsonify(ok=False, error="bad_payload"), 400

    r = Remesa.query.filter_by(request_id=req_id).first()
    if not r:
        # Idempotencia: si no existe aún, creamos como pending con metadatos mínimos
        r = Remesa(user_id=0, request_id=req_id, status=new_status, amount=0, currency_from="EUR", currency_to="EUR", country_dest=None)
        db.session.add(r)
    else:
        r.status = new_status
    db.session.commit()

    current_app.logger.info("[Remesas] webhook %s -> %s", req_id, new_status)
    return jsonify(ok=True)

@bp_remesas.get("/api/remesas/mias")
def listar_mias():
    uid = _get_user_id()
    if not uid:
        return jsonify(ok=False, error="missing_user"), 401

    status = (request.args.get("status") or "").lower()
    q = Remesa.query.filter_by(user_id=uid)
    if status in {"created","pending","completed","failed"}:
        q = q.filter(Remesa.status == status)
    q = q.order_by(Remesa.id.desc()).limit(200)
    return jsonify([r.to_dict() for r in q.all()])
