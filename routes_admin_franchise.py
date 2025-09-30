# routes_admin_franchise.py — Admin: ingest padrón, listar plazas, ocupar/liberar, export Excel
import io, math, os
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from sqlalchemy import func
from extensions import db
from models_franchise_slots import FranchiseSlot

bp_admin_franq = Blueprint("admin_franq", __name__)

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "ramon")

def _auth():
    return (request.headers.get("X-Admin-Key") or "") == ADMIN_API_KEY

def _slots_rule(municipio: str, provincia: str, poblacion: int) -> int:
    if poblacion is None or poblacion <= 0:
        return 0
    m = (municipio or "").strip().lower()
    p = (provincia or "").strip().lower()
    if (m == "madrid" and p == "madrid") or (m == "barcelona" and p == "barcelona"):
        return max(1, math.ceil(poblacion / 20000))
    return max(1, math.ceil(poblacion / 10000))

@bp_admin_franq.post("/api/admin/franquicia/ingest")
def ingest_csv():
    """Sube CSV con columnas: provincia, municipio, poblacion."""
    if not _auth(): return jsonify(ok=False, error="forbidden"), 403
    f = request.files.get("file")
    if not f: return jsonify(ok=False, error="missing_file"), 400
    try:
        df = pd.read_csv(f)
    except Exception:
        f.seek(0)
        df = pd.read_excel(f)

    # Normaliza columnas
    cols = {c.lower(): c for c in df.columns}
    col_p = cols.get("provincia") or [c for c in cols if "prov" in c][0]
    col_m = cols.get("municipio") or [c for c in cols if "muni" in c][0]
    col_h = cols.get("poblacion") or [c for c in cols if "habit" in c or "pob" in c][0]

    df = df[[col_p, col_m, col_h]].copy()
    df.columns = ["provincia", "municipio", "poblacion"]
    df["provincia"] = df["provincia"].astype(str).str.strip()
    df["municipio"] = df["municipio"].astype(str).str.strip()
    df["poblacion"] = pd.to_numeric(df["poblacion"], errors="coerce").fillna(0).astype(int)
    df = df[df["poblacion"] > 0]

    # Calcula plazas por municipio
    df["plazas"] = df.apply(lambda r: _slots_rule(r["municipio"], r["provincia"], r["poblacion"]), axis=1)
    df["ocupadas"] = 0
    df["libres"] = df["plazas"]
    df["assigned_to"] = None
    df["status"] = df["plazas"].apply(lambda x: "free" if x > 0 else "full")

    # Upsert simple (por provincia+municipio)
    inserted = 0
    for _, r in df.iterrows():
        row = (db.session.query(FranchiseSlot)
               .filter(FranchiseSlot.provincia.ilike(r["provincia"]),
                       FranchiseSlot.municipio.ilike(r["municipio"]))
               .first())
        if not row:
            row = FranchiseSlot(
                provincia=r["provincia"], municipio=r["municipio"],
                poblacion=r["poblacion"], plazas=r["plazas"],
                ocupadas=0, libres=r["plazas"], assigned_to=None, status="free" if r["plazas"]>0 else "full"
            )
            db.session.add(row)
            inserted += 1
        else:
            # actualiza población y plazas recalculadas si cambió
            row.poblacion = r["poblacion"]
            row.plazas = r["plazas"]
            # recalcula libres si plazas cambian (no tocar ocupadas si ya las has gestionado)
            row.libres = max(0, row.plazas - row.ocupadas)
            row.status = "full" if row.libres == 0 else ("free" if row.ocupadas == 0 else "partial")
    db.session.commit()
    return jsonify(ok=True, inserted=inserted, total=int(db.session.query(FranchiseSlot).count()))

@bp_admin_franq.get("/api/admin/franquicia/summary")
def summary():
    if not _auth(): return jsonify(ok=False, error="forbidden"), 403
    q = db.session.query(
        func.count(FranchiseSlot.id),
        func.sum(FranchiseSlot.poblacion),
        func.sum(FranchiseSlot.plazas),
        func.sum(FranchiseSlot.ocupadas),
        func.sum(FranchiseSlot.libres),
    ).one()
    total_munis = int(q[0] or 0)
    return jsonify(ok=True, total_municipios=total_munis,
                   habitantes=int(q[1] or 0),
                   plazas=int(q[2] or 0),
                   ocupadas=int(q[3] or 0),
                   libres=int(q[4] or 0))

@bp_admin_franq.get("/api/admin/franquicia/slots")
def list_slots():
    if not _auth(): return jsonify(ok=False, error="forbidden"), 403
    prov = request.args.get("provincia")
    muni = request.args.get("municipio")
    status = request.args.get("status")  # free|partial|full
    assigned = request.args.get("assigned_to")
    q = db.session.query(FranchiseSlot)
    if prov: q = q.filter(FranchiseSlot.provincia.ilike(f"%{prov}%"))
    if muni: q = q.filter(FranchiseSlot.municipio.ilike(f"%{muni}%"))
    if status in ("free","partial","full"): q = q.filter(FranchiseSlot.status==status)
    if assigned: q = q.filter(FranchiseSlot.assigned_to==assigned)
    rows = [r.to_dict() for r in q.order_by(FranchiseSlot.provincia, FranchiseSlot.municipio).limit(5000).all()]
    return jsonify(ok=True, count=len(rows), results=rows)

@bp_admin_franq.post("/api/admin/franquicia/slots/ocupar")
def ocupar():
    if not _auth(): return jsonify(ok=False, error="forbidden"), 403
    data = request.get_json(force=True) or {}
    slot_id = data.get("id")
    assigned_to = (data.get("assigned_to") or "").strip() or None
    inc = int(data.get("inc", 1))  # cuántas plazas ocupar
    row = db.session.get(FranchiseSlot, slot_id)
    if not row: return jsonify(ok=False, error="not_found"), 404
    row.ocupadas = min(row.plazas, row.ocupadas + inc)
    row.libres = max(0, row.plazas - row.ocupadas)
    if assigned_to: row.assigned_to = assigned_to
    row.status = "full" if row.libres == 0 else ("free" if row.ocupadas == 0 else "partial")
    db.session.commit()
    return jsonify(ok=True, slot=row.to_dict())

@bp_admin_franq.post("/api/admin/franquicia/slots/liberar")
def liberar():
    if not _auth(): return jsonify(ok=False, error="forbidden"), 403
    data = request.get_json(force=True) or {}
    slot_id = data.get("id")
    dec = int(data.get("dec", 1))
    row = db.session.get(FranchiseSlot, slot_id)
    if not row: return jsonify(ok=False, error="not_found"), 404
    row.ocupadas = max(0, row.ocupadas - dec)
    row.libres = max(0, row.plazas - row.ocupadas)
    row.status = "full" if row.libres == 0 else ("free" if row.ocupadas == 0 else "partial")
    db.session.commit()
    return jsonify(ok=True, slot=row.to_dict())

@bp_admin_franq.get("/api/admin/franquicia/export.xlsx")
def export_xlsx():
    if not _auth(): return jsonify(ok=False, error="forbidden"), 403
    # Municipios
    rows = db.session.query(FranchiseSlot).order_by(FranchiseSlot.provincia, FranchiseSlot.municipio).all()
    if not rows:
        return jsonify(ok=False, error="no_data"), 400
    df = pd.DataFrame([r.to_dict() for r in rows])

    # Provincias
    g = df.groupby("provincia", as_index=False).agg({
        "poblacion":"sum", "plazas":"sum", "ocupadas":"sum", "libres":"sum"
    }).sort_values("provincia")

    # Totales nacionales (incluye islas y ciudades autónomas)
    tot = {
        "habitantes": int(df["poblacion"].sum()),
        "plazas": int(df["plazas"].sum()),
        "ocupadas": int(df["ocupadas"].sum()),
        "libres": int(df["libres"].sum()),
        "municipios": int(len(df))
    }

    # Excel en memoria
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="xlsxwriter") as xw:
        df.to_excel(xw, index=False, sheet_name="Municipios")
        g.to_excel(xw, index=False, sheet_name="Provincias")
        pd.DataFrame([tot]).to_excel(xw, index=False, sheet_name="Totales")
    bio.seek(0)
    return send_file(bio, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name="plazas_franquicia.xlsx")
