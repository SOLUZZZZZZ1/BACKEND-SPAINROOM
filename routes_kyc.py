# routes_kyc.py — DEMO estable
import os, secrets
from flask import Blueprint, request, jsonify, make_response

bp_kyc = Blueprint("kyc", __name__)

def _public_base(req):
    return (os.getenv("PUBLIC_BASE_URL") or req.host_url.rstrip("/"))

@bp_kyc.route("/api/kyc/start", methods=["POST","GET","OPTIONS"])
def kyc_start():
    if request.method == "OPTIONS":
        resp = make_response("", 200)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "POST,GET,OPTIONS"
        return resp

    data  = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    if request.method == "POST" and not phone:
        return jsonify(ok=False, error="missing_phone"), 400

    token = secrets.token_urlsafe(20)
    link  = f"{_public_base(request)}/kyc/selfie/{token}"
    return jsonify(ok=True, session_id=0, token=token, link=link, demo=True), 200

@bp_kyc.route("/api/kyc/status", methods=["GET"])
def kyc_status():
    return jsonify(ok=True, state="pending", decision="", reason=""), 200

@bp_kyc.route("/kyc/selfie/<token>", methods=["GET"])
def kyc_selfie_stub(token):
    html = f"""<!doctype html><meta charset="utf-8"><title>Selfie (demo)</title>
<body style="margin:0;background:#0b1320;color:#fff;font-family:system-ui,Segoe UI,Roboto,Arial">
  <div style="max-width:520px;margin:0 auto;padding:24px;text-align:center">
    <img src="/cabecera.png" alt="SpainRoom" style="height:80px;display:block;margin:0 auto 10px"/>
    <h2 style="margin:0 0 8px">Selfie (demo)</h2>
    <p style="opacity:.9">El enlace funciona. Al activar Veriff, abrirá su flujo oficial.</p>
    <p style="opacity:.7">token: {token}</p>
  </div>
</body>"""
    return make_response(html, 200)
