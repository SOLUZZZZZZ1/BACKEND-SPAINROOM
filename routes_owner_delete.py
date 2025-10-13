# routes_owner_delete.py — Delete S3 objects (solo admin)
import os, boto3
from flask import Blueprint, request, jsonify
from botocore.exceptions import ClientError

bp_owner_delete = Blueprint("owner_delete", __name__)

ADMIN_KEY = (os.getenv("ADMIN_API_KEY") or os.getenv("ADMIN_KEY") or "").strip()

def _authorized() -> bool:
    if not ADMIN_KEY:
        return True
    return request.headers.get("X-Admin-Key") == ADMIN_KEY

def _s3():
    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )

@bp_owner_delete.route("/api/owner/cedula/delete", methods=["DELETE","OPTIONS"])
def delete():
    if request.method == "OPTIONS": return ("",204)
    if not _authorized(): return jsonify(ok=False, error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    s3_key = (data.get("s3_key") or "").strip()
    bucket = (os.getenv("S3_BUCKET") or "").strip()
    if not s3_key: return jsonify(ok=False, error="missing_s3_key"), 400
    try:
        _s3().delete_object(Bucket=bucket, Key=s3_key)
        return jsonify(ok=True, deleted=s3_key)
    except ClientError as e:
        return jsonify(ok=False, error="delete_failed", detail=str(e)), 500
