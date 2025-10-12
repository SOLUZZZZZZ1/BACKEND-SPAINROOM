# routes_veriff.py — Integración Veriff (demo + real con VERIFF_API_KEY)
# Nora · 2025-10-12
import os, hmac, hashlib, secrets
from flask import Blueprint, request, jsonify, make_response, current_app
import requests

bp_veriff = Blueprint("veriff", __name__)

VERIFF_API_KEY = (os.getenv("VERIFF_API_KEY") or "").strip()
VERIFF_WEBHOOK_SECRET = (os.getenv("VERIFF_WEBHOOK_SECRET") or "").strip()
VERIFF_BASE = (os.getenv("VERIFF_BASE_URL") or "https://stationapi.veriff.com/v1").rstrip("/")

def _corsify(resp):
    origin = request.headers.get("Origin", "*")
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Vary"] = "Origin"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Key"
    return resp

@bp_veriff.route("/api/kyc/veriff/session", methods=["POST","OPTIONS"])
def veriff_session():
    if request.method == "OPTIONS":
        return _corsify(make_response("", 204))
    data = request.get_json(silent=True) or {}

    # DEMO: si no hay API key, devolvemos un enlace interno que ya tienes
    if not VERIFF_API_KEY:
        token = secrets.token_urlsafe(16)
        link  = f"{(os.getenv('PUBLIC_BASE_URL') or request.host_url.rstrip('/'))}/kyc/selfie/{token}"
        return _corsify(jsonify(ok=True, demo=True, session_id=0, token=token, url=link))

    # REAL
    try:
        payload = {"verification": {
            "callback": (os.getenv("VERIFF_WEBHOOK_URL") or f"{request.host_url.rstrip('/')}/api/kyc/veriff/webhook"),
            "person": data.get("person") or {"firstName":"SpainRoom","lastName":"User"},
            "vendorData": data.get("vendorData") or "spainroom"
        }}
        headers = {"Content-Type":"application/json","X-AUTH-CLIENT": VERIFF_API_KEY}
        r = requests.post(f"{VERIFF_BASE}/verifications", headers=headers, json=payload, timeout=12)
        r.raise_for_status()
        j = r.json().get("verification", {})
        return _corsify(jsonify(ok=True, demo=False, session_id=j.get("id"), url=j.get("url"), vendorData=j.get("vendorData")))
    except Exception as e:
        try: current_app.logger.warning("[VERIFF] session error: %s", e)
        except Exception: pass
        token = secrets.token_urlsafe(16)
        link  = f"{(os.getenv('PUBLIC_BASE_URL') or request.host_url.rstrip('/'))}/kyc/selfie/{token}"
        return _corsify(jsonify(ok=True, demo=True, session_id=0, token=token, url=link))

def _verify_signature(raw_body: bytes, signature: str) -> bool:
    if not (VERIFF_WEBHOOK_SECRET and signature): return False
    try:
        mac = hmac.new(VERIFF_WEBHOOK_SECRET.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha256).hexdigest()
        return hmac.compare_digest(mac, signature.split("sha256=")[-1])
    except Exception:
        return False

@bp_veriff.route("/api/kyc/veriff/webhook", methods=["POST"])
def veriff_webhook():
    sig = request.headers.get("X-Hub-Signature","")
    raw = request.data or b""
    if VERIFF_WEBHOOK_SECRET and not _verify_signature(raw, sig):
        return jsonify(ok=False, error="invalid_signature"), 400
    try:
        event = request.get_json(force=True) or {}
        verification = event.get("verification") or {}
        status = (verification.get("status") or "").lower()
        decision = "approved" if status in ("approved","resubmission_requested") else "declined" if status=="declined" else "pending"
        try: current_app.logger.info("[VERIFF] webhook status=%s id=%s", status, verification.get("id"))
        except Exception: pass
        return jsonify(ok=True, status=status, decision=decision)
    except Exception:
        return jsonify(ok=False), 200
