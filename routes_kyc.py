# routes_kyc.py
from flask import Blueprint, request, jsonify
bp_kyc = Blueprint("kyc", __name__)

@bp_kyc.post("/api/kyc/verify_selfie")
def kyc_verify_selfie():
    # TODO: validar con proveedor: comparar cara del selfie con documento
    # Por ahora, demo "siempre ok":
    return jsonify(ok=True, match=True, _demo=True)
