# routes_owner_presign.py — Presign S3 (descarga segura con URL temporal)
# Nora · 2025-10-13
import os
import boto3
from botocore.exceptions import ClientError
from flask import Blueprint, request, jsonify

bp_owner_presign = Blueprint("owner_presign", __name__)

# Seguridad (usa la misma clave que el resto de endpoints owner)
ADMIN_KEY = (os.getenv("ADMIN_API_KEY") or os.getenv("ADMIN_KEY") or "").strip()

def _authorized() -> bool:
    if not ADMIN_KEY:
        return True  # modo dev si no hay clave
    return request.headers.get("X-Admin-Key") == ADMIN_KEY

def _s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )

@bp_owner_presign.route("/api/owner/cedula/presign", methods=["POST", "OPTIONS"])
def presign():
    """
    Body (JSON):
      {
        "s3_key": "cedulas/SRV-CHK-xxx/cedula.pdf",
        "expires": 900  # opcional, segundos (por defecto 3600)
      }
    Devuelve:
      { ok:true, presigned_url:"https://...", expires:900 }
    """
    if request.method == "OPTIONS":
        return ("", 204)

    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401

    data = request.get_json(silent=True) or {}
    s3_key = (data.get("s3_key") or "").strip()
    expires = int(data.get("expires") or os.getenv("S3_PRESIGN_EXP", "3600"))

    bucket = (os.getenv("S3_BUCKET") or "").strip()
    if not bucket:
        return jsonify(ok=False, error="s3_not_configured"), 400
    if not s3_key:
        return jsonify(ok=False, error="missing_s3_key"), 400
    if expires <= 0 or expires > 604800:  # máx 7 días
        expires = 3600

    s3 = _s3_client()

    # Comprobar que existe el objeto (head_object)
    try:
        s3.head_object(Bucket=bucket, Key=s3_key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return jsonify(ok=False, error="not_found", key=s3_key), 404
        # otro error
        return jsonify(ok=False, error="s3_head_error", detail=str(e)), 500

    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": s3_key},
            ExpiresIn=expires
        )
        return jsonify(ok=True, presigned_url=url, expires=expires)
    except ClientError as e:
        return jsonify(ok=False, error="presign_failed", detail=str(e)), 500
