# routes_owner_cedula.py — Owner endpoints (check + upload, S3 integrado)
# Nora · 2025-10-13
import os, uuid, boto3
from pathlib import Path
from flask import Blueprint, request, jsonify, current_app
from botocore.exceptions import ClientError

bp_owner = Blueprint("owner", __name__)

# === Seguridad ===
ADMIN_KEY = (os.getenv("ADMIN_API_KEY") or os.getenv("ADMIN_KEY") or "").strip()

def _authorized() -> bool:
    """Comprueba cabecera X-Admin-Key."""
    if not ADMIN_KEY:
        return True  # modo dev si no hay clave
    return request.headers.get("X-Admin-Key") == ADMIN_KEY

# === Cliente S3 ===
def _s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )

def _upload_to_s3(fileobj, bucket, key, content_type):
    """Sube el archivo a S3 y devuelve dict con info o error."""
    s3 = _s3_client()
    try:
        s3.upload_fileobj(
            Fileobj=fileobj,
            Bucket=bucket,
            Key=key,
            ExtraArgs={"ContentType": content_type, "ACL": "private"},
        )
        return {"ok": True, "s3_key": key}
    except ClientError as e:
        return {"ok": False, "error": str(e)}

# === Endpoints ===

@bp_owner.route("/check", methods=["POST", "OPTIONS"])
def check():
    if request.method == "OPTIONS":
        return ("", 204)
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    return jsonify(ok=True, id="SRV-CHK-" + uuid.uuid4().hex[:8])

@bp_owner.route("/cedula/upload", methods=["POST", "OPTIONS"])
def upload():
    if request.method == "OPTIONS":
        return ("", 204)
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401

    f = request.files.get("file")
    if not f:
        return jsonify(ok=False, error="no_file"), 400

    # si hay variables AWS → sube a S3
    bucket = os.getenv("S3_BUCKET")
    region = os.getenv("AWS_REGION", "us-east-1")
    prefix = os.getenv("S3_PREFIX", "cedulas/")

    if bucket and os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"):
        key = f"{prefix}{uuid.uuid4().hex[:8]}-{f.filename}"
        res = _upload_to_s3(f.stream, bucket, key, f.mimetype or "application/octet-stream")
        if res.get("ok"):
            # generar URL presignada
            s3 = _s3_client()
            url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=int(os.getenv("S3_PRESIGN_EXP", "3600")),
            )
            return jsonify(
                ok=True,
                storage="s3",
                bucket=bucket,
                region=region,
                s3_key=key,
                presigned_url=url,
            )
        else:
            current_app.logger.warning("S3 upload failed: %s", res.get("error"))
            # fallback local
    # === Fallback local ===
    up = Path(current_app.instance_path) / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    tgt = up / f.filename
    f.save(tgt)
    return jsonify(ok=True, storage="local", filename=f.filename)
