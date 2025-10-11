# routes_auto_check.py — Auto-check de cédula: address -> refcat -> registro en BD (no bloquea UI)
# Nora · 2025-10-11
import os, uuid, threading, time, json, requests
from datetime import datetime
from flask import Blueprint, request, jsonify
from sqlalchemy import text
from extensions import db

bp_autocheck = Blueprint("auto_check", __name__)

ADMIN_KEY = (os.getenv("ADMIN_API_KEY") or os.getenv("ADMIN_KEY") or "").strip()
_TASKS = {}

def _authorized():
    if not ADMIN_KEY:
        return True
    return request.headers.get("X-Admin-Key") == ADMIN_KEY

def _api_base(req):
    return req.host_url.rstrip("/")

def _db_upsert(task_id: str, status: str, payload: dict):
    sql = text("""
        INSERT INTO property_certificates_tasks (id, status, data, updated_at)
        VALUES (:id, :status, :data, :ts)
        ON CONFLICT (id) DO UPDATE SET status=:status, data=:data, updated_at=:ts
    """)
    db.session.execute(sql, {"id": task_id, "status": status, "data": json.dumps(payload or {}), "ts": datetime.utcnow()})
    db.session.commit()

def _worker_run(task_id: str, payload: dict, base: str):
    try:
        direccion = payload.get("direccion","").strip()
        municipio = payload.get("municipio","").strip()
        provincia = payload.get("provincia","").strip()
        cp        = payload.get("cp","").strip()

        # 1) resolver refcat
        refcat = None
        try:
            r = requests.post(f"{base}/api/catastro/resolve_direccion", json={
                "direccion": direccion, "municipio": municipio, "provincia": provincia, "cp": cp
            }, timeout=8)
            j = r.json()
            refcat = j.get("refcat")
        except Exception:
            pass

        # 2) consultar datos por refcat
        catastro_info = None
        if refcat and len(refcat) == 20:
            try:
                r2 = requests.post(f"{base}/api/catastro/consulta_refcat", json={"refcat": refcat}, timeout=8)
                j2 = r2.json(); catastro_info = j2 if j2.get("ok") else None
            except Exception:
                pass

        result = {"refcat": refcat, "catastro": catastro_info, "status": "found" if refcat else "not_found"}
        _db_upsert(task_id, "done", result)
    except Exception as e:
        _db_upsert(task_id, "error", {"error": str(e)})
    finally:
        _TASKS.pop(task_id, None)

@bp_autocheck.route("/api/owner/auto_check", methods=["POST","OPTIONS"])
def auto_check():
    if request.method == "OPTIONS":
        return ("",204)
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401

    body = request.get_json(silent=True) or {}
    required = ["direccion","municipio","provincia"]
    if not all((body.get(k) or "").strip() for k in required):
        return jsonify(ok=False, error="bad_request", message="Faltan direccion/municipio/provincia"), 400

    task_id = "AC-" + uuid.uuid4().hex[:10]
    _db_upsert(task_id, "queued", {"input": body})
    base = _api_base(request)
    t = threading.Thread(target=_worker_run, args=(task_id, body, base), daemon=True)
    _TASKS[task_id] = {"t": t, "ts": time.time()}
    t.start()
    return jsonify(ok=True, task_id=task_id)

@bp_autocheck.route("/api/owner/auto_check/<task_id>", methods=["GET"])
def auto_check_status(task_id):
    row = db.session.execute(text("SELECT status, data, updated_at FROM property_certificates_tasks WHERE id=:id"), {"id": task_id}).first()
    if not row:
        return jsonify(ok=False, error="not_found"), 404
    status, data, ts = row
    try:
        payload = json.loads(data or "{}")
    except Exception:
        payload = {}
    return jsonify(ok=True, status=status, data=payload, updated_at=(ts.isoformat() if ts else None))
